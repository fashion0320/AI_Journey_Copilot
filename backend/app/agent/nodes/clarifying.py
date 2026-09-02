"""clarifying 节点：生成澄清追问问题。

根据 missing_slots 和当前理解，生成一个最关键的追问问题。
通过 interrupt_after 机制等待用户回复。
"""

from __future__ import annotations

from typing import Any, Dict

from langgraph.types import RunnableConfig

from ..prompts import build_clarify_prompt, build_system_prompt
from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def clarifying_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """澄清追问节点。

    1. 检查追问次数，超过 3 次直接放弃
    2. 根据缺失槽位生成一个追问问题
    3. 推送澄清问题到前端
    4. （图在 interrupt_after 处暂停，等待用户回复）
    """
    configurable = config.get("configurable", {}) if config else {}
    claude = configurable.get("claude")
    stream_queue = configurable.get("stream_queue")

    count = state.get("clarify_count", 0) + 1
    missing_slots = state.get("missing_slots") or []
    gcp_snapshot = state.get("gcp_snapshot") or {}
    user_query = state.get("user_query", "")

    # 如果 destination_confirm 已经推送了带 candidates 的 clarify_question，
    # 则跳过（不重复发送追问），只更新 journey_status
    if (state.get("destination_candidates") or []) and not (state.get("destination") or {}).get("lat"):
        return {
            "clarify_count": count,
            "journey_status": "clarifying",
        }

    # 如果次数超限（最多 2 轮），生成一个放弃提示
    if count > 2:
        question = "抱歉，信息还是不够明确，您可以再详细描述一下您的需求吗？"
        if stream_queue:
            try:
                stream_queue.put_nowait(("clarify_question", {"question": question}))
            except Exception:
                pass
        return {
            "clarify_question": question,
            "clarify_count": count,
            "journey_status": "clarifying",
        }

    current_understanding = {
        "user_intent": state.get("user_intent", ""),
        "destination_name": state.get("destination_name", ""),
        "extracted_slots": state.get("extracted_slots", {}),
    }

    question = ""

    if claude:
        # 用 Claude 生成自然的追问
        sys_prompt = build_system_prompt(gcp_snapshot, user_query=user_query)
        clarify_prompt = build_clarify_prompt(
            missing_slots, current_understanding, gcp_snapshot, user_query=user_query,
        )
        messages = [{"role": "user", "content": clarify_prompt}]

        full_text_parts: list[str] = []
        try:
            async for evt in claude.chat_stream(messages, system=sys_prompt):
                evt_type = evt.type.value if hasattr(evt.type, "value") else evt.type
                if evt_type == "text_delta" and stream_queue:
                    try:
                        stream_queue.put_nowait(("token_stream", {"text": evt.text}))
                    except Exception:
                        pass
                if evt_type == "text_delta":
                    full_text_parts.append(evt.text)
            question = "".join(full_text_parts).strip()
        except Exception as e:
            logger.error("clarify claude error: %s", e)
            question = _fallback_clarify(missing_slots, current_understanding)
    else:
        question = _fallback_clarify(missing_slots, current_understanding)

    # 推送澄清问题事件
    if stream_queue:
        try:
            stream_queue.put_nowait(("clarify_question", {
                "question": question,
                "round": count,
            }))
        except Exception:
            pass

    return {
        "clarify_question": question,
        "clarify_count": count,
        "journey_status": "clarifying",
    }


def _fallback_clarify(
    missing_slots: list[str], current_understanding: Dict[str, Any],
) -> str:
    """降级的追问生成。

    注意：意图驱动场景下几乎不追问，所以这里主要处理目标驱动场景（缺目的地）。
    """
    intent = current_understanding.get("user_intent", "")

    # 如果是目标驱动且缺目的地，问目的地
    if not missing_slots or "destination" in missing_slots:
        return "请问您具体想去哪里呢？"

    # 意图驱动场景的必要追问（极少触发）
    if "cuisine_type" in missing_slots:
        if intent == "drinks":
            return "有什么偏好吗？比如清吧还是热闹点的地方？"
        return "有什么菜系偏好吗？"

    # 兜底（其他非必要槽位一般不追问，这里只做安全兜底）
    return "请问您还有什么具体要求吗？"
