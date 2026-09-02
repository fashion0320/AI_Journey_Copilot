# Sprint 4：LangGraph Agent + Orchestrator 实现计划

## Context

Sprint 1（项目骨架 + GCP）、Sprint 2（CPSP Adapters：高德、豆包语音、Claude）、Sprint 3（5 个核心 Skills）均已完成。`app/agent/` 目录当前为空，`/ws/chat` 目前只是简单的 Claude 流式对话。

Sprint 4 是整个 MVP 的核心：构建基于 LangGraph 的 Agent 状态机 + Orchestrator，将 GCP 上下文、Claude LLM、5 个 Skill 串联成完整的「意图理解→方案推荐→任务规划→行程执行→动态重规划」全链路能力。

**目标**：用户说一句「帮我安排一下今晚去虹桥机场接客户，飞机 8 点到」，Agent 能自动完成意图识别、目的地确认、路线+停车+提醒的多 Skill 编排，并在行程中监听 GCP 变化做动态重规划。

---

## 架构设计

### 整体分层

```
WebSocket 层 (app/api/ws.py)
    ↓ 接收用户输入 / 推送流式事件
Orchestrator 层 (app/agent/orchestrator.py)    ← 管理旅程实例、桥接 LangGraph 与 WS
LangGraph 状态机 (app/agent/graph.py + nodes/)
    ↓ 节点通过 stream_queue 推送 Claude 流式 token + Skill 事件
Skill Executor (app/agent/skill_executor.py)   ← 串并行编排 Skill
Skill 层 (app/skills/)  ←→  GCP ContextStore (app/gcp/store.py)
```

### 文件结构

```
app/agent/
├── __init__.py            # 导出 JourneyOrchestrator、build_journey_graph 等
├── state.py               # AgentState TypedDict + 工具函数
├── graph.py               # LangGraph StateGraph 构建（节点 + 边 + 路由）
├── orchestrator.py        # JourneyOrchestrator：WS ↔ LangGraph 桥梁（核心）
├── skill_executor.py      # SkillExecutor：串行/并行/DAG 执行 + 结果汇总
├── store.py               # JourneyStore：行程内存存储 + MemorySaver 包装
├── prompts.py             # 所有 Prompt 模板函数（system/intent/clarify/recommend/plan/replan）
├── nodes/
│   ├── __init__.py
│   ├── understanding.py   # 意图识别 + 信息抽取
│   ├── clarifying.py      # 生成追问问题
│   ├── destination_confirm.py  # 目的地消歧确认
│   ├── recommending.py    # 生成 3 套差异化方案
│   ├── planning.py        # 任务拆解 → Skill 编排执行
│   ├── ready.py           # 汇总结果 → JourneyPlan
│   ├── in_progress.py     # 行程进行中（ETA 更新）
│   ├── replanning.py      # 增量重规划
│   ├── arriving.py        # 最后一公里服务（停车引导+下车提醒）
│   └── completed.py       # 收尾（保存行程、更新偏好、清理）
```

---

## 1. AgentState（LangGraph State）

### 文件：`app/agent/state.py`

LangGraph 使用的 `TypedDict` 状态。**与 GCP 的 `JourneyState` 模型分离**——GCP 是全局共享状态，AgentState 是单次旅程实例的图内工作状态。在生命周期边界（ready、in_progress、completed）通过 `_sync_to_gcp()` 同步关键字段回 ContextStore。

```python
from typing import TypedDict, List, Dict, Any, Optional, Annotated
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    # ---- 身份 ----
    journey_id: str                     # = WS 连接 ID（MVP 阶段 1连接=1旅程）
    request_id: str                     # 当前请求 ID（用于 WS 关联）

    # ---- 用户输入 ----
    user_query: str                     # 最新用户输入文本
    user_intent: str                    # 识别出的具体意图（如 airport_pickup, dining）
    intent_type: str                    # goal_driven | intent_driven | unknown
    intent_confidence: float            # 意图识别置信度

    # ---- LLM 对话 ----
    chat_history: Annotated[List[Dict[str, Any]], add_messages]  # Anthropic 消息格式

    # ---- GCP 上下文 ----
    gcp_snapshot: Dict[str, Any]        # 旅程开始时的完整 GCP 快照（dict 形式）

    # ---- 目的地 ----
    destination_name: str               # 原始目的地名称
    destination: Dict[str, Any]         # 解析后 {lat, lon, name, address, poi_id}
    destination_candidates: List[Dict[str, Any]]  # 消歧候选列表
    extracted_slots: Dict[str, Any]     # 抽取的其他槽位（时间、人数、出行目的等）
    missing_slots: List[str]            # 缺失的关键槽位

    # ---- 方案层 ----
    proposals: List[Dict[str, Any]]     # 3 套差异化方案
    selected_plan_id: Optional[str]     # 用户选中的方案 ID

    # ---- 规划/执行 ----
    task_plan: List[Dict[str, Any]]     # 拆解后的子任务列表
    route: Dict[str, Any]               # route_master 结果
    eta: Dict[str, Any]                 # dynamic_eta 结果
    reminders: List[Dict[str, Any]]     # smart_remind 结果（列表）
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

    # ---- 输出 ----
    final_response_text: str            # 最终回复文本（用于 TTS 和消息记录）
    error: Optional[str]                # 错误信息
```

**关键设计**：
- 使用 LangGraph 的 `add_messages` reducer 管理 `chat_history`
- 每个节点只返回自己修改的字段（partial state dict），LangGraph 自动合并
- `gcp_snapshot` 在旅程开始时取一次快照；节点需要实时 GCP 时直接从 `ContextStore` 读取
- `_stream_queue`、`claude_client`、`skill_executor` 等**非序列化对象**通过 `config["configurable"]` 注入，不放入 state

---

## 2. LangGraph 状态图

### 文件：`app/agent/graph.py`

使用 `langgraph.graph.StateGraph` 构建状态机，`MemorySaver` 做 checkpoint（通过 JourneyStore 包装）。

### 中断点（Interrupt）

使用 `interrupt_after` 让图在需要用户输入的节点执行完后暂停：

```python
graph = build_journey_graph().compile(
    checkpointer=journey_store.get_checkpointer(),
    interrupt_after=["clarifying", "recommending", "ready"],
)
```

