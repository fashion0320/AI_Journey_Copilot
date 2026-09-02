"""replanning 节点：增量重规划。

根据 replan_reason，只重新执行受影响的 Skills，
对比新旧方案，生成变化说明。
"""

from __future__ import annotations

from typing import Any, Dict, List

from langgraph.types import RunnableConfig

from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def replanning_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """重规划节点。"""
    configurable = config.get("configurable", {}) if config else {}
    skill_executor = configurable.get("skill_executor")
    stream_queue = configurable.get("stream_queue")
    gcp_store = configurable.get("gcp_store")

    replan_reason = state.get("replan_reason", "")
    replan_count = state.get("replan_count", 0) + 1

    updates: Dict[str, Any] = {
        "replan_count": replan_count,
        "replan_reason": "",  # 消费掉，清空
        "journey_status": "replanning",
    }

    if not skill_executor:
        return updates

    dest = state.get("destination") or {}
    route = state.get("route") or {}
    gcp_snapshot = state.get("gcp_snapshot") or {}

    # 从 GCP 获取当前车辆位置（重规划需要当前位置）
    current_pos = None
    veh_pos = (gcp_snapshot.get("vehicle") or {}).get("position") or {}
    if veh_pos.get("lat"):
        current_pos = {"lat": float(veh_pos["lat"]), "lon": float(veh_pos["lon"])}
    # 兜底：用 route 里的 origin 或 陆家嘴
    if not current_pos:
        current_pos = {"lat": 31.2359, "lon": 121.4996}

    # 当前剩余距离/时间（用于重规划 context）
    remaining_ctx = {}
    eta = state.get("eta") or {}
    if eta.get("remaining_min") is not None:
        remaining_ctx["remaining_duration_min"] = eta["remaining_min"]
    if route.get("distance_km"):
        # 粗略估算剩余距离（按 ETA 比例）
        total_dur = route.get("duration_min") or eta.get("remaining_min", 30)
        remaining_dur = eta.get("remaining_min", total_dur)
        if total_dur > 0:
            remaining_ctx["remaining_distance_km"] = round(
                route["distance_km"] * remaining_dur / total_dur, 2
            )

    # 根据原因判断需要重算什么
    skills_to_rerun: List[Dict[str, Any]] = []

    if any(kw in replan_reason for kw in ["拥堵", "事故", "封路", "ETA变化", "路线"]):
        # 路线相关变化 → 重算路线 + ETA
        if dest.get("lat"):
            skills_to_rerun.append({
                "task_id": "reroute",
                "skill": "route_master",
                "action": "route_reroute",
                "params": {
                    "current_position": current_pos,
                    "origin": current_pos,
                    "destination": {"lat": dest["lat"], "lon": dest["lon"]},
                    "reason": replan_reason,
                    "context": remaining_ctx,
                    "strategy": route.get("strategy", "time_first"),
                },
                "parallel_group": "replan",
            })
            # 并行重算 ETA
            skills_to_rerun.append({
                "task_id": "eta_refresh",
                "skill": "dynamic_eta",
                "action": "eta_query",
                "params": {
                    "current_position": current_pos,
                    "destination": {"lat": dest["lat"], "lon": dest["lon"]},
                },
                "parallel_group": "replan",
            })

    if any(kw in replan_reason for kw in ["航班", "延误", "取消", "接机"]):
        # 航班变化 → 更新提醒，从 GCP 读取实际航班数据
        flight_info = {}
        if gcp_store:
            try:
                transit = gcp_store.get("transit") or {}
                if hasattr(transit, "model_dump"):
                    flight_info = transit.model_dump()
                elif isinstance(transit, dict):
                    flight_info = transit
            except Exception:
                flight_info = {}
        skills_to_rerun.append({
            "task_id": "flight_update",
            "skill": "smart_remind",
            "action": "transit_dynamic",
            "params": {
                "reason": replan_reason,
                "flight_no": flight_info.get("flight_no", ""),
                "flight_status": flight_info.get("status", ""),
                "delay_min": flight_info.get("delay_min", 0),
                "sta": flight_info.get("sta", ""),
                "ata": flight_info.get("ata", ""),
                "terminal": flight_info.get("terminal", ""),
            },
            "parallel_group": "replan",
        })

    if any(kw in replan_reason for kw in ["天气", "暴雨", "雷雨", "大风"]):
        # 天气变化 → 天气提醒
        skills_to_rerun.append({
            "task_id": "weather_alert",
            "skill": "smart_remind",
            "action": "weather",
            "params": {},
            "parallel_group": "replan",
        })

    if not skills_to_rerun:
        # 无明确原因，至少重算 ETA
        skills_to_rerun.append({
            "task_id": "eta_refresh",
            "skill": "dynamic_eta",
            "action": "eta_query",
            "params": {},
            "parallel_group": "",
        })

    # 执行重规划
    results = await skill_executor.execute_plan(skills_to_rerun)

    # 更新相关字段 — 归一化 reroute 结果字段名
    new_route = results.get("reroute") or {}
    normalized_route = None
    if isinstance(new_route, dict) and new_route.get("status") == "success":
        rd = new_route.get("data") or {}
        if rd:
            # 归一化：route_reroute 返回 new_distance_km / new_duration_min
            # 统一为 distance_km / duration_min，与 route_single 结果一致
            normalized_route = {
                "route_id": rd.get("route_id"),
                "distance_km": rd.get("new_distance_km", rd.get("distance_km", 0)),
                "duration_min": rd.get("new_duration_min", rd.get("duration_min", 0)),
                "toll_cny": rd.get("toll_cny", route.get("toll_cny", 0)),
                "polyline": rd.get("polyline", ""),
                "strategy": route.get("strategy", rd.get("strategy", "time_first")),
                "replan_reason": rd.get("reason", replan_reason),
                "delta_distance_km": rd.get("delta_distance_km"),
                "delta_duration_min": rd.get("delta_duration_min"),
                "is_faster": rd.get("is_faster"),
            }
            updates["route"] = normalized_route
            # 推送路线变化卡片
            if stream_queue:
                try:
                    stream_queue.put_nowait(("card_update", {
                        "type": "route",
                        "data": normalized_route,
                    }))
                except Exception:
                    pass

    # ETA 更新
    eta_result = results.get("eta_refresh") or {}
    if isinstance(eta_result, dict) and eta_result.get("data"):
        updates["eta"] = eta_result["data"]
    elif normalized_route:
        # 如果只有 reroute 成功但 ETA 查询失败，用路线数据兜底
        updates["eta"] = {
            "remaining_min": normalized_route.get("duration_min", 30),
            "eta_arrival_time": "",
            "confidence_band_min": 5,
            "traffic_level": "unknown",
        }

    # 追加新提醒
    new_reminders = []
    for tid, res in results.items():
        if tid in ("flight_update", "weather_alert"):
            if isinstance(res, dict) and res.get("data"):
                new_reminders.append(res["data"])
                # 推送提醒消息（只发 message，不发 token_stream 避免双发）
                if stream_queue and res["data"].get("tts_text"):
                    try:
                        stream_queue.put_nowait(("message", {
                            "role": "assistant",
                            "content": res["data"]["tts_text"],
                        }))
                    except Exception:
                        pass

    if new_reminders:
        existing = list(state.get("reminders") or [])
        existing.extend(new_reminders)
        updates["reminders"] = existing

    # 生成变化说明
    old_duration = route.get("duration_min", 0)
    new_duration = (updates.get("route") or {}).get("duration_min", old_duration)
    delta = new_duration - old_duration if new_duration else 0

    if abs(delta) >= 5 and stream_queue:
        msg = f"路线已更新，预计"
        if delta > 0:
            msg += f"多花{delta}分钟"
        else:
            msg += f"节省{-delta}分钟"
        try:
            stream_queue.put_nowait(("message", {
                "role": "assistant",
                "content": msg,
            }))
        except Exception:
            pass

    logger.info("replanning done, reason=%s, count=%d", replan_reason, replan_count)
    return updates
