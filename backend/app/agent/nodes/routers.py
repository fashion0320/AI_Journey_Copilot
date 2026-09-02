"""LangGraph 条件边路由函数。

所有路由函数是普通 sync 函数，签名：
    def router(state: AgentState) -> str
返回目标节点名或路由 key。
"""

from __future__ import annotations

from typing import Any, Dict


def route_after_understanding(state: Dict[str, Any]) -> str:
    """understanding 节点完成后的路由。

    - journey_status=ended → END（已发送结束消息）
    - intent unknown → END（道歉结束）
    - 缺少关键信息或无目的地 → clarifying
    - intent_driven → recommending（直接推荐）
    - goal_driven + 有目的地名 → destination_confirm（先消歧）
    """
    # 如果节点明确返回 ended 状态，直接结束
    if state.get("journey_status") == "ended":
        return "unknown"  # 路由到 END

    intent = state.get("intent_type", "unknown")
    if intent == "unknown":
        return "unknown"

    missing = state.get("missing_slots") or []
    dest = (state.get("destination_name") or "").strip()

    # 如果缺目的地（目标驱动），必须追问
    if intent == "goal_driven" and not dest:
        return "clarifying"

    # 如果有其他关键缺失，追问（仅限 goal_driven 场景）
    if intent == "goal_driven" and missing and not dest:
        return "clarifying"

    # 意图驱动型 → 直接推荐（POI 推荐，缺失信息用默认值填充）
    if intent == "intent_driven":
        return "recommending"

    # 目标驱动型 + 有目的地 → 先确认目的地
    return "destination_confirm"


def route_after_clarifying(state: Dict[str, Any]) -> str:
    """clarifying 节点后的路由。

    注意：clarifying 执行完后会 interrupt，resume 时会回到 understanding
    （因为 graph.add_edge("clarifying", "understanding")）。

    但如果 clarify_count >= 2，直接 give_up 到 END。
    """
    if state.get("clarify_count", 0) >= 2:
        return "give_up"

    # interrupt resume 后，用户回复会被注入，understanding 节点会重新分析
    # clarifying → understanding 是直接边，这里的路由逻辑主要用于参考
    dest = (state.get("destination_name") or "").strip()
    intent = state.get("intent_type", "unknown")
    missing = state.get("missing_slots") or []

    if missing:
        return "continue"
    if intent == "goal_driven" and dest:
        return "destination_confirm"
    if intent == "intent_driven":
        return "recommending"
    return "continue"


def route_after_destination_confirm(state: Dict[str, Any]) -> str:
    """destination_confirm 后的路由。

    - 0 候选 → clarifying（换个说法）
    - 多候选 → clarifying（clarifying 节点会检测已有 candidates 并复用）
    - 唯一确认 → parking_confirm（直接搜停车场，跳过推荐方案阶段）
    """
    cands = state.get("destination_candidates") or []
    dest = state.get("destination") or {}

    if not cands and not dest:
        return "clarifying"
    if len(cands) > 1 and not dest.get("lat"):
        return "clarifying"
    return "parking_confirm"


def route_after_in_progress(state: Dict[str, Any]) -> str:
    """in_progress 节点后的路由。

    - 剩余 ≤ 5min → arriving
    - 有 replan_reason 或重大延误 → replanning
    - 否则 → in_progress（循环回去，配合 interrupt_after 暂停等待 GCP 触发）
    """
    eta = state.get("eta") or {}
    remaining = eta.get("remaining_min")

    # 没有 ETA 数据（还没算出 ETA），循环等待
    if remaining is None or remaining == 0 or remaining == 999:
        return "in_progress"

    if remaining <= 5:
        return "arriving"

    if state.get("replan_reason"):
        return "replanning"

    # 检查最近的提醒中是否有重大延误
    reminders = state.get("reminders") or []
    for r in reminders[-5:]:
        if isinstance(r, dict) and r.get("eta_delta_min", 0) >= 15:
            return "replanning"

    return "in_progress"