三个中断点：
1. **clarifying 之后**：等待用户回复追问问题
2. **recommending 之后**：等待用户选择方案（或通过对话确认推荐方案）
3. **ready 之后**：等待用户确认出发

用户回复后，通过 `graph.aupdate_state()` 注入回复，然后 `graph.astream(None, config)` 恢复执行。

### 节点与路由总览

```
START → understanding
           │ route_after_understanding()
           ├─ intent unknown → END（道歉结束）
           ├─ 缺少关键信息 → clarifying ──(interrupt)──→ 用户回复
           │                                                         │
           │                                             understanding（resume）
           ├─ goal_driven + 目的地需消歧 → destination_confirm
           │                                           │ route_after_dest_confirm()
           │                                           ├─ 0 候选 → clarifying
           │                                           ├─ 多候选 → clarifying（问选哪个）
           │                                           └─ 唯一 → recommending
           └─ intent_driven / 信息充足 → recommending
                                                  │
                                                  ├─ (interrupt) 等用户选方案
                                                  │           │
                                                  └─ planning ←─ (resume with selected_plan_id)
                                                        │
                                                      ready
                                                        │ (interrupt 等确认出发)
                                                  in_progress ← (resume 确认出发)
                                                   │    │    │
                                route_after_in_progress()  │
                                                   │    │    └─ 正常 → 停留在 in_progress
                                                   │    │        （外部 GCP 监听触发重新进入）
                                                   │    ├─ ETA 变化大/交通严重/航班变动 → replanning
                                                   │    │                                    │
                                                   │    └─────────────────────────→ in_progress
                                                   └─ 即将到达（剩余 < 5min）→ arriving
                                                                          │
                                                                      completed
                                                                          │
                                                                         END
```

### 构建代码

```python
from langgraph.graph import StateGraph, START, END
from .state import AgentState
from .nodes import (
    understanding_node, clarifying_node, destination_confirm_node,
    recommending_node, planning_node, ready_node,
    in_progress_node, replanning_node, arriving_node, completed_node,
)
from .nodes.routers import (
    route_after_understanding, route_after_clarifying,
    route_after_destination_confirm, route_after_in_progress,
)

def build_journey_graph() -> StateGraph:
    g = StateGraph(AgentState)

    # 注册所有节点
    g.add_node("understanding", understanding_node)
    g.add_node("clarifying", clarifying_node)
    g.add_node("destination_confirm", destination_confirm_node)
    g.add_node("recommending", recommending_node)
    g.add_node("planning", planning_node)
    g.add_node("ready", ready_node)
    g.add_node("in_progress", in_progress_node)
    g.add_node("replanning", replanning_node)
    g.add_node("arriving", arriving_node)
    g.add_node("completed", completed_node)

    # 入口
    g.add_edge(START, "understanding")

    # 条件边
    g.add_conditional_edges("understanding", route_after_understanding, {
        "clarifying": "clarifying",
        "destination_confirm": "destination_confirm",
        "recommending": "recommending",
        "unknown": END,
    })
    g.add_conditional_edges("clarifying", route_after_clarifying, {
        "continue": "clarifying",
        "destination_confirm": "destination_confirm",
        "recommending": "recommending",
        "give_up": END,
    })
    g.add_conditional_edges("destination_confirm", route_after_destination_confirm, {
        "clarifying": "clarifying",
        "recommending": "recommending",
    })
    g.add_edge("recommending", "planning")
    g.add_edge("planning", "ready")
    g.add_edge("ready", "in_progress")  # interrupt 后 resume 自动进入
    g.add_conditional_edges("in_progress", route_after_in_progress, {
        "continue": END,  # 暂停，等外部 GCP 事件触发重新进入
        "replanning": "replanning",
        "arriving": "arriving",
    })
    g.add_edge("replanning", "in_progress")
    g.add_edge("arriving", "completed")
    g.add_edge("completed", END)

    return g
```

> **注意**：`in_progress` 节点路由返回 `"continue"` 时走向 `END`——这不是结束旅程，而是让 graph 执行暂停（不是 interrupt，而是自然结束 stream）。外部的 GCP 监听协程检测到变化时，通过 `graph.aupdate_state()` 更新状态 + `graph.astream(None, config)` 重新从 `in_progress` 节点执行。这避免了在图内做 busy loop。

---

## 3. 节点详细设计

### 节点通用模式

每个节点是一个 async 函数，签名：
```python
async def xxx_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
```

从 `config["configurable"]` 获取依赖：
- `stream_queue: asyncio.Queue` —— 推送流式事件
- `claude: ClaudeClient` —— LLM 客户端
- `skill_executor: SkillExecutor` —— Skill 执行器

返回值是要更新的 state 字段字典。

节点内部调用 Claude 时，通过 stream_queue 推送 `("token_stream", {"text": ...})` 事件，实现 token 级流式输出。

---

### 3.1 understanding 节点

**文件**：`app/agent/nodes/understanding.py`

**功能**：
1. 从 ContextStore 取最新 GCP 快照（更新 state.gcp_snapshot）
2. 构造意图识别 prompt（使用 `prompts.build_intent_prompt()`）
3. 调用 Claude（非流式或流式），支持 tool-use 调用地理编码（amap.geocode）
4. 解析返回的 JSON：`{intent_type, user_intent, confidence, destination_name, slots, missing_slots}`
5. 判断是 goal_driven 还是 intent_driven
6. 返回更新字段

**输出字段**：`intent_type, intent_confidence, user_intent, destination_name, extracted_slots, missing_slots, journey_status="understanding"`

### 3.2 clarifying 节点

**文件**：`app/agent/nodes/clarifying.py`

**功能**：
1. 检查 `clarify_count`，若 ≥ 3 直接返回 `give_up`（由路由判断）
2. 根据 `missing_slots` 和当前理解，调用 Claude 生成 1 个最关键的追问
3. 通过 stream_queue 推送 `("clarify_question", {"text": 问题, "options": 可选选项})`
4. 流式推送问题文本作为 TOKEN_STREAM

**输出字段**：`clarify_question, clarify_count=(+1), journey_status="clarifying"`

### 3.3 destination_confirm 节点

**文件**：`app/agent/nodes/destination_confirm.py`

