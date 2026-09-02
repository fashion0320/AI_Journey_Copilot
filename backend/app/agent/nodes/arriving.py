"""arriving 节点：最后一公里服务。

到达前 5 分钟触发：
- 停车引导
- 下车提醒（带好随身物品等）
"""

from __future__ import annotations

from typing import Any, Dict

from langgraph.types import RunnableConfig

from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def arriving_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """到达节点。"""
    configurable = config.get("configurable", {}) if config else {}
    skill_executor = configurable.get("skill_executor")
    stream_queue = configurable.get("stream_queue")
    gcp_store = configurable.get("gcp_store")

    dest = state.get("destination") or {}
    dest_name = state.get("destination_name", "目的地")
    parking = state.get("parking") or {}
    reminders = list(state.get("reminders") or [])

    # 如果没有停车信息，重新搜索
    if not parking.get("parking_lots") and skill_executor and dest.get("lat"):
        try:
            # 判断是否是交通枢纽
            from ...skills.parking_find import TRANSIT_HUBS
            is_hub = False
            hub_key = ""
            for key, hub_info in TRANSIT_HUBS.items():
                if key in dest_name or dest_name in key:
                    is_hub = True
                    hub_key = key
                    break

            if is_hub:
                result = await skill_executor.execute_skill(
                    "parking_find", "parking_transit_hub",
                    {"hub_name": hub_key, "destination_position": dest},
                    task_id="arr_parking_hub",
                )
            else:
                result = await skill_executor.execute_skill(
                    "parking_find", "parking_search",
                    {"destination_position": dest, "radius_m": 500, "limit": 3},
                    task_id="arr_parking",
                )

            if result.get("status") == "success":
                parking = result.get("data") or {}
        except Exception as e:
            logger.error("arriving parking error: %s", e)

    # 生成到达前提醒
    arrival_msg = f"即将到达{dest_name}"
    if skill_executor:
        try:
            remind_result = await skill_executor.execute_skill(
                "smart_remind", "pre_arrival",
                {
                    "destination": dest_name,
                    "parking_info": parking,
                    "eta_min": (state.get("eta") or {}).get("remaining_min", 5),
                },
                task_id="arr_remind",
            )
            if remind_result.get("status") == "success":
                data = remind_result.get("data") or {}
                reminders.append(data)
                if data.get("tts_text"):
                    arrival_msg = data["tts_text"]
        except Exception as e:
            logger.error("arriving remind error: %s", e)

    # 停车引导文本
    parking_text = ""
    lots = parking.get("parking_lots") or []
    if lots:
        p = lots[0]
        parking_text = f"建议停{p.get('name', '附近停车场')}"
        if p.get("walk_min"):
            parking_text += f"，步行约{p['walk_min']}分钟"
        entry_hint = parking.get("entry_hint", "")
        if entry_hint:
            parking_text += f"，{entry_hint}"

    # 推送到达卡片
    if stream_queue:
        try:
            stream_queue.put_nowait(("card_update", {
                "type": "arriving",
                "data": {
                    "destination": dest_name,
                    "parking": parking_text,
                    "parking_lots": lots[:3],
                    "arrival_message": arrival_msg,
                },
            }))
            # 只发送 message，不发送 token_stream（避免双发）
            stream_queue.put_nowait(("message", {
                "role": "assistant",
                "content": arrival_msg,
            }))
        except Exception:
            pass

    # 同步 GCP
    if gcp_store:
        try:
            await gcp_store.update({"journey.status": "arriving"})
        except Exception:
            pass

    return {
        "parking": parking,
        "reminders": reminders,
        "final_response_text": arrival_msg,
        "journey_status": "arriving",
    }
