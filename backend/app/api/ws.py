"""WebSocket 路由 —— 对话/语音通道 + GCP 事件推送。"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..agent import JourneyOrchestrator
from ..core.config import settings
from ..core.logging import get_logger
from ..core.websocket import WsMessageType, ws_msg, parse_ws_message
from ..gcp import get_store

logger = get_logger(__name__)
router = APIRouter()

# 活跃的 GCP 事件订阅者（前端控制面板用）
_gcp_clients: Set[WebSocket] = set()


# ====================== /ws/chat —— 主对话通道（文本 + 语音） ======================

@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    """主对话 WebSocket。

    支持文本输入和语音输入（ASR/TTS）。
    文本消息用 JSON text frame，音频数据用 base64 编码放在 AUDIO_CHUNK payload 中。
    """
    await websocket.accept()
    logger.info("chat ws connected: %s", id(websocket))

    orchestrator = JourneyOrchestrator(websocket)

    try:
        while True:
            raw = await websocket.receive()
            # FastAPI WebSocket: {"type": "websocket.receive", "text": str} or {"bytes": bytes}
            if raw["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(
                    code=raw.get("code", 1000),
                    reason=raw.get("reason", ""),
                )

            # 二进制帧：未来可用于直接传 PCM；当前语音用 base64-in-text
            if "bytes" in raw:
                # 二进制帧视为 audio chunk（直接转发）
                await orchestrator.handle_audio_chunk(raw["bytes"])
                continue

            if "text" not in raw:
                continue

            msg = parse_ws_message(raw["text"])
            logger.debug("chat ws received: %s, request_id=%s", msg.type, msg.request_id)

            if msg.type == WsMessageType.PING:
                await websocket.send_text(ws_msg(WsMessageType.PONG, {}, msg.request_id))
                continue

            if msg.type == WsMessageType.TEXT_INPUT:
                text = msg.payload.get("text", "")
                if not text:
                    await websocket.send_text(ws_msg(
                        WsMessageType.ERROR,
                        {"code": "EMPTY_MESSAGE", "message": "消息内容不能为空"},
                        msg.request_id,
                    ))
                    continue
                await orchestrator.start_journey(text, msg.request_id)
                continue

            if msg.type == WsMessageType.CLARIFY_REPLY:
                answer = msg.payload.get("answer", "")
                await orchestrator.handle_clarify_reply(answer, msg.request_id)
                continue

            if msg.type == WsMessageType.JOURNEY_ACTION:
                action = msg.payload.get("action", "")
                await orchestrator.handle_journey_action(
                    action, msg.payload, msg.request_id,
                )
                continue

            # ---- 语音消息 ----

            if msg.type == WsMessageType.AUDIO_START:
                if not orchestrator.voice_available:
                    await websocket.send_text(ws_msg(
                        WsMessageType.ERROR,
                        {"code": "VOICE_NOT_AVAILABLE",
                         "message": "语音服务未配置，请设置 VOLCENGINE_API_KEY"},
                        msg.request_id,
                    ))
                    continue
                try:
                    sample_rate = int(msg.payload.get("sample_rate", 16000))
                    audio_format = msg.payload.get("format", "pcm")
                    await orchestrator.handle_audio_start(
                        msg.request_id, sample_rate=sample_rate, audio_format=audio_format,
                    )
                except Exception as e:
                    logger.error("audio_start error: %s", e)
                    await websocket.send_text(ws_msg(
                        WsMessageType.ERROR,
                        {"code": "ASR_START_FAILED", "message": str(e)},
                        msg.request_id,
                    ))
                continue

            if msg.type == WsMessageType.AUDIO_CHUNK:
                if not orchestrator.voice_available:
                    await websocket.send_text(ws_msg(
                        WsMessageType.ERROR,
                        {"code": "VOICE_NOT_AVAILABLE",
                         "message": "语音服务未配置"},
                        msg.request_id,
                    ))
                    continue
                audio_b64 = msg.payload.get("audio", "")
                if audio_b64:
                    try:
                        audio_bytes = base64.b64decode(audio_b64)
                        await orchestrator.handle_audio_chunk(audio_bytes)
                    except Exception as e:
                        logger.warning("audio_chunk decode error: %s", e)
                continue

            if msg.type == WsMessageType.AUDIO_STOP:
                await orchestrator.handle_audio_stop(msg.request_id)
                continue

            # 其他未实现类型
            await websocket.send_text(ws_msg(
                WsMessageType.ERROR,
                {"code": "NOT_IMPLEMENTED", "message": f"handler for {msg.type} not ready yet"},
                msg.request_id,
            ))

    except WebSocketDisconnect:
        logger.info("chat ws disconnected: %s", id(websocket))
        orchestrator.cleanup()
    except Exception as e:
        logger.exception("chat ws error: %s", e)
        orchestrator.cleanup()


# ====================== /ws/gcp —— GCP 事件推送 ======================

async def _broadcast_gcp_event(message: str) -> None:
    dead = []
    for ws in _gcp_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _gcp_clients.discard(ws)


@router.websocket("/ws/gcp")
async def gcp_websocket(websocket: WebSocket):
    """GCP 控制面板 WebSocket：订阅 GCP 状态变化。"""
    await websocket.accept()
    _gcp_clients.add(websocket)
    logger.info("gcp ws connected: %s, total=%d", id(websocket), len(_gcp_clients))

    # 连上后先推送一次当前快照
    store = get_store()
    await websocket.send_text(ws_msg(WsMessageType.STATE_CHANGE, {
        "module": "gcp",
        "snapshot": store.to_dict(),
    }))

    # 启动后台任务监听 GCP 事件队列
    queue = store.get_event_queue()
    listener_task = asyncio.create_task(_gcp_event_listener(queue))

    try:
        while True:
            raw = await websocket.receive_text()
            msg = parse_ws_message(raw)
            if msg.type == WsMessageType.PING:
                await websocket.send_text(ws_msg(WsMessageType.PONG))
    except WebSocketDisconnect:
        logger.info("gcp ws disconnected: %s", id(websocket))
    except Exception as e:
        logger.exception("gcp ws error: %s", e)
    finally:
        _gcp_clients.discard(websocket)
        listener_task.cancel()


async def _gcp_event_listener(queue: asyncio.Queue) -> None:
    """监听 GCP 事件队列，广播给所有 /ws/gcp 订阅者。"""
    try:
        while True:
            event = await queue.get()
            await _broadcast_gcp_event(json.dumps(event, ensure_ascii=False))
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception("gcp event listener error: %s", e)