**功能**：
1. 取 `destination_name`，调用 `local_poi.poi_resolve` 或 `amap.place_text` 获取候选
2. 唯一匹配 → 直接写入 `destination` 字典（lat/lon/name/address）
3. 多候选（2-5个）→ 写入 `destination_candidates`，生成选择问题
4. 无结果 → 返回空 candidates，由路由回到 clarifying

**输出字段**：`destination, destination_candidates, journey_status="destination_confirm"`

### 3.4 recommending 节点

**文件**：`app/agent/nodes/recommending.py`

**功能**：
1. 根据 `intent_type` 分支：
   - **goal_driven**：并行调用 `route_master.route_single` 3种策略 + `parking_find.parking_search`
   - **intent_driven**：调用 `local_poi.poi_recommend` 得到 3个 POI → 对每个 POI 并行调路线规划
2. 通过 SkillExecutor.execute_parallel 执行
3. 调用 Claude 整合 Skill 结果为 3 套差异化方案
4. 推送 `("card_update", {"type": "proposals", "data": proposals})`
5. 流式推送推荐描述文本

**输出字段**：`proposals, journey_status="recommending"`

**方案格式**：
```json
{
  "id": "plan_1",
  "title": "最快路线 · 虹桥T2直达",
  "summary": "走延安高架，约35分钟，15公里",
  "eta_min": 35,
  "distance_km": 15.2,
  "toll_cny": 10,
  "strategy": "time_first",
  "parking_hint": "P6停车场最近",
  "pros": ["最快到达", "高速为主"],
  "cons": ["晚高峰可能拥堵", "有过路费"],
  "reason": "考虑到您接机时间紧张，推荐最快路线",
  "source": "amap_route_v5"
}
```

### 3.5 planning 节点

**文件**：`app/agent/nodes/planning.py`

**功能**：
1. 根据 `selected_plan_id` 选方案（默认选推荐的第1个）
2. 调用 Claude 做任务拆解（使用 `prompts.build_plan_decompose_prompt()`），输出 DAG 任务列表：
   ```json
   [{"task_id":"t1","skill":"route_master","action":"route_single",
     "params":{...},"deps":[],"parallel_group":"g1"},
    {"task_id":"t2","skill":"parking_find","action":"parking_search",
     "params":{...},"deps":[],"parallel_group":"g1"},
    {"task_id":"t3","skill":"smart_remind","action":"pre_departure",
     "params":{...},"deps":["t1","t2"],"parallel_group":""}]
   ```
3. 调用 `SkillExecutor.execute_plan(task_plan)` 执行
4. 汇总结果到各字段（route/eta/parking/poi_results/reminders）
5. 同步关键结果到 GCP ContextStore

**输出字段**：`task_plan, route, eta, parking, poi_results, reminders, skill_results, journey_status="planning"`

### 3.6 ready 节点

**文件**：`app/agent/nodes/ready.py`

**功能**：
1. 汇总 route+eta+parking+reminders 为完整 JourneyPlan
2. 调用 Claude 生成自然语言出发前总结
3. 推送 `("card_update", {"type": "journey_ready", "data": 完整计划})`
4. 推送 `("state_change", {"status": "ready"})`
5. 流式推送总结文本

**输出字段**：`final_response_text, journey_status="ready"`

### 3.7 in_progress 节点

**文件**：`app/agent/nodes/in_progress.py`

**功能**（每次被触发时执行一次，不循环）：
1. 若是首次进入（`journey_started_at == 0`）：记录开始时间，同步 GCP 状态为 IN_PROGRESS
2. 取最新车辆位置，调用 `dynamic_eta.eta_query` 更新 ETA
3. 根据 GCP 变化判断是否需要生成 in_journey 提醒
4. 更新 state 中的 eta 和 reminders

**输出字段**：`eta, reminders (增量), journey_status="in_progress", journey_started_at`

**路由函数 `route_after_in_progress`**：
- ETA 剩余 ≤ 5min → `"arriving"`
- replan_reason 非空 或 ETA 变化 ≥ 15min 或严重天气/交通 → `"replanning"`
- 否则 → `"continue"`（暂停执行，等待外部触发）

### 3.8 replanning 节点

**文件**：`app/agent/nodes/replanning.py`

**功能**：
1. 根据 `replan_reason` 判断受影响的 Skill（增量重规划）
   - 拥堵/事故 → `route_master.route_reroute` + `dynamic_eta`
   - 航班变动 → `dynamic_eta` + `smart_remind.transit_dynamic`
   - 天气恶化 → `smart_remind.weather`
2. 调用 SkillExecutor 重算受影响部分
3. 对比新旧方案，生成变化说明（如有大变化推送提醒）
4. 更新 route/eta/reminders
5. 推送 `("card_update", {"type": "replan", "data": {new_route, delta}})`
6. 清空 replan_reason

**输出字段**：`route, eta, reminders, replan_count=(+1), replan_reason="", journey_status="replanning"`

### 3.9 arriving 节点

**文件**：`app/agent/nodes/arriving.py`

**功能**：
1. 若停车信息缺失/过时，调用 `parking_find` 重新获取
2. 调用 `smart_remind.pre_arrival` 生成到达前提醒
3. 生成停车引导文案（入口、步行路线）
4. 推送 `("card_update", {"type": "arriving", "data": {parking, tip}})`
5. 流式推送到达提醒

**输出字段**：`parking, reminders (追加), final_response_text, journey_status="arriving"`

### 3.10 completed 节点

**文件**：`app/agent/nodes/completed.py`

**功能**：
1. 生成简短行程总结
2. 保存行程记录到 JourneyStore
3. 更新用户偏好（如常用目的地、路线策略偏好权重）
4. 同步 GCP journey 状态为 COMPLETED
5. Orchestrator 清理 GCP 监听任务

**输出字段**：`final_response_text, journey_status="completed"`

---

## 4. 路由函数

### 文件：`app/agent/nodes/routers.py`

所有路由函数是普通 sync 函数，签名 `def router(state: AgentState) -> str`，返回目标节点名或路由 key。

