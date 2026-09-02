"""completed 节点：行程收尾。

- 标记行程完成
- 保存行程历史
- 更新用户偏好（常用目的地等）
- 清理提醒
"""

from __future__ import annotations

from typing import Any, Dict

from langgraph.types import RunnableConfig

from ..state import AgentState
from ..store import get_journey_store
from ...core.logging import get_logger

logger = get_logger(__name__)


async def completed_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """完成节点：收尾工作。"""
    configurable = config.get("configurable", {}) if config else {}
    gcp_store = configurable.get("gcp_store")
    stream_queue = configurable.get("stream_queue")

    journey_id = state.get("journey_id", "")
    dest_name = state.get("destination_name", "")

    # 生成简短总结
    route = state.get("route") or {}
    eta = state.get("eta") or {}
    replan_count = state.get("replan_count", 0)

    summary = f"已到达{dest_name}"
    if route.get("distance_km"):
        summary += f"，全程{route['distance_km']}公里"
    if replan_count > 0:
        summary += f"，途中调整了{replan_count}次路线"

    # 保存到 JourneyStore
    try:
        store = get_journey_store()
        await store.complete_journey(journey_id, state)

        # 更新用户偏好
        gcp_snapshot = state.get("gcp_snapshot") or {}
        user_id = (gcp_snapshot.get("user_profile") or {}).get("id", "")
        if user_id:
            await store.update_preferences(user_id, state)
    except Exception as e:
        logger.error("save journey error: %s", e)

    # 同步 GCP
    if gcp_store:
        try:
            await gcp_store.update({"journey.status": "completed"})
        except Exception:
            pass

    # 推送完成消息
    if stream_queue:
        try:
            # 只发送 message，不发送 token_stream（避免双发）
            stream_queue.put_nowait(("message", {
                "role": "assistant",
                "content": summary + "，旅途愉快！",
            }))
        except Exception:
            pass

    logger.info("journey completed: %s → %s", journey_id, dest_name)
    return {
        "final_response_text": summary,
        "journey_status": "completed",
    }
