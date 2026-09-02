"""LangGraph Agent State 定义。

AgentState 是单次旅程实例在 LangGraph 图内的工作状态。
与 GCP 的 JourneyState 模型分离——GCP 是全局共享状态，
AgentState 是图内私有状态。在生命周期边界通过 Orchestrator
同步关键字段回 ContextStore。
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """LangGraph 状态机的完整状态。

    所有字段都是 optional（total=False），节点只返回自己修改的字段，
    LangGraph 自动合并 partial state dict。
    """

    # ---- 身份 ----
    journey_id: str                     # 旅程 ID（= WS 连接 ID，MVP 阶段 1连接=1旅程）
    request_id: str                     # 当前请求 ID（用于 WS 关联）

    # ---- 用户输入 ----
    user_query: str                     # 最新用户输入文本
    user_intent: str                    # 识别出的具体意图（如 airport_pickup, dining）
    intent_type: str                    # goal_driven | intent_driven | unknown
    intent_confidence: float            # 意图识别置信度 (0~1)

    # ---- LLM 对话 ----
    chat_history: Annotated[List[Dict[str, Any]], add_messages]  # Anthropic 消息格式

    # ---- GCP 上下文 ----
    gcp_snapshot: Dict[str, Any]        # 旅程开始时的完整 GCP 快照（dict 形式）

    # ---- 目的地 ----
    destination_name: str               # 原始目的地名称
    destination: Dict[str, Any]         # 解析后 {lat, lon, name, address, poi_id}
    destination_candidates: List[Dict[str, Any]]  # 消歧候选列表

    # ---- 槽位 ----
    extracted_slots: Dict[str, Any]     # 抽取的槽位（时间、人数、出行目的等）
    missing_slots: List[str]            # 缺失的关键槽位名称

    # ---- 方案层 ----
    proposals: List[Dict[str, Any]]     # 3 套差异化方案
    selected_plan_id: Optional[str]     # 用户选中的方案 ID

    # ---- 规划/执行 ----
    task_plan: List[Dict[str, Any]]     # 拆解后的子任务列表（DAG）
    route: Dict[str, Any]               # route_master 结果
    eta: Dict[str, Any]                 # dynamic_eta 结果
    reminders: List[Dict[str, Any]]     # smart_remind 结果列表
    parking: Dict[str, Any]             # parking_find 结果
    poi_results: Dict[str, Any]         # local_poi 结果
    skill_results: Dict[str, Any]       # 所有 Skill 结果汇总 {task_id: result_dict}

    # ---- 澄清 ----
    clarify_question: str               # 当前追问问题
    clarify_count: int                  # 已追问轮数（防止无限追问）
    user_clarify_reply: str             # 用户对最新追问的回复

    # ---- 行程生命周期 ----
    journey_status: str                 # 镜像 JourneyStatus 枚举值
    journey_started_at: float           # 行程开始时间戳
    replan_reason: str                  # 触发重规划的原因
    replan_count: int                   # 重规划次数

    # ---- 多轮修改 ----
    is_modification: bool               # 当前是否为修改请求（用户在已有推荐基础上提出修改）
    modification_type: str              # 修改类型：budget_change / cuisine_change / distance_change / other_preference / new_request
    modification_params: Dict[str, Any] # 修改参数（如 budget_level, max_price_per_person, cuisine_type 等）

    # ---- 输出 ----
    final_response_text: str            # 最终回复文本（用于 TTS 和消息记录）
    error: Optional[str]                # 错误信息


# ==================== 状态初始化 ====================

def init_agent_state(
    journey_id: str,
    user_query: str,
    gcp_snapshot: Dict[str, Any],
    request_id: str = "",
) -> AgentState:
    """构建初始 AgentState。"""
    return AgentState(
        journey_id=journey_id,
        request_id=request_id,
        user_query=user_query,
        chat_history=[{"role": "user", "content": user_query}],
        gcp_snapshot=gcp_snapshot,
        intent_type="unknown",
        intent_confidence=0.0,
        user_intent="",
        destination_name="",
        destination={},
        destination_candidates=[],
        extracted_slots={},
        missing_slots=[],
        proposals=[],
        selected_plan_id=None,
        task_plan=[],
        route={},
        eta={},
        reminders=[],
        parking={},
        poi_results={},
        skill_results={},
        clarify_question="",
        clarify_count=0,
        user_clarify_reply="",
        journey_status="idle",
        journey_started_at=0.0,
        replan_reason="",
        replan_count=0,
        is_modification=False,
        modification_type="",
        modification_params={},
        final_response_text="",
        error=None,
    )