```python
def route_after_understanding(state: AgentState) -> str:
    intent = state.get("intent_type", "unknown")
    if intent == "unknown":
        return "unknown"
    missing = state.get("missing_slots", [])
    dest = state.get("destination_name", "").strip()
    if missing or not dest:
        return "clarifying"
    if intent == "intent_driven":
        return "recommending"
    # goal_driven + 有目的地 → 先消歧
    return "destination_confirm"

def route_after_clarifying(state: AgentState) -> str:
    if state.get("clarify_count", 0) >= 3:
        return "give_up"
    # 用户回复后回到 understanding 重新理解（通过 clarifying→understanding 边？）
    # 实际上：resume 后 clarifying 节点检查用户回复，
    # 如果信息已足够，设置正确的下一跳
    # 简化：clarifying 节点更新 destination_name/extracted_slots，
    # 路由判断是否还需要消歧
    dest = state.get("destination_name", "").strip()
    intent = state.get("intent_type", "unknown")
    if state.get("missing_slots"):
        return "continue"
    if intent == "goal_driven" and dest:
        return "destination_confirm"
    if intent == "intent_driven":
        return "recommending"
    return "continue"

def route_after_destination_confirm(state: AgentState) -> str:
    cands = state.get("destination_candidates", [])
    dest = state.get("destination", {})
    if not cands and not dest:
        return "clarifying"
    if len(cands) > 1 and not dest:
        return "clarifying"
    return "recommending"

def route_after_in_progress(state: AgentState) -> str:
    eta = state.get("eta", {})
    remaining = eta.get("remaining_min", 999)
    if remaining <= 5:
        return "arriving"
    if state.get("replan_reason"):
        return "replanning"
    # 检查 reminders 中是否有重大延误提醒
    for r in state.get("reminders", [])[-5:]:
        if r.get("eta_delta_min", 0) >= 15:
            return "replanning"
    return "continue"
```

---

## 5. SkillExecutor

### 文件：`app/agent/skill_executor.py`

负责 Skill 的串并行编排和结果汇总，同时处理 GCP 切片和事件推送。

```python
class SkillExecutor:
    def __init__(
        self,
        gcp_store: "ContextStore",
        stream_queue: Optional[asyncio.Queue] = None,
    ):
        self.gcp_store = gcp_store
        self.stream_queue = stream_queue
        self._skills = get_all_skills()

    async def execute_skill(
        self, skill_name: str, action: str, params: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """执行单个 Skill。"""
        skill = self._skills.get(skill_name)
        if skill is None:
            return SkillResult.error(f"skill '{skill_name}' not found").to_dict()

        # 推送 SKILL_START
        if self.stream_queue:
            await self.stream_queue.put(("skill_start", {"skill": skill_name, "action": action}))

        # 取 GCP 切片
        gcp_dict = self.gcp_store.to_dict()
        gcp_slice = skill.extract_gcp_slice(gcp_dict)

        # 执行
        result = await skill.execute(
            {"action": action, **params}, gcp_slice, context
        )
        result_dict = result.to_dict()

        # 推送 SKILL_RESULT
        if self.stream_queue:
            await self.stream_queue.put(("skill_result", {
                "skill": skill_name, "action": action, "result": result_dict,
            }))

        return result_dict

    async def run_parallel(
        self, tasks: List[Dict[str, Any]], context: Dict = None,
    ) -> Dict[str, Dict]:
        """并行执行多个独立任务。"""
        async def _run(task):
            task_id = task["task_id"]
            result = await self.execute_skill(
                task["skill"], task["action"], task.get("params", {}), context
            )
            return task_id, result

        results = await asyncio.gather(
            *[_run(t) for t in tasks], return_exceptions=True
        )
        out = {}
        for item in results:
            if isinstance(item, Exception):
                logger.error("parallel task error: %s", item)
                continue
            tid, res = item
            out[tid] = res
        return out

    async def run_serial(
        self, tasks: List[Dict[str, Any]], context: Dict = None,
    ) -> Dict[str, Dict]:
        """串行执行任务链，后一个可以用前一个的结果。"""
        out = {}
        ctx = dict(context or {})
        for task in tasks:
            tid = task["task_id"]
            try:
                result = await self.execute_skill(
                    task["skill"], task["action"], task.get("params", {}), ctx
                )
                out[tid] = result
                ctx[f"prev_{tid}"] = result  # 传递给下一个
            except Exception as e:
                logger.error("serial task %s error: %s", tid, e)
                out[tid] = SkillResult.error(str(e)).to_dict()
        return out

    async def execute_plan(
        self, plan: List[Dict[str, Any]], context: Dict = None,
    ) -> Dict[str, Dict]:
        """执行完整任务计划（DAG）。

        plan: [{task_id, skill, action, params, deps: [task_id], parallel_group}]
        - deps 为空的任务可以立即执行
        - 同 parallel_group 的任务并行
        - 有 deps 的任务等依赖完成后串行执行
        """
        # MVP 简化：分波执行（wave execution）
        # 1. 先按 parallel_group 分组并行执行无依赖的任务
        # 2. 等它们完成后执行有依赖的串行任务
        completed: Dict[str, Dict] = {}
        remaining = list(plan)

        while remaining:
            # 找出所有依赖已满足的任务
            ready = [
                t for t in remaining
                if all(d in completed for d in t.get("deps", []))
            ]
            if not ready:
                logger.error("circular dependency in task plan: %s", remaining)
                break

            # 按 parallel_group 分组执行
            groups: Dict[str, List[Dict]] = {}
            for t in ready:
                pg = t.get("parallel_group", "")
                groups.setdefault(pg, []).append(t)

            for pg, tasks in groups.items():
                ctx = {**(context or {}), "completed": completed}
                if pg and len(tasks) > 1:
                    results = await self.run_parallel(tasks, ctx)
                else:
                    results = await self.run_serial(tasks, ctx)
                completed.update(results)

            # 从 remaining 移除已完成
            done_ids = {t["task_id"] for t in ready}
            remaining = [t for t in remaining if t["task_id"] not in done_ids]

        return completed
```

---

## 6. JourneyStore

### 文件：`app/agent/store.py`

包装 LangGraph `MemorySaver`，管理活跃旅程实例。

