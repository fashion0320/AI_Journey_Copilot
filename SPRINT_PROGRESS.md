# AI Journey Copilot — Sprint 进度日志

> 用于 /clear 后快速恢复上下文。每次完成里程碑时更新。

## 当前 Sprint

**Sprint 7：Demo 场景验证 + 优化** — 进行中
- 开始时间：2026-08-28
- 计划文件：`PLAN-sprint7-demo-optimization.md`

## 已完成

- [x] **Sprint 1**：项目骨架 + GCP 数据层
- [x] **Sprint 2**：CPSP Adapters（高德、豆包语音、Claude）
- [x] **Sprint 3**：5 个 Skills 实现
- [x] **Sprint 4**：LangGraph 状态机 + Agent Core
- [x] **Sprint 5**：前端 UI 卡片 + 完善
- [x] **Sprint 6**：全链路联调

## Sprint 4 进度（已完成）

### Phase 1：基础层 ✅
- [x] `app/agent/state.py` — AgentState TypedDict + init_agent_state
- [x] `app/agent/prompts.py` — 6 个 Prompt builder 函数
- [x] `app/agent/store.py` — JourneyStore + MemorySaver 包装
- [x] `app/agent/skill_executor.py` — SkillExecutor（单 Skill + 串行 + 并行 + DAG）

### Phase 2：核心节点 ✅
- [x] `app/agent/nodes/routers.py` — 4 个条件路由函数
- [x] `app/agent/nodes/understanding.py` — 意图识别 + 槽位抽取 + 降级
- [x] `app/agent/nodes/clarifying.py` — 澄清追问 + 降级
- [x] `app/agent/nodes/destination_confirm.py` — 目的地消歧（POI + geocode 降级）
- [x] `app/agent/nodes/recommending.py` — 3 套方案生成（goal-driven + intent-driven + Claude 整合）

### Phase 3：规划 + 就绪 + 图组装 ✅
- [x] `app/agent/nodes/planning.py` — 任务拆解（Claude + 降级）+ SkillExecutor DAG 执行
- [x] `app/agent/nodes/ready.py` — 汇总 JourneyPlan + 出发前总结
- [x] `app/agent/graph.py` — StateGraph 组装 + compile_journey_graph + make_graph_config

### Phase 4：行程中后期 ✅
- [x] `app/agent/nodes/in_progress.py` — 首次进入记录 + ETA 更新 + 卡片推送
- [x] `app/agent/nodes/replanning.py` — 增量重规划（按原因分 Skill 重算）
- [x] `app/agent/nodes/arriving.py` — 停车搜索 + 到达提醒 + 卡片
- [x] `app/agent/nodes/completed.py` — 保存历史 + 更新偏好 + 同步 GCP

### Phase 5：Orchestrator + WS 集成 ✅
- [x] `app/agent/orchestrator.py` — JourneyOrchestrator 完整实现
- [x] `app/api/ws.py` — /ws/chat 接入 Orchestrator
- [x] `app/agent/__init__.py` — 导出核心接口

### Phase 6：端到端验证 + 收尾 ✅
- [x] 启动测试（无导入错误）
- [x] 目标驱动场景验证（降级路径 OK）
- [x] 意图驱动场景验证（找餐厅 → 3 套方案）
- [x] 澄清追问验证（模糊输入 → 追问 → 恢复理解）
- [x] 重规划验证（设置 replan_reason → replanning → in_progress）
- [x] 到达场景验证（ETA ≤5min → arriving → completed）
- [x] Orchestrator 完整流式推送验证（123 条消息，4 种类型）
- [x] README.md 更新 Sprint 4 完成标记

## Sprint 5 进度（已完成）

### Phase 1：类型定义 + Store 扩展 ✅
- [x] `src/types/index.ts` — 新增 CardType / CardItem + 8 种卡片数据类型
- [x] `src/store/journeyStore.ts` — 重写为卡片状态管理（addCard / updateCard / findCard / 去重 / 交互方法 / 派生 selector）

### Phase 2：useChatWebSocket Hook 增强 ✅
- [x] card_update → 按 type 添加对应卡片
- [x] state_change → 更新 journeyStatus
- [x] clarify_question → 添加 clarify 类型卡片
- [x] skill_start → 添加 skill 类型卡片（pending）
- [x] skill_result → 更新对应 skill 卡片状态
- [x] error → 添加 error 类型卡片

