"""JourneyOrchestrator — WebSocket 与 LangGraph 之间的核心桥梁。

职责：
- 管理每个 WS 连接对应的旅程图实例
- 桥接 LangGraph 状态机与 WebSocket 流式消息
- 处理 interrupt/resume 流（澄清/选方案/确认出发）
- 同步关键状态回 GCP ContextStore
- 行程进行中监听 GCP 事件，触发重规划/到达
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket

from .graph import compile_journey_graph, make_graph_config
from .skill_executor import SkillExecutor
from .state import init_agent_state
from .store import get_journey_store
from ..adapters.claude import get_claude
from ..adapters.volc_asr import AsrEventType, VolcAsrClient
from ..adapters.volc_tts import TtsEventType, VolcTtsClient
from ..core.config import settings
from ..core.logging import get_logger
from ..core.websocket import WsMessageType, ws_msg
from ..gcp import get_store
from ..utils.polyline import plain_to_polyline

logger = get_logger(__name__)


class JourneyOrchestrator:
    """每个 WebSocket 连接对应一个 Orchestrator 实例。

    管理一次完整的出行对话旅程。
    """

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.journey_id = f"j_{id(ws)}"

        # GCP
        self.gcp_store = get_store()

        # LangGraph
        self.store = get_journey_store()
        self.graph = compile_journey_graph()

        # 流式事件队列（节点 → WS）
        self.stream_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        # Skill 执行器
        self.skill_executor = SkillExecutor(self.gcp_store, self.stream_queue)

        # Claude
        self.claude = get_claude()

        # 后台任务
        self._stream_writer_task: Optional[asyncio.Task] = None
        self._gcp_monitor_task: Optional[asyncio.Task] = None
        self._gcp_monitor_running = False

        # 语音会话（ASR）
        self._asr_client: Optional[VolcAsrClient] = None
        self._asr_task: Optional[asyncio.Task] = None
        self._asr_request_id: str = ""
        self._voice_enabled: bool = bool(
            settings.volcengine_asr_api_key or settings.volcengine_api_key
        )
        self._tts_enabled: bool = bool(
            settings.volcengine_tts_api_key or settings.volcengine_api_key
        )
        self._tts_play_task: Optional[asyncio.Task] = None

        # 状态标记
        self._journey_started = False
        self._journey_ended = False  # 旅程是否已终态（completed/ended）
        self._state_snapshot: Dict[str, Any] = {}  # 跟踪 graph state 的最新快照
        self._request_id: str = ""

        # 图执行任务（后台运行，支持中断）
        self._graph_task: Optional[asyncio.Task] = None
        self._graph_cancelled: bool = False

    # ---- 公共 API（被 ws.py 调用） ----

    async def start_journey(self, user_query: str, request_id: str) -> None:
        """新用户输入 → 启动/恢复图执行，流式推送结果。

        - 首次调用：创建新旅程
        - 已有旅程且在终态（completed/ended）：重启新旅程
        - 已有旅程且在进行中：作为新的 user_query 注入并恢复
        """
        need_new_journey = False
        if not self._journey_started or self._journey_ended:
            need_new_journey = True

        # 取消当前正在执行的图任务（如果有）
        await self._cancel_graph_task()

        if need_new_journey:
            # 重置 GCP 中的旅程状态（避免旧数据干扰）
            try:
                await self.gcp_store.update({
                    "journey.status": "idle",
                    "journey.destination_name": "",
                    "journey.eta_remaining_min": 0,
                    "journey.route.distance_km": 0,
                    "journey.route.duration_min": 0,
                })
            except Exception:
                pass
            # 重置 demo 进度
            self._demo_progress = 0.0
            await self.store.create_journey(self.journey_id)
            self._journey_started = True
            self._journey_ended = False
            gcp_snapshot = self.gcp_store.to_dict()
            initial_state = init_agent_state(
                journey_id=self.journey_id,
                user_query=user_query,
                gcp_snapshot=gcp_snapshot,
                request_id=request_id,
            )
            self._state_snapshot = dict(initial_state)
            await self._start_graph_task(initial_state, request_id)
        else:
            # 已有旅程且在进行中：用户发来新 query
            # 注意：图可能在 interrupt_after 点暂停（recommending/ready/in_progress），
            # 也可能还在执行某个节点中（用户中途打断）。
            # 直接 resume 会走到下一个节点（planning），不会回到 understanding。
            # 正确做法是：用本地累积的 _state_snapshot 作为旧 state（避免依赖 graph checkpoint 时机），
            # 基于它构建新 initial_state，保留 chat_history / proposals / slots，
            # 然后从 START → understanding 重新执行。
            old_values = self._state_snapshot or {}
            # 同时尝试从 graph 获取最新状态作为补充（如果已经 checkpoint 了则更准确）
            try:
                snapshot = await self.graph.aget_state(self._config())
                snap_values = snapshot.values or {}
                # 合并：checkpoint 里有的字段优先用（它更完整）
                if snap_values:
                    for k, v in snap_values.items():
                        if v is not None:
                            old_values[k] = v
            except Exception:
                pass
            gcp_snapshot = self.gcp_store.to_dict()
            initial_state = init_agent_state(
                journey_id=self.journey_id,
                user_query=user_query,
                gcp_snapshot=gcp_snapshot,
                request_id=request_id,
            )
            # 重置 _state_snapshot（新任务会从 initial_state 开始重新累积）
            self._state_snapshot = dict(initial_state)

            # 保留已有对话历史（追加新 query，而非覆盖）
            old_history = old_values.get("chat_history") or []
            if old_history:
                # 统一转为 dict（add_messages 可能把它们转成 Message 对象）
                hist_dicts = []
                for m in old_history:
                    if isinstance(m, dict):
                        hist_dicts.append(m)
                    else:
                        try:
                            mt = getattr(m, "type", "")
                            rm = {"human": "user", "ai": "assistant", "system": "system"}
                            hist_dicts.append({
                                "role": rm.get(mt, "user"),
                                "content": getattr(m, "content", ""),
                            })
                        except Exception:
                            pass
                # 追加本轮用户消息
                hist_dicts.append({"role": "user", "content": user_query})
                initial_state["chat_history"] = hist_dicts

            # 保留已有推荐和槽位（让 understanding_node 的快速路径判断有数据可用）
            existing_proposals = old_values.get("proposals") or []
            existing_slots = old_values.get("extracted_slots") or {}
            existing_intent_type = old_values.get("intent_type", "")
            existing_user_intent = old_values.get("user_intent", "")
            existing_dest_name = old_values.get("destination_name", "")
            existing_dest = old_values.get("destination") or {}
            logger.info("resuming with existing context: intent_type=%r, user_intent=%r, proposals=%d, slots_keys=%s",
                         existing_intent_type, existing_user_intent, len(existing_proposals),
                         list(existing_slots.keys()) if existing_slots else [])

            if existing_proposals:
                initial_state["proposals"] = existing_proposals
            if existing_slots:
                initial_state["extracted_slots"] = existing_slots
            if existing_intent_type and existing_intent_type != "unknown":
                initial_state["intent_type"] = existing_intent_type
                initial_state["user_intent"] = existing_user_intent
            if existing_dest_name:
                initial_state["destination_name"] = existing_dest_name
                initial_state["destination"] = existing_dest

            await self._start_graph_task(initial_state, request_id)

    async def handle_clarify_reply(self, answer: str, request_id: str) -> None:
        """用户回复澄清问题 → resume 图。"""
        await self._cancel_graph_task()
        await self.graph.aupdate_state(
            self._config(),
            {
                "user_clarify_reply": answer,
                "chat_history": [{"role": "user", "content": answer}],
                "request_id": request_id,
            },
        )
        await self._start_graph_task(None, request_id)

    async def handle_journey_action(
        self, action: str, payload: Dict[str, Any], request_id: str,
    ) -> None:
        """处理旅程操作。

        action:
        - select_plan: 用户选择方案
        - confirm_departure: 确认出发
        - select_candidate: 选择候选 POI（目的地消歧）
        - cancel: 取消行程
        """
        updates: Dict[str, Any] = {"request_id": request_id}

        if action == "select_plan":
            updates["selected_plan_id"] = payload.get("plan_id", "plan_1")

        elif action == "confirm_departure":
            updates["journey_started_at"] = 0.0  # 将在 in_progress 节点设置

        elif action == "select_candidate":
            # 用户从候选列表中选择了一个 POI，直接写入 destination
            candidate = payload.get("candidate") or {}
            location = candidate.get("location")
            dest_lat: float = 0
            dest_lon: float = 0
            # location 可能是 dict {lat, lon} 或 string "lon,lat"
            if isinstance(location, dict):
                dest_lat = float(location.get("lat", 0) or 0)
                dest_lon = float(location.get("lon", 0) or location.get("lng", 0) or 0)
            elif isinstance(location, str) and "," in location:
                parts = location.split(",")
                try:
                    dest_lon = float(parts[0])
                    dest_lat = float(parts[1])
                except (ValueError, IndexError):
                    pass
            dest = {
                "name": candidate.get("name", ""),
                "address": candidate.get("address", ""),
                "lat": dest_lat,
                "lon": dest_lon,
                "poi_id": candidate.get("id", ""),
            }
            updates["destination"] = dest
            updates["destination_name"] = candidate.get("name", "")
            updates["destination_candidates"] = []  # 消费掉候选
            updates["user_clarify_reply"] = ""  # 避免被当成新 query 重新解析
            logger.info(
                "select_candidate: name=%s, lat=%s, lon=%s",
                dest["name"], dest_lat, dest_lon,
            )

        elif action == "cancel":
            await self._sync_to_gcp("idle")
            self._stop_gcp_monitor()
            return

        await self.graph.aupdate_state(self._config(), updates)
        await self._start_graph_task(None, request_id)

    # ---- 语音 API（被 ws.py 调用） ----

    @property
    def voice_available(self) -> bool:
        """语音功能是否可用（API key 已配置）。"""
        return self._voice_enabled

    async def handle_audio_start(
        self, request_id: str, sample_rate: int = 16000, audio_format: str = "pcm",
    ) -> None:
        """开始语音输入：创建 ASR 客户端，启动识别。"""
        if not self._voice_enabled:
            raise RuntimeError("voice service not configured")

        # 清理之前可能残留的会话
        await self._cleanup_asr()

        self._asr_request_id = request_id
        self._asr_client = VolcAsrClient(
            sample_rate=sample_rate,
            audio_format=audio_format,
            enable_nonstream=True,
        )
        self._asr_task = asyncio.create_task(self._run_asr(request_id))
        logger.info("asr session started: rid=%s", request_id)

    async def handle_audio_chunk(self, audio_bytes: bytes) -> None:
        """接收音频分片，转发给 ASR。"""
        if not self._voice_enabled:
            # 语音不可用时不处理，ws.py 已在 AUDIO_START 时返回错误
            return
        if not self._asr_client:
            logger.warning("audio_chunk received but no ASR session active")
            return
        if audio_bytes:
            await self._asr_client.send_audio(audio_bytes)

    async def handle_audio_stop(self, request_id: str) -> None:
        """结束语音输入：通知 ASR 结束，等待最终结果。"""
        if self._asr_client:
            await self._asr_client.finish()
            logger.info("asr audio stopped, waiting final result: rid=%s", request_id)

    async def _run_asr(self, request_id: str) -> None:
        """ASR 事件循环：消费 ASR 事件，实时推送到 WS，最终文本入旅程。"""
        if not self._asr_client:
            return

        final_text = ""
        _asr_ready_sent = False
        try:
            async for event in self._asr_client.start():
                # 连接建立后的第一个 START 事件 → 通知前端 ASR 已就绪
                if event.type == AsrEventType.START and not _asr_ready_sent:
                    _asr_ready_sent = True
                    try:
                        await self.ws.send_text(ws_msg(
                            WsMessageType.ASR_READY,
                            {"status": "ready"},
                            request_id,
                        ))
                    except Exception as e:
                        logger.debug("asr ready send error: %s", e)
                    continue

                if event.type == AsrEventType.PARTIAL:
                    # 实时转写结果：直接发 WS（不经过 stream_queue，因为可能不在图执行阶段）
                    try:
                        await self.ws.send_text(ws_msg(
                            WsMessageType.ASR_TEXT,
                            {"text": event.text, "is_final": False},
                            request_id,
                        ))
                    except Exception as e:
                        logger.debug("asr partial send error: %s", e)

                elif event.type == AsrEventType.UTTERANCE:
                    if event.is_final:
                        final_text = event.text

                elif event.type == AsrEventType.FINAL:
                    final_text = event.text
                    try:
                        await self.ws.send_text(ws_msg(
                            WsMessageType.ASR_FINAL,
                            {"text": event.text, "is_final": True},
                            request_id,
                        ))
                    except Exception as e:
                        logger.debug("asr final send error: %s", e)

                elif event.type == AsrEventType.END:
                    break

                elif event.type == AsrEventType.ERROR:
                    logger.error("asr error: %s", event.text)
                    try:
                        await self.ws.send_text(ws_msg(
                            WsMessageType.ERROR,
                            {"code": "ASR_ERROR", "message": f"语音识别出错: {event.text}"},
                            request_id,
                        ))
                    except Exception:
                        pass
        except Exception as e:
            logger.exception("asr session error: %s", e)
            try:
                await self.ws.send_text(ws_msg(
                    WsMessageType.ERROR,
                    {"code": "ASR_ERROR", "message": f"语音识别出错: {e}"},
                    request_id,
                ))
            except Exception:
                pass

        # ASR 结束后，用最终文本启动旅程
        if final_text.strip():
            logger.info("asr final text: '%s', starting journey", final_text[:50])
            await self.start_journey(final_text.strip(), request_id)

        await self._cleanup_asr()

    async def _cleanup_asr(self) -> None:
        """清理 ASR 会话资源。"""
        if self._asr_task and not self._asr_task.done():
            self._asr_task.cancel()
            try:
                await self._asr_task
            except asyncio.CancelledError:
                pass
            self._asr_task = None
        if self._asr_client:
            self._asr_client = None

    async def _speak(self, text: str, request_id: str) -> None:
        """TTS 合成：将文本转为语音，通过 WS 发送给前端。"""
        if not self._tts_enabled or not text.strip():
            return

        try:
            import base64
            tts = VolcTtsClient()
            audio_chunks: List[bytes] = []
            async for event in tts.synthesize(text):
                if event.type == TtsEventType.AUDIO:
                    audio_chunks.append(event.audio)
                elif event.type == TtsEventType.ERROR:
                    logger.error("tts error: %s", event.text)
                    return

            if audio_chunks:
                audio_data = b"".join(audio_chunks)
                audio_b64 = base64.b64encode(audio_data).decode("ascii")
                try:
                    await self.ws.send_text(ws_msg(
                        WsMessageType.TTS_AUDIO,
                        {
                            "audio_b64": audio_b64,
                            "format": tts.audio_format,
                            "text": text[:200],
                        },
                        request_id,
                    ))
                except Exception as e:
                    logger.error("tts ws send error: %s", e)
        except Exception as e:
            logger.exception("tts synthesize error: %s", e)

    def cleanup(self) -> None:
        """WS 断开时清理资源。"""
        self._stop_gcp_monitor()
        self._stop_stream_writer()
        # 清理 ASR 会话
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._cleanup_asr())
        except Exception:
            pass
        if self._journey_started:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.store.abandon_journey(self.journey_id))
            except Exception:
                pass
        logger.info("orchestrator cleaned up: %s", self.journey_id)

    # ---- 图任务管理（后台执行 + 可中断） ----

    async def _start_graph_task(self, initial_state: Any, request_id: str) -> None:
        """启动图执行任务（后台运行，不阻塞调用者）。

        如果已有运行中的任务，会先取消并等待其完全清理，避免新旧任务
        竞争 stream_queue / writer_task 资源。
        """
        await self._cancel_graph_task()
        self._graph_cancelled = False
        self._graph_task = asyncio.create_task(
            self._run_graph(initial_state, request_id)
        )
        self._graph_task.add_done_callback(self._on_graph_task_done)
        logger.debug("graph task started: rid=%s", request_id)

    async def _cancel_graph_task(self) -> None:
        """取消当前运行的图执行任务，并等待其完全清理。"""
        self._graph_cancelled = True

        old_task = self._graph_task
        old_writer = self._stream_writer_task

        if old_task and not old_task.done():
            old_task.cancel()
            logger.info("graph task cancelled: %s", self.journey_id)
            # 等待旧任务真正结束（包括 finally 块执行完毕），
            # 避免它还在向 stream_queue 放 __end__ 或其他残留事件
            try:
                await asyncio.wait_for(asyncio.shield(old_task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass

        # 等待旧的 stream writer 退出
        if old_writer and not old_writer.done():
            try:
                await asyncio.wait_for(asyncio.shield(old_writer), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass

        # 停止 TTS 播放
        if self._tts_play_task and not self._tts_play_task.done():
            self._tts_play_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._tts_play_task), timeout=1.0)
            except Exception:
                pass
            self._tts_play_task = None

        # 清空 stream_queue，丢弃旧任务的残留事件
        self._clear_stream_queue()
        self._stream_writer_task = None

    def _clear_stream_queue(self) -> None:
        """清空 stream_queue 中的所有待发送事件。"""
        while not self.stream_queue.empty():
            try:
                self.stream_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _on_graph_task_done(self, task: asyncio.Task) -> None:
        """图任务完成后的回调（清理引用）。"""
        if self._graph_task is task:
            self._graph_task = None
        if task.cancelled():
            logger.info("graph task finished (cancelled): %s", self.journey_id)
        elif task.exception():
            logger.error(
                "graph task finished with error: %s",
                task.exception(),
            )
        else:
            logger.debug("graph task finished normally: %s", self.journey_id)

    # ---- 核心执行循环 ----

    async def _run_graph(
        self, initial_state: Any, request_id: str,
    ) -> None:
        """执行图（首次或 resume），并将事件转发到 WS。"""
        self._request_id = request_id

        # 启动 stream writer 后台任务
        writer_done = asyncio.Event()
        self._stream_writer_task = asyncio.create_task(
            self._drain_queue_to_ws(writer_done)
        )

        exec_error = None
        try:
            if initial_state is not None:
                async for event in self.graph.astream(
                    initial_state,
                    self._config(),
                    stream_mode="updates",
                ):
                    await self._handle_graph_event(event)
            else:
                async for event in self.graph.astream(
                    None,
                    self._config(),
                    stream_mode="updates",
                ):
                    await self._handle_graph_event(event)
        except asyncio.CancelledError:
            logger.info("graph execution cancelled: %s", self.journey_id)
            # 发送结束哨兵以解锁 stream writer
            try:
                self.stream_queue.put_nowait(("__end__", {}))
            except Exception:
                pass
            raise
        except Exception as e:
            exec_error = e
            logger.exception("graph execution error: %s", e)
            try:
                self.stream_queue.put_nowait(("error", {
                    "code": "GRAPH_ERROR",
                    "message": str(e),
                }))
            except Exception:
                pass
        finally:
            # 等待队列排空（发送 __end__ 哨兵）
            if not self._graph_cancelled:
                try:
                    await self.stream_queue.put(("__end__", {}))
                except Exception:
                    pass
            try:
                await writer_done.wait()
            except asyncio.CancelledError:
                pass
            self._stream_writer_task = None

        # 执行完后同步状态到 GCP，并可能启动 GCP 监听（被取消时跳过）
        if exec_error is None and not self._graph_cancelled:
            try:
                snapshot = await self.graph.aget_state(self._config())
                status = (snapshot.values or {}).get("journey_status", "")
                await self._sync_to_gcp(status)

                # 旅程进行中（含规划/就绪/进行中/重规划）启动 GCP 监听
                if status in ("in_progress", "replanning", "arriving",
                              "planning", "ready", "recommending", "understanding"):
                    self._start_gcp_monitor()
                elif status in ("completed", "ended", "idle"):
                    self._stop_gcp_monitor()
            except Exception as e:
                logger.error("post-graph sync error: %s", e)
        self._graph_cancelled = False

    async def _handle_graph_event(self, event: Dict[str, Any]) -> None:
        """处理 graph astream 的 updates 事件。"""
        for node_name, node_output in event.items():
            if not isinstance(node_output, dict):
                continue

            # 累积 state 快照（节点输出覆盖/更新本地快照）
            for k, v in node_output.items():
                if isinstance(v, dict) and k in self._state_snapshot and isinstance(self._state_snapshot.get(k), dict):
                    # dict 字段浅 merge（如 extracted_slots, skill_results 等）
                    self._state_snapshot[k] = {**self._state_snapshot[k], **v}
                else:
                    # 其他类型（scalar/list）直接覆盖。add_messages 等 reducer 返回的是完整新值，
                    # 所以 chat_history/proposals 等 list 字段用节点输出的完整版本覆盖
                    self._state_snapshot[k] = v

            # 状态变化
            new_status = node_output.get("journey_status")
            if new_status:
                # 检测终态
                if new_status in ("completed", "ended"):
                    self._journey_ended = True
                try:
                    self.stream_queue.put_nowait(("state_change", {
                        "from_state": node_name,
                        "to_state": new_status,
                    }))
                except Exception:
                    pass

            # 卡片推送职责：
            # - proposals → recommending_node 直接推送
            # - journey_ready → ready_node 直接推送（包含 route/eta/parking 汇总）
            # - eta → in_progress_node 直接推送
            # - route (重规划) → replanning_node 直接推送
            # - parking (arriving) → arriving_node 推送 arriving 卡片含 parking
            # - arriving → arriving_node 直接推送
            # orchestrator 只推送 state_change，各节点自己推送 card_update，避免重复。

    async def _drain_queue_to_ws(self, done: asyncio.Event) -> None:
        """后台任务：将 stream_queue 中的事件翻译为 WS 消息发送。"""
        _ws_closed = False
        try:
            while True:
                evt_type, data = await self.stream_queue.get()
                if evt_type == "__end__":
                    break
                if _ws_closed:
                    continue  # WS 已关闭，静默丢弃
                try:
                    await self._send_ws_event(evt_type, data)
                except RuntimeError as e:
                    # WS 已关闭（"Cannot call send once a close message has been sent"等）
                    if "close" in str(e).lower() or "not connected" in str(e).lower():
                        _ws_closed = True
                        logger.warning("ws closed during stream, dropping remaining events")
                    else:
                        raise
        except Exception as e:
            logger.exception("drain_queue error: %s", e)
        finally:
            done.set()

    async def _send_ws_event(self, evt_type: str, data: Dict) -> None:
        """将内部事件映射为 WsMessage 并发送。"""
        mapping = {
            "token_stream": WsMessageType.TOKEN_STREAM,
            "thinking_stream": WsMessageType.THINKING_STREAM,
            "thinking_end": WsMessageType.THINKING_END,
            "message": WsMessageType.MESSAGE,
            "skill_start": WsMessageType.SKILL_START,
            "skill_result": WsMessageType.SKILL_RESULT,
            "state_change": WsMessageType.STATE_CHANGE,
            "card_update": WsMessageType.CARD_UPDATE,
            "clarify_question": WsMessageType.CLARIFY_QUESTION,
            "error": WsMessageType.ERROR,
            "asr_text": WsMessageType.ASR_TEXT,
            "asr_final": WsMessageType.ASR_FINAL,
        }
        ws_type = mapping.get(evt_type)
        if ws_type is None:
            logger.debug("unknown event type: %s", evt_type)
            return

        try:
            await self.ws.send_text(ws_msg(ws_type, data, self._request_id))
        except Exception as e:
            logger.error("ws send error: %s", e)

        # TTS 播报：assistant message 事件触发语音合成（异步，不阻塞）
        if evt_type == "message" and self._tts_enabled:
            msg_content = data.get("content", "") if isinstance(data, dict) else ""
            msg_role = data.get("role", "") if isinstance(data, dict) else "assistant"
            if msg_role == "assistant" and msg_content.strip():
                try:
                    self._tts_play_task = asyncio.create_task(self._speak(msg_content, self._request_id))
                except Exception:
                    pass

    # ---- GCP 同步 ----

    async def _sync_to_gcp(self, status: str) -> None:
        """在生命周期边界同步关键字段到 GCP ContextStore。"""
        try:
            snapshot = await self.graph.aget_state(self._config())
        except Exception:
            return

        values = snapshot.values or {}

        updates: Dict[str, Any] = {"journey.status": status}

        # 同步意图类型（避免 GCP 面板一直显示 unknown）
        intent_type = values.get("intent_type", "")
        if intent_type and intent_type != "unknown":
            updates["journey.intent_type"] = intent_type

        user_query = values.get("user_query", "")
        if user_query:
            updates["journey.user_query"] = user_query

        dest_name = values.get("destination_name", "")
        if dest_name:
            updates["journey.destination_name"] = dest_name

        dest = values.get("destination") or {}
        if dest.get("lat") is not None and dest.get("lon") is not None:
            updates["journey.destination.lat"] = float(dest["lat"])
            updates["journey.destination.lon"] = float(dest["lon"])

        eta = values.get("eta") or {}
        if eta.get("eta_arrival_time"):
            updates["journey.eta_arrival"] = str(eta["eta_arrival_time"])
        if eta.get("remaining_min") is not None:
            updates["journey.eta_remaining_min"] = int(eta["remaining_min"])

        route = values.get("route") or {}
        if route.get("distance_km") is not None:
            updates["journey.route.distance_km"] = float(route["distance_km"])
        if route.get("duration_min") is not None:
            updates["journey.route.duration_min"] = int(route["duration_min"])

        # 同步进度
        if values.get("progress_pct") is not None:
            updates["journey.progress_pct"] = float(values["progress_pct"])

        try:
            await self.gcp_store.update(updates)
        except Exception as e:
            logger.error("sync to gcp error: %s", e)

    # ---- GCP 事件监听（in_progress 期间） ----

    def _start_gcp_monitor(self) -> None:
        if self._gcp_monitor_running:
            return
        self._gcp_monitor_running = True
        self._gcp_monitor_task = asyncio.create_task(
            self._monitor_gcp_loop()
        )
        logger.info("gcp monitor started: %s", self.journey_id)

    def _stop_gcp_monitor(self) -> None:
        self._gcp_monitor_running = False
        if self._gcp_monitor_task and not self._gcp_monitor_task.done():
            self._gcp_monitor_task.cancel()
            self._gcp_monitor_task = None

    async def _monitor_gcp_loop(self) -> None:
        """后台协程：行程进行中监听 GCP 事件，触发重规划/到达判断。

        - 每 check_interval 秒做一次定期 ETA 检查
        - 监听 GCP event queue 的交通/航班/天气变化事件，事件驱动触发重规划
        - 事件触发有冷却间隔保护，避免级联
        """
        # Demo 模式下用短间隔加速演示，非 demo 模式保持 30s
        if self._demo_simulation_enabled:
            check_interval = settings.demo_eta_interval_sec
            min_event_interval = 5  # demo 模式下冷却间隔也缩短
        else:
            check_interval = 30
            min_event_interval = 15  # 秒，事件触发的最小冷却间隔（防级联，重规划代价高）
        replan_cooldown = 60  # 秒，同类原因重规划的冷却时间
        max_replan_count = 5  # 最大连续重规划次数（防无限循环）
        last_check = 0.0
        _last_replan_category = ""
        _last_replan_time = 0.0
        # 订阅独立的事件队列（fan-out），不与 /ws/gcp 广播争抢
        queue = self.gcp_store.subscribe_events(maxsize=100)

        # 启动时立即做一次检查
        try:
            await self._periodic_eta_check()
            last_check = time.time()
        except Exception as e:
            logger.error("initial eta check error: %s", e)

        try:
            while self._gcp_monitor_running:
                now = time.time()
                time_since_last = now - last_check
                should_check = time_since_last >= check_interval

                # 计算等待超时时间（到下次定时检查的剩余时间，或最多等5秒）
                wait_timeout = max(1.0, min(5.0, check_interval - time_since_last))

                event_received = False
                event = None
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_timeout)
                    event_received = True
                    logger.debug("gcp event received: %s", event.get("type") if isinstance(event, dict) else event)
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    break

                now = time.time()
                time_since_last = now - last_check

                # 分析事件是否需要重规划
                replan_reason = ""
                if event_received and time_since_last >= min_event_interval:
                    # 过滤掉 demo 自己产生的位置更新事件（通过 position_source 判断）
                    is_self_generated = False
                    if isinstance(event, dict):
                        fields = event.get("fields", []) or []
                        pos_fields = [f for f in fields if f.startswith("vehicle.position")]
                        if pos_fields:
                            snap = event.get("snapshot", {}) or {}
                            veh = snap.get("vehicle", {}) or {}
                            is_self_generated = (veh.get("position_source") == "demo_sim")
                    if not is_self_generated:
                        # 读取当前 graph state 判断 replan_count
                        try:
                            snap = await self.graph.aget_state(self._config())
                            vals = snap.values or {}
                            replan_count = vals.get("replan_count", 0)
                        except Exception:
                            replan_count = 0

                        if replan_count < max_replan_count:
                            rule_reason = self._analyze_gcp_event(event)
                            if rule_reason:
                                cat = self._replan_category(rule_reason)
                                if (
                                    cat != _last_replan_category
                                    or now - _last_replan_time >= replan_cooldown
                                ):
                                    # 尝试 LLM 判定（可选），规则命中后可直接用
                                    replan_reason = rule_reason

                        # 航班动态提醒（不依赖重规划判断，只要有航班变化就播报）
                        if isinstance(event, dict) and self.skill_executor:
                            fields = event.get("fields", []) or []
                            transit_fields = [f for f in fields if f.startswith("transit.")]
                            if transit_fields:
                                snap = event.get("snapshot", {}) or {}
                                transit = snap.get("transit") or {}
                                if transit.get("flight_no"):
                                    try:
                                        remind_result = await self.skill_executor.execute_skill(
                                            "smart_remind", "transit_dynamic",
                                            {
                                                "flight_no": transit.get("flight_no", ""),
                                                "flight_status": transit.get("status", ""),
                                                "delay_min": transit.get("delay_min", 0),
                                                "terminal": transit.get("terminal", ""),
                                                "gate": transit.get("gate", ""),
                                            },
                                            task_id="gcp_transit_remind",
                                        )
                                        if remind_result.get("status") == "success":
                                            tts = (remind_result.get("data") or {}).get("tts_text", "")
                                            if tts and self.stream_queue:
                                                try:
                                                    self.stream_queue.put_nowait(("message", {
                                                        "role": "assistant",
                                                        "content": tts,
                                                    }))
                                                except Exception:
                                                    pass
                                    except Exception as e:
                                        logger.error("transit dynamic remind error: %s", e)
                                    _last_replan_category = cat
                                    _last_replan_time = now
                                    logger.info(
                                        "gcp event triggers replan: reason=%s, count=%d",
                                        rule_reason, replan_count + 1,
                                    )

                # 判断是否该做检查：
                # - 定时检查到点了
                # - 有非自生成事件（无论是否触发重规划，都做 ETA 更新）
                # - 外部手动修改位置（position_source != demo_sim）也触发检查
                should_check_from_event = False
                if event_received and time_since_last >= min_event_interval:
                    if isinstance(event, dict):
                        fields = event.get("fields", []) or []
                        pos_only = all(f.startswith("vehicle.position") for f in fields)
                        if not pos_only:
                            should_check_from_event = True
                        else:
                            # 位置字段变化，如果是外部手动修改（非 demo），也触发检查
                            snap = event.get("snapshot", {}) or {}
                            veh = snap.get("vehicle", {}) or {}
                            if veh.get("position_source") != "demo_sim":
                                should_check_from_event = True
                    else:
                        should_check_from_event = True

                do_check = should_check or should_check_from_event

                if do_check:
                    last_check = time.time()
                    await self._periodic_eta_check(replan_reason=replan_reason)

        except asyncio.CancelledError:
            logger.info("gcp monitor cancelled: %s", self.journey_id)
        except Exception as e:
            logger.exception("gcp monitor error: %s", e)
        finally:
            self.gcp_store.unsubscribe_events(queue)

    async def _periodic_eta_check(self, replan_reason: str = "") -> None:
        """定期 ETA 检查：更新 ETA，判断是否到达/需要重规划。

        如果启用了 DEMO 模拟模式（JOURNEY_DEMO_SIMULATION=1），
        每次检查前自动将车辆位置沿路线 polyline 前进一步，
        让整个行程能够自动推进，无需手动操作 GCP 面板。

        Args:
            replan_reason: 由 GCP 事件分析得出的重规划原因。非空时在 state 中
                设置 replan_reason，使 route_after_in_progress 路由到 replanning 节点。
        """
        try:
            snapshot = await self.graph.aget_state(self._config())
            values = snapshot.values or {}
            status = values.get("journey_status", "")
            logger.info("periodic_eta_check: status=%s, demo=%s", status, self._demo_simulation_enabled)

            if status != "in_progress":
                if status in ("completed", "ended"):
                    self._stop_gcp_monitor()
                # 非 in_progress 状态（如 planning/ready/recommending）只同步不推进
                return

            # Demo 模式：沿路线推进车辆位置（仅在行程进行中）
            if self._demo_simulation_enabled:
                await self._advance_demo_vehicle()

            # 使用固定 request_id 以便前端去重更新同一张 ETA 卡片
            monitor_rid = f"gcp_monitor_{self.journey_id}"
            self._request_id = monitor_rid
            state_updates: Dict[str, Any] = {"request_id": monitor_rid}

            # 注入 GCP 事件触发的重规划原因
            if replan_reason:
                state_updates["replan_reason"] = replan_reason

            # Demo 模式：将推进进度注入 state，in_progress 节点可用此比例模拟 ETA 递减
            if self._demo_simulation_enabled:
                state_updates["progress_pct"] = self._demo_progress

            await self.graph.aupdate_state(self._config(), state_updates)

            # 启动 stream writer 以便推送 ETA/卡片更新
            writer_done = asyncio.Event()
            self._stream_writer_task = asyncio.create_task(
                self._drain_queue_to_ws(writer_done)
            )

            try:
                # 从 in_progress 节点恢复执行（路由会判断 arriving/replanning/continue）
                async for event in self.graph.astream(
                    None, self._config(), stream_mode="updates",
                ):
                    await self._handle_graph_event(event)
            except asyncio.CancelledError:
                logger.info("periodic eta check cancelled")
                try:
                    self.stream_queue.put_nowait(("__end__", {}))
                except Exception:
                    pass
                raise
            finally:
                # 发送结束哨兵
                try:
                    if not self._graph_cancelled:
                        await self.stream_queue.put(("__end__", {}))
                    else:
                        self.stream_queue.put_nowait(("__end__", {}))
                except Exception:
                    pass
                try:
                    await writer_done.wait()
                except asyncio.CancelledError:
                    pass
                self._stream_writer_task = None

            # 如果图被取消了（用户发了新请求），不继续同步
            if self._graph_cancelled:
                return

            # 同步 GCP
            new_snapshot = await self.graph.aget_state(self._config())
            new_values = new_snapshot.values or {}
            new_status = new_values.get("journey_status", "")
            await self._sync_to_gcp(new_status)

            # 如果状态变成 arriving/completed，停止监听
            if new_status in ("arriving", "completed", "ended"):
                self._stop_gcp_monitor()

        except asyncio.CancelledError:
            logger.info("periodic eta check cancelled (outer)")
        except Exception as e:
            logger.exception("periodic eta check error: %s", e)

    # ---- GCP 事件分析（事件驱动重规划） ----

    @staticmethod
    def _analyze_gcp_event(event: Dict[str, Any]) -> str:
        """分析 GCP 事件，判断是否需要重规划。

        返回重规划原因字符串（空串表示不需要重规划）。
        基于规则快速判定：检查变更的字段是否落在交通/航班/天气等关键模块。
        """
        if not isinstance(event, dict):
            return ""
        fields = event.get("fields", []) or []
        snapshot = event.get("snapshot", {}) or {}
        if not fields:
            return ""

        # ---- 交通变化 ----
        traffic_fields = [f for f in fields if f.startswith("traffic.on_route")]
        if traffic_fields:
            traffic = ((snapshot.get("traffic") or {}).get("on_route") or {})
            status = str(traffic.get("overall_status", ""))
            delay = traffic.get("total_delay_min", 0) or 0
            if status in ("congested", "severe"):
                if status == "severe":
                    return "前方道路严重拥堵，需要重新规划路线"
                return "前方道路拥堵，预计行程时间增加"
            try:
                if int(delay) >= 15:
                    return f"路线拥堵延误{int(delay)}分钟，需要重新规划"
            except (ValueError, TypeError):
                pass

        # ---- 航班变化 ----
        transit_fields = [f for f in fields if f.startswith("transit.")]
        if transit_fields:
            transit = snapshot.get("transit") or {}
            flight_status = str(transit.get("status", ""))
            delay_min = transit.get("delay_min", 0) or 0
            if flight_status == "cancelled":
                return "航班已取消，请确认行程安排"
            if flight_status == "delayed":
                try:
                    dm = int(delay_min)
                    if dm >= 15:
                        return f"航班延误{dm}分钟，请确认接送时间"
                except (ValueError, TypeError):
                    return "航班延误，请确认接送时间"
            if flight_status == "arrived":
                return "航班已到达，准备前往接机"

        # ---- 天气变化 ----
        weather_fields = [f for f in fields if f.startswith("weather.live")]
        if weather_fields:
            weather_live = ((snapshot.get("weather") or {}).get("live") or {})
            w = str(weather_live.get("weather", ""))
            wind = str(weather_live.get("windpower", ""))
            severe_keywords = ["暴雨", "暴雪", "台风", "冰雹", "大雾", "雷暴", "强对流"]
            if any(kw in w for kw in severe_keywords):
                return f"天气变化（{w}），请注意行车安全"
            # 风力 ≥6 级
            try:
                wind_num = int(wind.replace("级", "").replace("-", "").split("~")[-1])
                if wind_num >= 6:
                    return "大风天气，请注意行车安全"
            except (ValueError, IndexError):
                pass

        return ""

    @staticmethod
    def _replan_category(reason: str) -> str:
        """将重规划原因归类，用于冷却去重。"""
        if any(k in reason for k in ["拥堵", "路线", "延误"]):
            return "traffic"
        if any(k in reason for k in ["航班", "接机"]):
            return "flight"
        if any(k in reason for k in ["天气", "大风", "暴雨"]):
            return "weather"
        return "other"

    # ---- Demo 模拟模式 ----

    @property
    def _demo_simulation_enabled(self) -> bool:
        """是否启用 Demo 自动模拟模式。

        通过配置项 JOURNEY_DEMO_SIMULATION=true 启用（在 .env 中设置）。
        启用后，行程中的车辆位置会沿路线自动推进。
        """
        return settings.journey_demo_simulation

    _demo_progress: float = 0.0  # 0.0 ~ 1.0，已行进的比例

    async def _advance_demo_vehicle(self) -> None:
        """Demo 模式：沿路线 polyline 推进车辆位置。

        采用固定时间步长递减：每次推进 DEMO_ETA_STEP_MIN 分钟对应的进度，
        让 ETA 以恒定速率减少，演示节奏更可控。
        """
        try:
            snapshot = await self.graph.aget_state(self._config())
            values = snapshot.values or {}
            status = values.get("journey_status", "")
            if status != "in_progress":
                return

            route = values.get("route") or {}
            total_min = route.get("duration_min", 0)
            polyline_str = route.get("polyline", "")
            if not polyline_str or not total_min:
                return

            points = plain_to_polyline(polyline_str)
            if len(points) < 2:
                return

            # 每次推进固定分钟数对应的进度比例
            step_min = float(settings.demo_eta_step_min)
            if total_min > 0:
                progress_step = step_min / total_min
            else:
                progress_step = 0.12  # 兜底：退化为比例推进

            prev_progress = self._demo_progress
            self._demo_progress = min(1.0, self._demo_progress + progress_step)
            if self._demo_progress >= 0.98:
                # 接近终点时，让 ETA check 判定到达
                self._demo_progress = 1.0

            logger.info(
                "demo advance: total_min=%s, step_min=%s, progress %.3f→%.3f",
                total_min, step_min, prev_progress, self._demo_progress,
            )

            # 在路线上找到对应进度的点
            total_dist = 0.0
            seg_dists: List[float] = []
            for i in range(len(points) - 1):
                d = self._haversine_km(points[i], points[i + 1])
                seg_dists.append(d)
                total_dist += d

            if total_dist == 0:
                return

            target_dist = total_dist * self._demo_progress
            acc = 0.0
            for i, seg_d in enumerate(seg_dists):
                if acc + seg_d >= target_dist:
                    ratio = (target_dist - acc) / seg_d if seg_d > 0 else 0
                    # 在第 i 段上按比例插值
                    p1 = points[i]
                    p2 = points[i + 1]
                    lon = p1[0] + (p2[0] - p1[0]) * ratio
                    lat = p1[1] + (p2[1] - p1[1]) * ratio
                    # 更新 GCP 中的车辆位置（标记为 demo 自生成，避免触发自身的重规划）
                    await self.gcp_store.update({
                        "vehicle.position.lat": round(lat, 6),
                        "vehicle.position.lon": round(lon, 6),
                        "vehicle.position_source": "demo_sim",
                    })
                    logger.info(
                        "demo simulation: progress=%.1f%%, pos=(%.6f, %.6f)",
                        self._demo_progress * 100, lon, lat,
                    )
                    return
                acc += seg_d

            # 到达终点
            last = points[-1]
            await self.gcp_store.update({
                "vehicle.position.lat": round(last[1], 6),
                "vehicle.position.lon": round(last[0], 6),
                "vehicle.position_source": "demo_sim",
            })
        except Exception as e:
            logger.warning("demo advance error: %s", e)

    @staticmethod
    def _haversine_km(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        """计算两点间的球面距离（公里）。"""
        lon1, lat1 = p1
        lon2, lat2 = p2
        R = 6371.0  # 地球半径 km
        lat1_r = math.radians(lat1)
        lat2_r = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    # ---- 辅助 ----

    def _config(self) -> Dict[str, Any]:
        """构造 LangGraph config。"""
        return make_graph_config(
            journey_id=self.journey_id,
            stream_queue=self.stream_queue,
            claude=self.claude,
            skill_executor=self.skill_executor,
            gcp_store=self.gcp_store,
        )

    def _stop_stream_writer(self) -> None:
        if self._stream_writer_task and not self._stream_writer_task.done():
            self._stream_writer_task.cancel()
            self._stream_writer_task = None