```python
class JourneyStore:
    _instance = None

    def __init__(self):
        self._checkpointer = MemorySaver()
        self._journeys: Dict[str, Dict[str, Any]] = {}  # journey_id → metadata
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "JourneyStore":
        if cls._instance is None:
            cls._instance = JourneyStore()
        return cls._instance

    def get_checkpointer(self) -> MemorySaver:
        return self._checkpointer

    def get_config(self, journey_id: str) -> Dict[str, Any]:
        return {"configurable": {"thread_id": journey_id}}

    async def create_journey(self, journey_id: str) -> None:
        self._journeys[journey_id] = {
            "created_at": time.time(),
            "last_activity": time.time(),
            "status": "active",
        }

    async def touch(self, journey_id: str) -> None:
        if journey_id in self._journeys:
            self._journeys[journey_id]["last_activity"] = time.time()

    async def complete_journey(
        self, journey_id: str, final_state: Dict[str, Any],
    ) -> None:
        if journey_id in self._journeys:
            self._journeys[journey_id]["status"] = "completed"
            self._journeys[journey_id]["completed_at"] = time.time()
            self._journeys[journey_id]["summary"] = {
                "destination": final_state.get("destination_name", ""),
                "duration_min": final_state.get("eta", {}).get("remaining_min", 0),
                "distance_km": final_state.get("route", {}).get("distance_km", 0),
            }

    async def update_preferences(
        self, user_id: str, journey_state: Dict[str, Any],
    ) -> None:
        """行程结束后增量更新用户偏好。"""
        # MVP 简化：记录常用目的地（追加到 frequent_pois）
        ...

    def cleanup(self) -> None:
        """断开连接时调用。"""
        pass
```

---

## 7. JourneyOrchestrator（核心桥梁）

### 文件：`app/agent/orchestrator.py`

这是最关键的文件——管理每个 WS 连接对应的图实例，桥接 LangGraph 和 WebSocket，处理中断/恢复、GCP 监听、状态同步。