### Phase 3：卡片组件实现 ✅
- [x] ProposalCard（3 套方案横向列表，点击选择）
- [x] JourneyReadyCard（行程就绪大卡片 + 确认出发按钮）
- [x] RouteCard（路线概要）
- [x] EtaCard（实时 ETA + 交通状态）
- [x] ParkingCard（停车场推荐列表）
- [x] ArrivingCard（到达引导 + 停车）
- [x] ClarifyCard（澄清追问 + 候选列表 + 输入框）
- [x] SkillStatusCard（可折叠执行状态 + spinner）
- [x] ErrorCard（错误提示）
- [x] StateBanner（顶部状态横幅 + 动画 dots）
- [x] CardItem（分发器）+ Cards.css（共享样式）+ index.ts（统一导出）
- [x] 深色主题统一（毛玻璃 + indigo 主色调 + 间距节奏）

### Phase 4：ChatPanel 集成 ✅
- [x] StateBanner 嵌入 header 下方
- [x] 卡片列表内嵌消息流（按时间顺序追加）
- [x] 卡片点击交互（选择方案 / 确认出发 / 回复澄清 / 选择候选）
- [x] 自动滚动到底部（cards 变化触发）

### Phase 5：地图叠加层 ✅
- [x] 路线 polyline 绘制（setPolyline，indigo 主题色）
- [x] 目的地 Marker（红色渐变圆形）
- [x] 停车场 Marker（arriving 阶段显示前 5 个）
- [x] 右上角浮动 ETA 卡片（ETA + 到达时间 + 交通状态）
- [x] 路线自动缩放到视野范围（fitToPoints）
- [x] useAmap 新增 fitToPoints（基于 Bounds，无临时 marker 泄漏）

### Phase 6：验证 + 收尾 ✅
- [x] TypeScript 编译通过（零错误）
- [x] Vite 生产构建成功（83 modules, 186KB）
- [x] 后端服务启动正常，WebSocket 连接 OK
- [x] 前端 dev server 启动 OK
- [x] README.md 更新 Sprint 5 完成标记
- [x] SPRINT_PROGRESS.md 更新

## Sprint 6 进度（已完成）

### Phase 1：数据契约修复 ✅
- [x] 停车场坐标字段名统一（transit hub 路径 position → location）
- [x] EtaCard/RouteCard falsy 0 误隐藏修复
- [x] getDestination() arriving 分支 stub 填充 + (0,0) 坐标过滤
- [x] ready 节点 destination 默认值用 None 替代 0
- [x] destination_confirm 节点 location 字符串规范化为 {lat, lon}
- [x] orchestrator 卡片重复推送清理（节点自管 card_update）

### Phase 2：交互流程修复 ✅
- [x] 新增 `select_candidate` journey_action（前后端）
- [x] understanding 节点快速路径（已有目的地坐标时跳过重新解析）
- [x] destination_confirm 节点已有有效坐标时直接跳过搜索
- [x] JourneyReadyCard 确认出发后按钮状态
- [x] ProposalCard 选中后状态反馈 + 规划中提示
- [x] planning 节点禁用 Claude 任务拆解（action 名称不匹配），改用内置规则计划
- [x] 路线/ETA/停车数据收集兜底：遍历所有 skill 结果

### Phase 3：启动验证 ✅
- [x] 前端 .env 创建
- [x] 后端导入无错误
- [x] 前端 TypeScript 编译通过（零错误）
- [x] 前端 Vite 生产构建通过（83 modules, 189KB）
- [x] WebSocket 连接验证
- [x] 目标驱动完整流程端到端测试通过（输入 → 候选选择 → 方案选择 → 确认出发 → in_progress）
- [x] 澄清+候选选择流程验证
- [x] 地图 polyline 渲染（修复了 AMap 编码 polyline 解码问题）
- [x] 目的地 Marker 渲染
- [x] 停车场 Marker 渲染（到达阶段）
- [x] 右上角 ETA 浮动卡片

### Phase 4：收尾 ✅
- [x] SPRINT_PROGRESS.md 更新
- [x] README.md 更新待补充（Sprint 7 一起更新）

## 关键决策记录

