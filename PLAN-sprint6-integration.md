# Sprint 6: 全链路联调计划

> 目标：前后端端到端跑通，从用户输入 → 方案选择 → 确认出发 → 行程中 → 到达，所有卡片和地图交互正常。

## 已发现的 Bug / Gap

### P0 — 阻塞流程
1. **POI 候选选择流程断裂**：前端点击候选 POI 发送 `clarify_reply {answer: name, candidate_id: id}`，但后端 `handle_clarify_reply` 只读 `answer`，不处理 candidate_id；resume 后 understanding 把候选名称当新 query 重新解析，无法正确确认目的地。
   - **修复**：前端改为发送 `journey_action {action: 'select_candidate', candidate: {...}}`；后端新增 select_candidate 处理，直接写入 destination，绕过 understanding 重新解析。
2. **前端 .env 缺失**：没有 `.env` 文件，Vite 无法获取 AMAP key。创建 `.env` 配置。

### P1 — 显示/交互问题
3. **停车场坐标字段名不匹配**：后端 parking_find 输出 `position: {lat, lon}`，前端 MapView 读 `lot.lon/lng/location.lon/location.lng` 但不读 `lot.position`，导致停车场 marker 无法渲染。
4. **`EtaCard`/`RouteCard` 被 `0` 值误隐藏**：`if (!data?.remaining_min)` 会把 `0` 当作 falsy 隐藏卡片。
5. **`getDestination()` 在 arriving 阶段 stub 为空**：arriving 卡片渲染后地图上没有目的地 marker。
6. **ready 节点 destination lat/lon 默认 0**：如果 state.destination 为空，发送 `{lat:0, lon:0}` 会在 (0,0) 放一个 marker。
7. **卡片和消息分离渲染**：所有消息在前、所有卡片在后，导致对话流中 AI 消息和卡片不对应。

### P2 — 体验优化
8. **选中方案后 UI 反馈不够**：selectPlan 后应显示"正在规划"状态。
9. **确认出发后应禁用按钮**：JourneyReadyCard 点击确认出发后按钮应禁用/消失。
10. **澄清回复后应禁用输入**：ClarifyCard 已有 `replied` 状态，但 CSS 上需要更明确的"已发送"反馈。

## 执行 Phase

### Phase 1: 修复数据契约不匹配
- [x] 修复停车场坐标字段（后端：transit hub path 的 position → location）
- [x] 修复 EtaCard/RouteCard 的 falsy 0 检查
- [x] 修复 getDestination() arriving 分支 + (0,0) 过滤
- [x] 修复 ready 节点 destination 默认值（用 None 而非 0）
- [x] 修复 destination_confirm 的 location 字符串 → dict 规范化
- [x] 修复 orchestrator 卡片重复推送（移除 route/parking 重复推送）

### Phase 2: 修复交互流程
- [x] 新增 select_candidate journey_action（前后端）
- [x] 修改 understanding 节点：如果已有 destination lat/lon，跳过重新解析（快速路径）
- [x] 修改 destination_confirm 节点：已有有效坐标直接跳过搜索
- [x] 修复 confirm_departure 后按钮状态（JourneyReadyCard）
- [x] 修复 ProposalCard 选中后的状态反馈（使用 store.selectedPlanId + 规划中提示）
- [x] 修复 planning 节点：禁用 Claude 任务拆解（action 名称不匹配问题），改用内置规则计划
- [x] 修复路线/ETA/停车数据收集：兜底遍历所有 skill 结果

### Phase 3: 启动验证
- [x] 创建前端 .env
- [x] 启动后端验证无导入错误
- [x] 启动前端验证编译通过（TS + Vite build）
- [x] WebSocket 连接验证
- [x] 目标驱动场景端到端测试（完整 4 步：输入→候选选择→方案选择→确认出发）
- [x] 澄清+候选选择场景测试
- [ ] 地图叠加层验证（需浏览器，待手动验证）

### Phase 4: 收尾
- [ ] SPRINT_PROGRESS.md 更新
- [ ] README 更新