```python
class JourneyOrchestrator:
    """WS 层唯一交互的类。封装 graph + store + streaming + GCP 监听。"""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.journey_id = f"j_{id(ws)}"
        self.store = get_journey_store()
        self.graph = build_journey_graph().compile(
            checkpointer=self.store.get_checkpointer(),
            interrupt_after=["clarifying", "recommending", "ready"],
        )
        self.stream_queue: asyncio.Queue = asyncio.Queue()
        self.claude = get_claude()
        self.gcp_store = get_store()
        self.skill_executor = SkillExecutor(self.gcp_store, self.stream_queue)
        self.gcp_monitor_task: Optional[asyncio.Task] = None
        self._stream_writer_task: Optional[asyncio.Task] = None

    def _graph_config(self) -> Dict[str, Any]:
        cfg = self.store.get_config(self.journey_id)
        cfg["configurable"].update({
            "stream_queue": self.stream_queue,
            "claude": self.claude,
            "skill_executor": self.skill_executor,
        })
        return cfg

    # ---- 入口方法（被 ws.py 调用） ----

    async def start_journey(
        self, user_query: str, request_id: str,
    ) -> None:
        """新用户输入 → 启动/恢复图执行，流式推送结果。"""
        await self.store.create_journey(self.journey_id)

        # 取 GCP 快照
        gcp_snapshot = self.gcp_store.to_dict()

        initial_state: AgentState = {
            "journey_id": self.journey_id,
            "request_id": request_id,
            "user_query": user_query,
            "chat_history": [{"role": "user", "content": user_query}],
            "gcp_snapshot": gcp_snapshot,
            "intent_type": "unknown", "intent_confidence": 0.0,
            "user_intent": "", "destination_name": "",
            "destination": {}, "destination_candidates": [],
            "extracted_slots": {}, "missing_slots": [],
            "proposals": [], "selected_plan_id": None,
            "task_plan": [],
            "route": {}, "eta": {}, "reminders": [], "parking": {},
            "poi_results": {}, "skill_results": {},
            "clarify_question": "", "clarify_count": 0,
            "user_clarify_reply": "",
            "journey_status": "idle",
            "journey_started_at": 0.0,
            "replan_reason": "", "replan_count": 0,
            "final_response_text": "", "error": None,
        }

        await self._run_graph(initial_state)

    async def handle_clarify_reply(
        self, answer: str, request_id: str,
    ) -> None:
        """用户回复澄清问题 → resume 图。"""
        await self.graph.aupdate_state(
            self._graph_config(),
            {"user_clarify_reply": answer,
             "chat_history": [{"role": "user", "content": answer}]},
        )
        await self._run_graph(None)

    async def handle_journey_action(
        self, action: str, payload: Dict, request_id: str,
    ) -> None:
        """处理旅程操作。

        action: select_plan / confirm_departure / cancel / ...
        """
        updates: Dict[str, Any] = {}
        if action == "select_plan":
            updates["selected_plan_id"] = payload.get("plan_id", "")
        elif action == "confirm_departure":
            updates["journey_started_at"] = 0.0  # 标记需要在 in_progress 设置
        elif action == "cancel":
            # 直接结束旅程
            await self._sync_to_gcp("completed")
            return

        if updates:
            await self.graph.aupdate_state(self._graph_config(), updates)
        await self._run_graph(None)

    # ---- 核心执行循环 ----

    async def _run_graph(
        self, initial_state: Optional[Dict[str, Any]],
    ) -> None:
        """执行图（首次或 resume），并将 stream_queue 事件转发到 WS。"""
        # 启动 stream writer 后台任务
        writer_done = asyncio.Event()
        self._stream_writer_task = asyncio.create_task(
            self._drain_queue_to_ws(writer_done)
        )

        try:
            if initial_state is not None:
                async for event in self.graph.astream(
                    initial_state, self._graph_config(),
                    stream_mode="updates",
                ):
                    await self._handle_graph_event(event)
            else:
                async for event in self.graph.astream(
                    None, self._graph_config(),
                    stream_mode="updates",
                ):
                    await self._handle_graph_event(event)
        except Exception as e:
            logger.exception("graph execution error: %s", e)
            await self.ws.send_text(ws_msg(
                WsMessageType.ERROR,
                {"code": "GRAPH_ERROR", "message": str(e)},
            ))
        finally:
            # 等待队列排空
            await self.stream_queue.put(("__end__", {}))
            await writer_done.wait()

        # 执行完后同步状态到 GCP，并可能启动 GCP 监听
        snapshot = await self.graph.aget_state(self._graph_config())
        status = snapshot.values.get("journey_status", "")
        await self._sync_to_gcp(status)

        if status == "in_progress":
            self._start_gcp_monitor()
        elif status in ("completed", "arriving"):
            self._stop_gcp_monitor()

    async def _handle_graph_event(self, event: Dict[str, Any]) -> None:
        """处理 graph astream 的 updates 事件。"""
        for node_name, node_output in event.items():
            # 状态变化推送
            if "journey_status" in node_output:
                await self.stream_queue.put(("state_change", {
                    "from_state": node_name,
                    "to_state": node_output["journey_status"],
                }))
            # 方案卡片推送
            if "proposals" in node_output:
                await self.stream_queue.put(("card_update", {
                    "type": "proposals", "data": node_output["proposals"],
                }))
            # 路线卡片推送
            if "route" in node_output and node_output["route"]:
                await self.stream_queue.put(("card_update", {
                    "type": "route", "data": node_output["route"],
                }))

    async def _drain_queue_to_ws(self, done: asyncio.Event) -> None:
        """后台任务：将 stream_queue 中的事件翻译为 WS 消息发送。"""
        try:
            while True:
                evt_type, data = await self.stream_queue.get()
                if evt_type == "__end__":
                    break
                await self._send_ws_event(evt_type, data)
        except Exception as e:
            logger.exception("drain_queue error: %s", e)
        finally:
            done.set()

    async def _send_ws_event(self, evt_type: str, data: Dict) -> None:
        """将内部事件映射为 WsMessage 发送。"""
        mapping = {
            "token_stream": WsMessageType.TOKEN_STREAM,
            "message": WsMessageType.MESSAGE,
            "skill_start": WsMessageType.SKILL_START,
            "skill_result": WsMessageType.SKILL_RESULT,
            "state_change": WsMessageType.STATE_CHANGE,
            "card_update": WsMessageType.CARD_UPDATE,
            "clarify_question": WsMessageType.CLARIFY_QUESTION,
            "error": WsMessageType.ERROR,
        }
        ws_type = mapping.get(evt_type)
        if ws_type is None:
            return
        await self.ws.send_text(ws_msg(ws_type, data))

    async def _sync_to_gcp(self, status: str) -> None:
        """在生命周期边界同步关键字段到 GCP ContextStore。"""
        snapshot = await self.graph.aget_state(self._graph_config())
        values = snapshot.values
        updates: Dict[str, Any] = {"journey.status": status}

        if values.get("destination_name"):
            updates["journey.destination_name"] = values["destination_name"]
        dest = values.get("destination") or {}
        if dest.get("lat") is not None:
            updates["journey.destination.lat"] = dest["lat"]
            updates["journey.destination.lon"] = dest["lon"]
        eta = values.get("eta") or {}
        if eta.get("eta_arrival_time"):
            updates["journey.eta_arrival"] = eta["eta_arrival_time"]
        if eta.get("remaining_min") is not None:
            updates["journey.eta_remaining_min"] = eta["remaining_min"]
        route = values.get("route") or {}
        if route.get("distance_km") is not None:
            updates["journey.progress_pct"] = 0  # 将由监控任务更新

        if len(updates) > 1:
            await self.gcp_store.update(updates)

    # ---- GCP 事件监听（in_progress 期间） ----

    def _start_gcp_monitor(self) -> None:
        if self.gcp_monitor_task and not self.gcp_monitor_task.done():
            return
        self.gcp_monitor_task = asyncio.create_task(
            self._monitor_gcp_loop()
        )

    def _stop_gcp_monitor(self) -> None:
        if self.gcp_monitor_task:
            self.gcp_monitor_task.cancel()
            self.gcp_monitor_task = None

    async def _monitor_gcp_loop(self) -> None:
        """后台协程：在行程进行中监听 GCP 变化，触发重规划/到达判断。

        MVP 简化实现：
        - 定期（30s）检查车辆位置变化 + ETA 更新
        - 监听 GCP event queue 中的航班/天气/交通变化
        - 检测到重大变化 → 设置 replan_reason → 恢复图到 in_progress
        - 检测到达 → 触发 arriving
        """
        queue = self.gcp_store.get_event_queue()
        check_interval = 30  # 秒
        last_check = 0.0

        while True:
            try:
                # 等待 GCP 事件（带超时）
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=check_interval,
                    )
                except asyncio.TimeoutError:
                    event = None

                now = time.time()
                if event or (now - last_check) >= check_interval:
                    last_check = now
                    await self._periodic_check()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("gcp monitor error: %s", e)
                await asyncio.sleep(5)

    async def _periodic_check(self) -> None:
        """定期检查：更新 ETA、判断到达/重规划。"""
        # 取最新状态
        snapshot = await self.graph.aget_state(self._graph_config())
        values = snapshot.values

        # 更新 ETA
        route = values.get("route") or {}
        vehicle_pos = self.gcp_store.get("vehicle.position")
        if route and vehicle_pos:
            result = await self.skill_executor.execute_skill(
                "dynamic_eta", "eta_query",
                {"current_position": vehicle_pos,
                 "route_polyline": route.get("polyline", ""),
                 "destination": values.get("destination", {})},
            )
            if result.get("status") == "success":
                remaining = result.get("data", {}).get("remaining_min", 999)
                # 判断是否需要重规划
                prev_eta = values.get("eta", {}).get("remaining_min", remaining)
                delta = abs(remaining - prev_eta)

                updates: Dict[str, Any] = {"eta": result.get("data", {})}

                if remaining <= 5:
                    # 到达
                    await self.graph.aupdate_state(
                        self._graph_config(), updates,
                    )
                    await self._run_graph(None)
                    return
                elif delta >= 10:
                    # 需要重规划
                    updates["replan_reason"] = f"ETA变化{delta}分钟"
                    await self.graph.aupdate_state(
                        self._graph_config(), updates,
                    )
                    await self._run_graph(None)
                    return
                else:
                    # 仅更新 ETA，不触发图执行
                    await self.graph.aupdate_state(
                        self._graph_config(), updates,
                    )
                    await self.stream_queue.put(("card_update", {
                        "type": "eta", "data": result.get("data", {}),
                    }))

    def cleanup(self) -> None:
        """WS 断开时清理。"""
        self._stop_gcp_monitor()
```

---

## 8. Prompts

### 文件：`app/agent/prompts.py`

所有 Prompt 以 builder 函数形式导出，动态注入 GCP 上下文和可用 Skill 信息。