1. **Interrupt 机制**：使用 LangGraph `interrupt_after=["clarifying", "recommending", "ready"]`
2. **GCP 监听**：Orchestrator 外部后台协程监听 GCP 事件，不在图内循环
3. **流式输出**：通过 `stream_queue`（asyncio.Queue）+ config 注入实现 token 级流式推送
4. **状态同步**：在生命周期边界（ready/in_progress/completed）同步回 GCP ContextStore
5. **卡片嵌入消息流**：所有卡片作为对话内容的一部分显示在聊天面板中
6. **journeyStore 集中管理**：所有卡片状态通过 zustand 管理，WebSocket Hook 只负责事件→store 映射
7. **CSS 纯手写，沿用现有主题**：不引入新的 UI 库，保持代码量精简，视觉风格统一
8. **卡片与消息通过 request_id 关联**：同一轮对话产生的消息和卡片共享 request_id
9. **幂等渲染**：通过 `type + request_id` 做去重 key，避免同一卡片重复推送导致重复渲染
10. **地图 polyline 解码**：前端支持 "lon,lat;lon,lat" 明文格式，AMap 编码格式降级为空
11. **fitToPoints 实现**：使用 Bounds 而非临时 Marker，避免 overlay 泄漏
12. **POI 候选选择走 journey_action**：选择候选 POI 走 `journey_action {select_candidate}` 而非 `clarify_reply`，直接写入 destination 避免重新解析
13. **Planning 节点用内置规则而非 Claude 拆解**：Claude 生成的 skill/action 名称经常不匹配，MVP 阶段用内置规则计划更可靠
14. **卡片推送职责**：各节点自己推送 card_update，orchestrator 只推送 state_change，避免重复卡片

## Sprint 7 进度（进行中）

### Phase 1：Sprint 6 收尾 + 基线验证 ✅
- [x] TypeScript 编译零错误
- [x] 后端导入无错误
- [x] 前后端服务正常启动

### Phase 2：关键 Bug 修复 ✅
- [x] 地图中心初始不更新问题（useAmap 增加 center 变化时 setCenter 效果）
- [x] 路线 polyline 渲染竞态条件（MapView 改为 useMemo 派生数据，cards 变化触发重渲染）
- [x] AMap v5 polyline 编码格式解码（后端新增 polyline 工具，解码为纯文本格式）
- [x] 后端 route_master 三个方法（single/multi/reroute）统一解码 polyline
- [x] 重规划节点修复：补全 current_position 参数
- [x] 重规划节点修复：归一化 reroute 结果字段名（new_distance_km → distance_km）
- [x] 重规划节点修复：并行重算 ETA
- [x] GCP 面板显示状态统一为 gcpStore（移除 MainPage 本地重复状态）

### Phase 3：UX 优化 ✅（部分）
- [x] 聊天欢迎页 + 快捷建议按钮
- [x] 消息/卡片入场动画（淡入 + 上滑）
- [x] 滚动条美化

### Phase 4：Demo 模拟模式 ✅
- [x] 新增 JOURNEY_DEMO_SIMULATION 环境变量
- [x] 车辆位置沿路线 polyline 自动推进（每 30s 约 12% 进度）
- [x] 基于 Haversine 距离的路段插值
- [x] 后端已以 demo 模式启动

### 待完成
- [ ] 场景 1 端到端完整验证（需要浏览器打开前端测试）
- [ ] 场景 2 意图驱动验证
- [ ] 重规划功能验证
- [ ] 移动端适配检查
- [ ] 卡片数量上限 / 性能优化
- [ ] 错误边界
- [ ] README + Demo 使用说明更新

15. **Polyline 解码在后端完成**：AMap v5 API 返回编码 polyline，统一在后端解码为 "lon,lat;lon,lat;..." 纯文本格式，前端直接解析
16. **Demo 模拟模式**：通过 JOURNEY_DEMO_SIMULATION=1 环境变量启用，车辆位置沿路线自动推进（每 30s 约 12% 进度），约 4 分钟完成全程
17. **Map overlays 使用 useMemo 派生数据**：避免 get*() 函数引用稳定导致 effect 不触发的竞态条件，cards 数组变化时自动重新计算
18. **重规划结果字段归一化**：route_reroute 返回的 new_distance_km/new_duration_min 在 replanning 节点统一映射为 distance_km/duration_min，与 route_single 结果一致

## 已知问题 / TODO

- 重规划触发（GCP 事件导致的拥堵/事故）需要手动通过 GCP 面板触发
- WebSocket 断线重连时状态不同步
- 无 React Error Boundary

## 下一步入口点

> /clear 后从这里继续：
> 1. 读 `SPRINT_PROGRESS.md`（本文件）了解进度
> 2. Sprint 7（Demo 场景验证 + 优化）进行中 — 从 `PLAN-sprint7-demo-optimization.md` 开始
> 3. 在浏览器打开 http://localhost:5173 测试端到端场景
> 4. 如果需要回查设计细节：`PLAN-sprint5-frontend-cards.md`
