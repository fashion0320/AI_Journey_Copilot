"""LangGraph 状态图构建。

使用 StateGraph 构建完整的旅程状态机，包含 10 个节点和条件边路由。
使用 MemorySaver 作为 checkpointer，支持 interrupt/resume。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from .nodes import (
    arriving_node,
    clarifying_node,
    completed_node,
    destination_confirm_node,
    in_progress_node,
    parking_confirm_node,
    planning_node,
    recommending_node,
    replanning_node,
    route_after_destination_confirm,
    route_after_in_progress,
    route_after_understanding,
    understanding_node,
)
from .state import AgentState
from .store import get_journey_store
from .skill_executor import SkillExecutor
from ..core.logging import get_logger

logger = get_logger(__name__)


def build_journey_graph() -> StateGraph:
    """构建完整的旅程状态机 StateGraph。

    注意：返回的是未编译的 StateGraph，调用方负责 compile。
    compile 时需要传入 checkpointer（从 JourneyStore 获取）。
    """
    g = StateGraph(AgentState)

    # 注册所有节点
    g.add_node("understanding", understanding_node)
    g.add_node("clarifying", clarifying_node)
    g.add_node("destination_confirm", destination_confirm_node)
    g.add_node("recommending", recommending_node)
    g.add_node("parking_confirm", parking_confirm_node)
    g.add_node("planning", planning_node)
    g.add_node("in_progress", in_progress_node)
    g.add_node("replanning", replanning_node)
    g.add_node("arriving", arriving_node)
    g.add_node("completed", completed_node)

    # 入口
    g.add_edge(START, "understanding")

    # understanding 之后的条件路由
    g.add_conditional_edges(
        "understanding",
        route_after_understanding,
        {
            "clarifying": "clarifying",
            "destination_confirm": "destination_confirm",
            "recommending": "recommending",
            "unknown": END,
        },
    )

    # clarifying 之后 → 回到 understanding 重新分析用户回复
    # clarifying 节点在 interrupt_after 列表中，生成追问后暂停，
    # 用户回复后 resume，understanding 会结合新回复重新理解意图
    g.add_edge("clarifying", "understanding")

    # 注意：clarify_count >= 2 的 give_up 逻辑在 understanding 节点开头检查，
    # 如果超限直接返回 intent_type="unknown" → route_after_understanding → END

    # destination_confirm 之后的条件路由
    # 目标驱动型：destination 确认后 → parking_confirm（直接搜停车场）
    g.add_conditional_edges(
        "destination_confirm",
        route_after_destination_confirm,
        {
            "clarifying": "clarifying",
            "parking_confirm": "parking_confirm",
        },
    )

    # 意图驱动型：recommending → parking_confirm（用户选方案后搜停车场）
    # interrupt_after=["recommending"] 在 compile 时配置
    g.add_edge("recommending", "parking_confirm")

    # parking_confirm → planning（用户确认出发后 resume，进行路线规划+ETA计算）
    # interrupt_after=["parking_confirm"] 在 compile 时配置
    g.add_edge("parking_confirm", "planning")

    # planning → in_progress（路线规划完直接进入行程进行中，无需再次确认）
    g.add_edge("planning", "in_progress")

    # in_progress 之后的条件路由
    # continue → 回到 in_progress（配合 interrupt_after 实现等待-唤醒循环）
    g.add_conditional_edges(
        "in_progress",
        route_after_in_progress,
        {
            "in_progress": "in_progress",  # 循环：等待下一次 GCP 触发
            "replanning": "replanning",
            "arriving": "arriving",
        },
    )

    # replanning → in_progress
    g.add_edge("replanning", "in_progress")

    # arriving → completed
    g.add_edge("arriving", "completed")

    # completed → END
    g.add_edge("completed", END)

    return g


def compile_journey_graph(
    checkpointer: Optional[Any] = None,
    interrupt_after: Optional[list[str]] = None,
):
    """编译旅程图，带 checkpointer 和 interrupt 配置。

    Args:
        checkpointer: LangGraph checkpointer，默认从 JourneyStore 获取
        interrupt_after: 在哪些节点后中断等待用户输入，默认 ["clarifying", "recommending", "parking_confirm", "in_progress"]

    Returns:
        编译后的 LangGraph
    """
    if checkpointer is None:
        store = get_journey_store()
        checkpointer = store.get_checkpointer()

    if interrupt_after is None:
        interrupt_after = ["clarifying", "recommending", "parking_confirm", "in_progress"]

    graph = build_journey_graph()
    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_after=interrupt_after,
    )
    logger.info("journey graph compiled, interrupt_after=%s", interrupt_after)
    return compiled


def make_graph_config(
    journey_id: str,
    stream_queue: Any = None,
    claude: Any = None,
    skill_executor: Any = None,
    gcp_store: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造 LangGraph config（含 thread_id + 可配置依赖）。

    节点通过 config["configurable"] 获取这些依赖。
    """
    cfg = get_journey_store().get_config(journey_id)
    cfg["configurable"]["stream_queue"] = stream_queue
    cfg["configurable"]["claude"] = claude
    cfg["configurable"]["skill_executor"] = skill_executor
    cfg["configurable"]["gcp_store"] = gcp_store
    if extra:
        cfg["configurable"].update(extra)
    return cfg