```python
def build_system_prompt(gcp_snapshot: Dict[str, Any]) -> str:
    """基础系统提示词：角色定义 + 能力边界 + GCP 上下文。"""
    ...

def build_intent_prompt(gcp_snapshot: Dict[str, Any]) -> str:
    """意图识别 + 槽位抽取 prompt。
    输出 JSON: {intent_type, user_intent, confidence, destination_name,
                extracted_slots, missing_slots}
    """
    ...

def build_clarify_prompt(
    missing_slots: List[str], current_understanding: Dict[str, Any],
    gcp_snapshot: Dict[str, Any],
) -> str:
    """澄清追问 prompt。输出：单个追问问题文本。"""
    ...

def build_recommend_prompt(
    intent_type: str, destination: Dict, skill_results: Dict,
    user_profile: Dict, gcp_snapshot: Dict[str, Any],
) -> str:
    """方案推荐 prompt。输出：3套方案 JSON 数组。"""
    ...

def build_plan_decompose_prompt(
    selected_plan: Dict, available_skills: List[Dict],
    gcp_snapshot: Dict[str, Any],
) -> str:
    """任务拆解 prompt。
    输出：[{task_id, skill, action, params, deps, parallel_group}]
    """
    ...

def build_replan_detection_prompt(
    gcp_changes: Dict[str, Any], current_route: Dict, current_eta: Dict,
) -> str:
    """重规划触发判断 prompt。
    输出：{need_replan, reason, affected_skills}
    """
    ...
```

**关键原则**：
- 中文，简洁专业，适合驾驶场景
- 结构化输出用 JSON Schema 约束
- Prompt 精炼，减少 token 消耗
- 系统 prompt 中列出所有 Skill 的能力描述（名称+功能），供 Claude 参考

---

## 9. WebSocket 集成

### 修改文件：`app/api/ws.py`

将 `/ws/chat` 从直接调用 Claude 改为使用 `JourneyOrchestrator`。

```python
@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    await websocket.accept()
    logger.info("chat ws connected: %s", id(websocket))
    orchestrator = JourneyOrchestrator(websocket)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = parse_ws_message(raw)

            if msg.type == WsMessageType.PING:
                await websocket.send_text(ws_msg(
                    WsMessageType.PONG, {}, msg.request_id
                ))
                continue

            if msg.type == WsMessageType.TEXT_INPUT:
                text = msg.payload.get("text", "")
                if not text:
                    await websocket.send_text(ws_msg(
                        WsMessageType.ERROR,
                        {"code": "EMPTY_MESSAGE", "message": "消息不能为空"},
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
                    action, msg.payload, msg.request_id
                )
                continue

            if msg.type in (WsMessageType.AUDIO_START,
                           WsMessageType.AUDIO_CHUNK,
                           WsMessageType.AUDIO_STOP):
                await websocket.send_text(ws_msg(
                    WsMessageType.ERROR,
                    {"code": "NOT_IMPLEMENTED",
                     "message": f"audio not ready ({msg.type})"},
                    msg.request_id,
                ))
                continue

    except WebSocketDisconnect:
        orchestrator.cleanup()
        logger.info("chat ws disconnected: %s", id(websocket))
    except Exception as e:
        orchestrator.cleanup()
        logger.exception("chat ws error: %s", e)
```

### 下行事件 payload 约定

| WsMessageType | payload | 触发时机 |
|---|---|---|
| `TOKEN_STREAM` | `{text, index?}` | Claude 流式 token |
| `MESSAGE` | `{role, content, stop_reason, usage}` | 一轮完整消息 |
| `SKILL_START` | `{skill, action, task_id?}` | Skill 开始执行 |
| `SKILL_RESULT` | `{skill, action, result}` | Skill 执行完成 |
| `STATE_CHANGE` | `{from_state, to_state}` | 旅程状态切换 |
| `CARD_UPDATE` | `{type, data}` | 卡片数据更新（proposals/route/eta/arriving） |
| `CLARIFY_QUESTION` | `{question, options?}` | 澄清追问 |
| `ERROR` | `{code, message}` | 错误 |

---

## 10. 上下文管理与开发进度恢复

### 10.1 Agent 运行时上下文管理

**对话历史压缩**：在每个节点调用 Claude 前，通过工具函数 `compress_messages(chat_history, max_messages=20)` 裁剪历史：
- 保留系统 prompt + 前 2 条用户消息作为锚点
- 保留最近 10 轮对话
- Skill 结果在历史中以摘要形式保留（skill 名称 + 关键指标 + 1 句话），不保留完整 JSON

**GCP 快照最小化**：旅程开始时取一次完整快照，之后通过 GCP 事件队列只接收变化增量。

### 10.2 开发进度日志（应对 /clear 后恢复）

**文件**：`SPRINT_PROGRESS.md`（仓库根目录，和 PLAN-sprint*.md 同层）

每次开始 Sprint 或完成里程碑时更新，记录：
- **当前 Sprint**：编号 + 名称 + 开始时间
- **已完成**：任务清单（带文件路径引用）
- **进行中**：当前正在做的任务
- **待完成**：剩余任务列表
- **关键决策**：设计决策记录
- **已知问题**：bug / TODO
- **下一步**：恢复后该从哪里开始

**使用方式**：
- 每次 /clear 后，先读 `SPRINT_PROGRESS.md` 恢复上下文
- 每个子任务（如一个节点文件）完成后更新进度
- Sprint 完成后归档

**初始化内容**：Sprint 4 开始时创建，记录 Sprint 4 的完整任务清单，然后边做边更新。

---

