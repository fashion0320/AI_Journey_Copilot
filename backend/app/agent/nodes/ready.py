"""ready 节点：汇总结果 → JourneyPlan，等待用户确认出发。"""

from __future__ import annotations

from typing import Any, Dict, List

from langgraph.types import RunnableConfig

from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def ready_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """就绪节点：汇总规划结果，生成出发前总结。"""
    configurable = config.get("configurable", {}) if config else {}
    claude = configurable.get("claude")
    stream_queue = configurable.get("stream_queue")
    gcp_store = configurable.get("gcp_store")

    route = state.get("route") or {}
    eta = state.get("eta") or {}
    parking = state.get("parking") or {}
    reminders = list(state.get("reminders") or [])
    proposals = state.get("proposals") or []
    dest_name = state.get("destination_name", "目的地")
    gcp_snapshot = state.get("gcp_snapshot") or {}

    # ========== 天气提醒：出发前检查天气是否需要提醒 ==========
    if skill_executor := configurable.get("skill_executor"):
        try:
            weather_info = _extract_weather(gcp_snapshot)
            if weather_info:
                w_result = await skill_executor.execute_skill(
                    "smart_remind", "weather",
                    {"weather": weather_info, "is_driving": True},
                    task_id="ready_weather",
                )
                if w_result.get("status") == "success":
                    w_data = w_result.get("data") or {}
                    # 仅在天气有 warning/danger 级别时加入提醒列表
                    if w_data.get("severity") in ("warning", "danger"):
                        reminders.append(w_data)
                        if stream_queue:
                            try:
                                stream_queue.put_nowait(("message", {
                                    "role": "assistant",
                                    "content": w_data.get("tts_text", ""),
                                }))
                            except Exception:
                                pass
        except Exception as e:
            logger.error("ready weather remind error: %s", e)

    # 构造完整 JourneyPlan 卡片数据
    dest_pos = state.get("destination") or {}
    dest_lat = dest_pos.get("lat")
    dest_lon = dest_pos.get("lon")
    journey_plan = {
        "destination": {
            "name": dest_name,
            "lat": dest_lat,
            "lon": dest_lon,
        },
        "route": {
            "distance_km": route.get("distance_km", 0),
            "duration_min": route.get("duration_min", eta.get("remaining_min", 0)),
            "toll_cny": route.get("toll_cny", 0),
            "strategy": route.get("strategy", ""),
            "polyline": route.get("polyline", ""),
        },
        "eta": {
            "remaining_min": eta.get("remaining_min", route.get("duration_min", 0)),
            "eta_arrival_time": eta.get("eta_arrival_time", ""),
            "traffic_level": eta.get("traffic_level", "smooth"),
        },
        "parking": {
            "lots": (parking.get("parking_lots") or [])[:3],
            "recommended_index": 0,
        },
        "reminders": reminders,
    }

    # 生成出发前总结文案
    summary_text = await _generate_summary(
        claude, dest_name, route, eta, parking, reminders,
        state.get("gcp_snapshot") or {}, stream_queue,
    )

    # 推送 journey_ready 卡片
    if stream_queue:
        try:
            stream_queue.put_nowait(("card_update", {
                "type": "journey_ready",
                "data": journey_plan,
            }))
        except Exception:
            pass

        # 推送最终消息
        try:
            stream_queue.put_nowait(("message", {
                "role": "assistant",
                "content": summary_text,
            }))
        except Exception:
            pass

    # 同步到 GCP
    if gcp_store:
        try:
            await gcp_store.update({
                "journey.destination_name": dest_name,
                "journey.route.distance_km": route.get("distance_km", 0),
                "journey.route.duration_min": route.get("duration_min", 0),
                "journey.eta_remaining_min": eta.get("remaining_min", 0),
            })
        except Exception:
            pass

    return {
        "final_response_text": summary_text,
        "journey_status": "ready",
        "eta": eta if eta else {"remaining_min": route.get("duration_min", 0)},
    }


async def _generate_summary(
    claude: Any,
    dest_name: str,
    route: Dict[str, Any],
    eta: Dict[str, Any],
    parking: Dict[str, Any],
    reminders: list,
    gcp_snapshot: Dict[str, Any],
    stream_queue: Any,
) -> str:
    """生成出发前总结文案。"""
    duration = route.get("duration_min", 0) or eta.get("remaining_min", 0)
    distance = route.get("distance_km", 0)

    # 没有 Claude 时的降级文案
    if not claude:
        parking_text = ""
        lots = parking.get("parking_lots") or []
        if lots:
            p = lots[0]
            parking_text = f"，推荐停{p.get('name', '附近停车场')}"
        return f"好的，前往{dest_name}的路线已规划好，预计{duration}分钟，{distance}公里{parking_text}。准备好出发了吗？"

    # 用 Claude 生成自然语言总结
    reminder_text = ""
    for r in reminders[:2]:
        if isinstance(r, dict) and r.get("tts_text"):
            reminder_text += f"- {r['tts_text']}\n"

    prompt = f"""请根据以下出行方案，生成一段简洁的出发前总结，适合语音播报。

目的地：{dest_name}
预计用时：{duration} 分钟
距离：{distance} 公里
停车信息：{(parking.get('parking_lots') or [{}])[0].get('name', '暂无') if parking.get('parking_lots') else '暂无'}
提醒：
{reminder_text}

要求：
- 2-3 句话，简洁自然
- 适合语音播报，不要复杂标点
- 结尾询问是否准备好出发
- 中文"""

    messages = [{"role": "user", "content": prompt}]
    full_text_parts: list[str] = []
    try:
        async for evt in claude.chat_stream(messages, system="你是一位简洁友好的座舱助手。"):
            evt_type = evt.type.value if hasattr(evt.type, "value") else evt.type
            if evt_type == "text_delta" and stream_queue:
                try:
                    stream_queue.put_nowait(("token_stream", {"text": evt.text}))
                except Exception:
                    pass
            if evt_type == "text_delta":
                full_text_parts.append(evt.text)
        result = "".join(full_text_parts).strip()
        if result:
            return result
    except Exception as e:
        logger.error("ready summary error: %s", e)

    return f"前往{dest_name}约{duration}分钟，{distance}公里。准备好出发了吗？"


def _extract_weather(gcp_snapshot: Dict[str, Any]) -> Dict[str, Any] | None:
    """从 GCP 快照中提取天气信息。"""
    if not gcp_snapshot:
        return None
    weather_ctx = gcp_snapshot.get("weather") or {}
    live = weather_ctx.get("live") or {}
    if not live.get("weather"):
        return None
    return {
        "weather": live.get("weather", ""),
        "temperature": live.get("temperature", 0),
        "windpower": live.get("windpower", ""),
    }
