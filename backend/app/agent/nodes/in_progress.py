"""in_progress 节点：行程进行中。

每次被触发时执行一次（不循环）：
- 首次进入：记录开始时间，同步 GCP 状态
- 更新 ETA（基于当前车辆位置到目的地的实时距离/时间）
- 检测是否需要重规划 / 是否即将到达
- 触发必要的 in_journey 提醒
- 推送 ETA 卡片更新
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict

from langgraph.types import RunnableConfig

from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def in_progress_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """行程进行中节点。

    每次 GCP 变化/定时检查触发时被 Orchestrator 调用一次。
    """
    configurable = config.get("configurable", {}) if config else {}
    skill_executor = configurable.get("skill_executor")
    stream_queue = configurable.get("stream_queue")
    gcp_store = configurable.get("gcp_store")

    updates: Dict[str, Any] = {"journey_status": "in_progress"}

    # 首次进入 → 记录开始时间
    started_at = state.get("journey_started_at", 0.0)
    if started_at == 0.0:
        started_at = time.time()
        updates["journey_started_at"] = started_at
        logger.info("journey started: %s", state.get("journey_id", ""))

    # 获取当前位置
    current_pos = None
    route_data = state.get("route") or {}
    if gcp_store:
        try:
            current_pos = gcp_store.get("vehicle.position") or None
        except Exception:
            pass

    # 将 Position 对象 / dict 统一为 {"lat": float, "lon": float}
    current_pos_dict = _to_latlon_dict(current_pos)

    # 获取目的地
    dest = state.get("destination") or {}
    dest_pos = _to_latlon_dict(dest)

    from ...core.config import settings
    demo_mode = settings.journey_demo_simulation

    # 更新 ETA
    eta_data: Dict[str, Any] = {}
    dest_pos_dict = dest_pos  # 已通过 _to_latlon_dict 转换

    # ETA 计算策略：
    # - Demo 模式：始终使用进度比例模拟 ETA（保证平滑递减，避免高德 API 跳变）
    # - 非 Demo 模式：优先调用真实 ETA API，失败时回退到模拟
    progress_pct = state.get("progress_pct", 0.0)
    prev_eta = state.get("eta") or {}

    duration_min_val = route_data.get("duration_min")
    distance_km_val = route_data.get("distance_km")
    has_valid_route = (
        duration_min_val is not None and isinstance(duration_min_val, (int, float)) and duration_min_val > 0
        and distance_km_val is not None and isinstance(distance_km_val, (int, float)) and distance_km_val > 0
    )

    if demo_mode and has_valid_route:
        eta_data = _simulate_eta_from_progress(state, current_pos_dict, gcp_store)
        if eta_data and eta_data.get("remaining_min") is not None:
            # ETA 单调性约束：模拟模式下 ETA 不应增加
            prev_remaining = prev_eta.get("remaining_min")
            progress_pct_log = state.get("progress_pct", 0.0)
            logger.info(
                "demo eta calc: progress=%.3f, total_min=%s, computed_remaining=%s, prev_remaining=%s",
                progress_pct_log, duration_min_val,
                eta_data.get("remaining_min"), prev_remaining,
            )
            if prev_remaining is not None and eta_data["remaining_min"] > prev_remaining:
                eta_data["remaining_min"] = max(0, prev_remaining)
                from datetime import datetime, timedelta
                eta_data["eta_arrival_time"] = (
                    datetime.now() + timedelta(minutes=eta_data["remaining_min"])
                ).strftime("%H:%M")
                logger.info("demo eta clamped to prev: %s", eta_data["remaining_min"])
            updates["eta"] = eta_data

    # 非 demo 模式：调用真实 ETA API
    if not eta_data and skill_executor and current_pos_dict and dest_pos_dict:
        try:
            dest_for_skill = {
                "lat": dest_pos_dict["lat"],
                "lon": dest_pos_dict["lon"],
                "name": dest.get("name", "目的地"),
            }
            result = await skill_executor.execute_skill(
                "dynamic_eta", "eta_query",
                {
                    "current_position": current_pos_dict,
                    "route_polyline": route_data.get("polyline", ""),
                    "destination": dest_for_skill,
                    "total_distance_km": route_data.get("distance_km"),
                    "total_duration_min": route_data.get("duration_min"),
                },
                task_id="eta_update",
            )
            if result.get("status") == "success":
                eta_data = result.get("data") or {}
                # ETA 单调性约束：非 demo 模式下允许小幅度增加（真实交通变化），
                # 但超过 3 分钟的跳变视为 API 抖动，钳制
                prev_remaining = prev_eta.get("remaining_min")
                if prev_remaining is not None and eta_data.get("remaining_min"):
                    jump = eta_data["remaining_min"] - prev_remaining
                    if jump > 3 and not state.get("replan_reason"):
                        eta_data["remaining_min"] = prev_remaining
                        eta_data["eta_arrival_time"] = prev_eta.get("eta_arrival_time", "")
                updates["eta"] = eta_data
        except Exception as e:
            logger.error("eta update error: %s", e)

    # 兜底：如果以上都失败，用按进度比例递减的模拟 ETA
    if not eta_data and route_data.get("duration_min") and route_data.get("distance_km"):
        eta_data = _simulate_eta_from_progress(state, current_pos_dict, gcp_store)
        if eta_data:
            updates["eta"] = eta_data

    if not eta_data:
        eta_data = state.get("eta") or {}

    # 检查 ETA 变化，触发提醒
    prev_eta = (state.get("eta") or {}).get("remaining_min", 0)
    current_remaining = eta_data.get("remaining_min", prev_eta)

    # 判断是否即将到达（≤5 分钟或剩余距离 ≤ 500m）
    remaining_km = eta_data.get("remaining_km", route_data.get("distance_km", 0) or 0)
    if current_remaining is not None and current_remaining != 999:
        if current_remaining <= 5 or (remaining_km and remaining_km <= 0.5):
            updates["journey_status"] = "arriving"

    # ETA 突变检测：非 demo 模式下，ETA 突增 ≥10 分钟时触发重规划
    if (
        prev_eta
        and current_remaining
        and not demo_mode
        and current_remaining - prev_eta >= 10
        and not state.get("replan_reason")
    ):
        updates["replan_reason"] = f"ETA变化，预计到达时间延后约{int(current_remaining - prev_eta)}分钟"

    # ========== ETA 变化 in_journey 提醒 ==========
    if (
        prev_eta
        and current_remaining is not None
        and current_remaining != prev_eta
        and skill_executor
    ):
        eta_delta = int(current_remaining - prev_eta)
        # 仅在变化≥5分钟时才播报，避免频繁提醒
        if abs(eta_delta) >= 5:
            try:
                traffic_info = "路况变化"
                if (gcp_store and
                    hasattr(gcp_store, 'get') and
                    callable(gcp_store.get)):
                    try:
                        traffic = gcp_store.get("traffic.on_route") or {}
                        if isinstance(traffic, dict):
                            traffic_info = traffic.get("worst_segment_desc", "路况变化")
                    except Exception:
                        pass

                next_stop = state.get("next_stop") or state.get("destination_name", "")
                remind_result = await skill_executor.execute_skill(
                    "smart_remind", "in_journey",
                    {
                        "eta_delta_min": eta_delta,
                        "current_traffic": traffic_info if eta_delta > 0 else "",
                        "next_stop": next_stop,
                    },
                    task_id="in_journey_eta",
                )
                if remind_result.get("status") == "success" and stream_queue:
                    tts = (remind_result.get("data") or {}).get("tts_text", "")
                    if tts:
                        try:
                            stream_queue.put_nowait(("message", {
                                "role": "assistant",
                                "content": tts,
                            }))
                        except Exception:
                            pass
            except Exception as e:
                logger.error("in_journey remind error: %s", e)

    # 推送 ETA 更新卡片
    if stream_queue and eta_data:
        try:
            stream_queue.put_nowait(("card_update", {
                "type": "eta",
                "data": eta_data,
            }))
        except Exception:
            pass

    # 更新 GCP 中的进度百分比（demo 模式使用）
    progress_pct = state.get("progress_pct", 0.0)
    total_km = route_data.get("distance_km", 0) or 0
    if total_km > 0 and remaining_km:
        progress_pct = max(progress_pct, 1.0 - (remaining_km / total_km))
        updates["progress_pct"] = round(min(progress_pct, 1.0), 4)

    return updates


# ==================== 辅助方法 ====================

def _to_latlon_dict(obj: Any) -> Dict[str, float] | None:
    """将 Pydantic Position / dict / "lon,lat" 字符串统一为 {"lat": float, "lon": float}。"""
    if obj is None:
        return None
    # Pydantic model (Position)
    if hasattr(obj, "lat") and hasattr(obj, "lon"):
        try:
            return {"lat": float(obj.lat), "lon": float(obj.lon)}
        except (TypeError, ValueError):
            return None
    # dict
    if isinstance(obj, dict):
        lat = obj.get("lat")
        lon = obj.get("lon", obj.get("lng"))
        if lat is not None and lon is not None:
            try:
                return {"lat": float(lat), "lon": float(lon)}
            except (TypeError, ValueError):
                return None
    # "lon,lat" 字符串
    if isinstance(obj, str) and "," in obj:
        parts = obj.split(",")
        try:
            return {"lat": float(parts[1]), "lon": float(parts[0])}
        except (ValueError, IndexError):
            return None
    return None


def _simulate_eta_from_progress(
    state: AgentState,
    current_pos_dict: Dict[str, float] | None,
    gcp_store: Any = None,
) -> Dict[str, Any]:
    """当高德 ETA API 不可用或数据缺失时，按进度比例模拟 ETA 递减。

    - Demo 模式下车辆位置沿路线推进，progress_pct 随 GCP 更新累积
    - 以初始规划的 duration_min 为基准，按已行进比例线性递减
    - 接机场景下考虑航班到达时间
    """
    route = state.get("route") or {}
    total_min = route.get("duration_min", 0)
    total_km = route.get("distance_km", 0)

    if total_min is None or total_km is None or total_min <= 0 or total_km <= 0:
        return {}

    # 优先用 GCP 中的 progress（由 _advance_demo_vehicle 间接反映）
    progress_pct = state.get("progress_pct", 0.0)
    started_at = state.get("journey_started_at", 0.0)

    # 如果没有显式进度，根据经过时间估算（假设按匀速行驶）
    if progress_pct <= 0 and started_at > 0:
        elapsed_sec = time.time() - started_at
        total_sec = total_min * 60
        if total_sec > 0:
            progress_pct = min(1.0, elapsed_sec / total_sec)

    remaining_min = max(0, round(total_min * (1.0 - progress_pct)))
    remaining_km = round(max(0, total_km * (1.0 - progress_pct)), 2)
    now = datetime.now()

    # 接机场景：如果有航班信息且航班未到达，ETA 基于航班到达时间
    eta_arrival_time = ""
    flight_adjusted = False
    if gcp_store and progress_pct < 0.05:  # 出发初期考虑航班时间
        try:
            transit = gcp_store.get("transit") or {}
            flight_status = str(transit.get("status", "")) if hasattr(transit, "get") else ""
            delay_min = 0
            try:
                delay_min = int(getattr(transit, "delay_min", 0) or transit.get("delay_min", 0))
            except (ValueError, TypeError):
                pass

            if flight_status in ("scheduled", "delayed", "departed", "boarding"):
                # 航班还未到达，到达时间 = 航班预计到达时间 + 缓冲
                ata_str = ""
                if hasattr(transit, "ata"):
                    ata_str = transit.ata or ""
                elif isinstance(transit, dict):
                    ata_str = transit.get("ata", "")

                sta_str = ""
                if hasattr(transit, "sta"):
                    sta_str = transit.sta or ""
                elif isinstance(transit, dict):
                    sta_str = transit.get("sta", "")

                # 解析到达时间
                flight_arrival = None
                for ts in [ata_str, sta_str]:
                    if ts:
                        try:
                            flight_arrival = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            break
                        except (ValueError, TypeError):
                            pass

                if flight_arrival:
                    # 航班到达后需要一定缓冲（停車、步行到到达口等）
                    buffer_min = 10 + delay_min
                    # 到达目的地（机场）的时间 = 航班到达 + 缓冲 - 驾车时间
                    # 如果现在出发驾车时间还很长，可能需要等待
                    eta_arrival_time = flight_arrival.strftime("%H:%M")
                    flight_adjusted = True
        except Exception:
            pass

    if not flight_adjusted:
        arrival_time = now + timedelta(minutes=remaining_min)
        eta_arrival_time = arrival_time.strftime("%H:%M")

    return {
        "remaining_min": remaining_min,
        "remaining_km": remaining_km,
        "eta_arrival_time": eta_arrival_time,
        "confidence_band_min": max(1, round(remaining_min * 0.15)),
        "traffic_level": "unknown",
        "destination_name": (state.get("destination") or {}).get("name", "目的地"),
        "simulated": True,
        "flight_adjusted": flight_adjusted,
    }