## 关键文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/agent/state.py` | 新建 | AgentState TypedDict + 类型/常量 |
| `app/agent/prompts.py` | 新建 | 所有 Prompt builder 函数 |
| `app/agent/skill_executor.py` | 新建 | SkillExecutor（串并行+DAG） |
| `app/agent/store.py` | 新建 | JourneyStore（MemorySaver 包装） |
| `app/agent/graph.py` | 新建 | LangGraph StateGraph 构建 |
| `app/agent/orchestrator.py` | 新建 | JourneyOrchestrator（核心桥梁） |
| `app/agent/nodes/__init__.py` | 新建 | 导出所有节点函数 + 路由函数 |
| `app/agent/nodes/routers.py` | 新建 | 4 个条件路由函数 |
| `app/agent/nodes/understanding.py` | 新建 | 理解节点 |
| `app/agent/nodes/clarifying.py` | 新建 | 澄清节点 |
| `app/agent/nodes/destination_confirm.py` | 新建 | 目的地确认节点 |
| `app/agent/nodes/recommending.py` | 新建 | 推荐节点 |
| `app/agent/nodes/planning.py` | 新建 | 规划节点 |
| `app/agent/nodes/ready.py` | 新建 | 就绪节点 |
| `app/agent/nodes/in_progress.py` | 新建 | 进行中节点 |
| `app/agent/nodes/replanning.py` | 新建 | 重规划节点 |
| `app/agent/nodes/arriving.py` | 新建 | 到达节点 |
| `app/agent/nodes/completed.py` | 新建 | 完成节点 |
| `app/agent/__init__.py` | 修改 | 导出 JourneyOrchestrator 等 |
| `app/api/ws.py` | 修改 | /ws/chat 接入 JourneyOrchestrator |
| `README.md` | 修改 | 标记 Sprint 4 完成 |
| `SPRINT_PROGRESS.md` | 新建 | 开发进度日志（/clear 恢复用） |

---

## 可复用的现有代码

- **ClaudeClient**（`app/adapters/claude.py`）：`chat_stream` / `chat` / 自动 tool-use loop，节点直接使用
- **build_copilot_system_prompt()**（`app/adapters/prompts.py`）：系统提示词基础模板，在其基础上扩展
- **BaseSkill + registry**（`app/skills/`）：`get_all_skills()`, `get_skill()`，SkillExecutor 直接使用
- **ContextStore**（`app/gcp/store.py`）：`snapshot()`, `to_dict()`, `get()`, `update()`, `get_event_queue()`
- **JourneyStatus / IntentType 枚举**（`app/gcp/models.py`）：状态值直接对应 journey_status 字段
- **WsMessageType + ws_msg() + parse_ws_message()**（`app/core/websocket.py`）：WS 消息协议
- **AmapClient**（`app/adapters/amap.py`）：`geocode` 等被 understanding 节点使用
- **SkillResult**（`app/skills/base.py`）：Skill 返回类型，节点直接消费
- **MemorySaver**（`langgraph.checkpoint.memory`）：通过 JourneyStore 包装使用

---

## 验证方式

### 1. 启动验证
```bash
cd backend && uvicorn app.main:app --reload
```
确认所有 agent 模块无导入错误，graph 编译成功。

### 2. 目标驱动型场景（Amy 接机）
- WS 连接后发送 TEXT_INPUT：「帮我去虹桥机场 T2 接客户，航班 8 点到」
- 预期事件序列：
  1. TOKEN_STREAM（流式回复）
  2. SKILL_START: route_master / parking_find（并行）
  3. SKILL_RESULT: ...
  4. CARD_UPDATE: type=proposals（3 套方案）
  5. STATE_CHANGE: to_state=ready
  6. CLARIFY_QUESTION（或直接等 JOURNEY_ACTION）
- 发送 JOURNEY_ACTION: {action: "select_plan", plan_id: "plan_1"}
- 发送 JOURNEY_ACTION: {action: "confirm_departure"}
- 预期：进入 in_progress，开始 ETA 监控

### 3. 意图驱动型场景（Claire 聚会）
- 切换到 Claire 预设后发送：「今晚想找个安静点的地方和闺蜜吃饭」
- 预期：
  - intent_type = intent_driven
  - 调用 local_poi.poi_recommend → 3 家餐厅
  - 3 套方案包含餐厅+路线+推荐理由
  - 推荐理由贴合 Claire 画像（氛围感、bistro、法餐/意餐等）

### 4. 澄清追问
- 发送：「等下出去一下」
- 预期：
  - CLARIFY_QUESTION 追问目的地/目的
  - 回复后正确理解
  - 最多追问 3 轮后放弃

### 5. 重规划
- 确认出发后，通过 GCP REST API 更新交通为拥堵
- 预期：
  - GCP 监听检测到变化
  - 触发 replanning
  - 重新计算路线+ETA
  - 推送 ETA 变化提醒

### 6. 到达
- 更新车辆位置到目的地附近（或模拟 remaining_min < 5）
- 预期：
  - 进入 arriving
  - 推送停车引导卡片
  - 最后进入 completed

### 7. /ws/gcp 联动
- 验证旅程状态变化同步推送到 GCP WebSocket 通道
- 前端 GCP 面板正确显示状态变化

---

## 实施顺序（6 个阶段）

### Phase 1：基础层（state → prompts → store → skill_executor）
1. `state.py` — 定义 AgentState TypedDict
2. `prompts.py` — 所有 Prompt builder 函数
3. `store.py` — JourneyStore + MemorySaver 包装
4. `skill_executor.py` — SkillExecutor（单 Skill → 串行 → 并行 → DAG）

### Phase 2：核心节点（understanding → clarifying → destination_confirm → recommending）
5. `nodes/routers.py` — 路由函数骨架
6. `nodes/understanding.py` — 意图识别 + 槽位抽取
7. `nodes/clarifying.py` — 澄清追问
8. `nodes/destination_confirm.py` — 目的地消歧
9. `nodes/recommending.py` — 3 套方案生成

### Phase 3：规划+就绪（planning → ready → graph 组装）
10. `nodes/planning.py` — 任务拆解 + SkillExecutor 执行
11. `nodes/ready.py` — 汇总 + 等待确认
12. `graph.py` — 组装 StateGraph + interrupt_after + compile

### Phase 4：行程中后期（in_progress → replanning → arriving → completed）
13. `nodes/in_progress.py` — ETA 更新
14. `nodes/replanning.py` — 增量重规划
15. `nodes/arriving.py` — 停车+到达提醒
16. `nodes/completed.py` — 收尾+偏好更新

### Phase 5：Orchestrator + WS 集成
17. `orchestrator.py` — JourneyOrchestrator 完整实现（_run_graph + _drain_queue_to_ws + _sync_to_gcp + GCP monitor）
18. `ws.py` 改造 — 替换 _handle_text_input 为 Orchestrator 调用

### Phase 6：端到端验证 + 收尾
19. 启动测试 + 场景验证（目标驱动/意图驱动/澄清/重规划/到达）
20. Bug 修复 + edge cases
21. `SPRINT_PROGRESS.md` 初始化
22. `README.md` 更新 Sprint 4 完成标记
