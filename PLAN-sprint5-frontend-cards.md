# Sprint 5：前端 UI 卡片 + 完善

## Context

Sprint 1-4 已完成后端 Agent 全链路（意图识别→方案推荐→任务规划→行程执行→动态重规划）。后端通过 WebSocket 推送丰富的事件（token_stream / message / skill_start / skill_result / card_update / state_change / clarify_question / error），但前端目前只有基础文本聊天气泡，没有卡片化呈现。

**目标**：将后端 Agent 推送的各类事件以美观的卡片形式呈现在聊天面板中，让用户能看到方案选择、路线信息、ETA 更新、停车引导等丰富内容，形成完整的视觉交互闭环。

---

## 后端事件 → 前端卡片 映射

### 事件类型总览

| 后端事件 | payload 结构 | 前端卡片 |
|---|---|---|
| `card_update {type: proposals}` | data 是数组，每项 `{id, title, summary, eta_min, distance_km, strategy, pros, cons, reason}` | ProposalCard |
| `card_update {type: journey_ready}` | data 是对象 `{destination, route, eta, parking, reminders}` | JourneyReadyCard |
| `card_update {type: route}` | data `{distance_km, duration_min, toll_cny, strategy, polyline}` | RouteCard |
| `card_update {type: eta}` | data `{remaining_min, eta_arrival_time, traffic_level, confidence_band_min}` | EtaCard |
| `card_update {type: parking}` | data `{parking_lots, entry_hint}` | ParkingCard |
| `card_update {type: arriving}` | data `{destination, parking, parking_lots, arrival_message}` | ArrivingCard |
| `clarify_question` | `{question, round?, candidates?}` | ClarifyCard |
| `state_change` | `{from_state, to_state}` | StateBanner |
| `skill_start` | `{skill, action, task_id}` | SkillStatusCard（加载中） |
| `skill_result` | `{skill, action, task_id, status, result?, error?}` | SkillStatusCard（更新状态） |
| `error` | `{code, message}` | ErrorCard |
| `message` | `{role, content}` | 聊天气泡（已有） |
| `token_stream` | `{text, index?}` | 流式气泡（已有） |

### 关键注意点

1. **proposals 的 data 是数组**，其他 card_update 的 data 是对象
2. **clarify_question 除 question 外字段都是可选的**（round、candidates 可能没有）
3. **卡片可能重复推送**（节点直推 + orchestrator 从状态派生），前端需去重或幂等处理
4. **skill_result 有两种变体**：正常路径有 `result` 对象，异常路径有 `error` 字符串
5. parking / route 卡片只有数据非空时才推送，收到即有有效数据

---

## 前端架构设计

### 数据流

```
WebSocket (chatWs)
    ↓
useChatWebSocket Hook  ← 监听所有事件类型
    ↓
zustand stores
    ├── chatStore — 消息文本（保持不变）
    └── journeyStore — 卡片列表 + 旅程状态 + 交互方法
    ↓
ChatPanel 渲染
    ├── StateBanner（顶部状态栏）
    ├── 消息列表（每条消息后跟关联卡片）
    │   ├── 文本气泡
    │   ├── ProposalCard
    │   ├── JourneyReadyCard
    │   ├── RouteCard
    │   ├── EtaCard
    │   ├── ParkingCard
    │   ├── ClarifyCard
    │   ├── ArrivingCard
    │   ├── SkillStatusCard（可折叠）
    │   └── ErrorCard
    └── 输入框
```

### 卡片位置策略

- **消息流内嵌入**：所有卡片紧跟在对应 AI 回复气泡之后，作为消息内容的一部分
- **顶部 StateBanner**：ChatPanel header 下方显示当前旅程状态
- **地图叠加层**：ETA 卡片和目的地 marker 在地图上显示（Phase 5）

---

## Phase 1：类型定义 + Store 扩展

### 文件修改

**`src/types/index.ts`** — 新增卡片相关类型

```typescript
// 卡片类型
export type CardType =
  | 'proposals'
  | 'journey_ready'
  | 'route'
  | 'eta'
  | 'parking'
  | 'arriving'
  | 'clarify'
  | 'skill'
  | 'error'

// 方案
export interface ProposalData {
  id: string
  title: string
  summary: string
  eta_min: number
  distance_km: number
  strategy: string
  parking_hint: string
  pros: string[]
  cons: string[]
  reason: string
  source: string
}

// 行程就绪
export interface JourneyReadyData {
  destination: { name: string; lat: number; lon: number }
  route: { distance_km: number; duration_min: number; toll_cny: number; strategy: string; polyline: string }
  eta: { remaining_min: number; eta_arrival_time: string; traffic_level: string }
  parking: { lots: any[]; recommended_index: number }
  reminders: any[]
}

// ETA
export interface EtaData {
  remaining_min: number
  eta_arrival_time: string
  traffic_level: string
  confidence_band_min: number
}

// 停车
export interface ParkingData {
  parking_lots: Array<{ name: string; walk_min?: number; [key: string]: any }>
  entry_hint?: string
}

// 候选 POI（澄清用）
export interface PoICandidate {
  id: string
  name: string
  address: string
  location: { lat: number; lon: number }
  distance: string
  category: string
}

// 澄清问题
export interface ClarifyData {
  question: string
  round?: number
  candidates?: PoICandidate[]
}

// Skill 状态
export interface SkillStatusData {
  skill: string
  action: string
  task_id: string
  status: 'pending' | 'success' | 'error' | 'no_result'
  result?: any
  error?: string
}

// 通用卡片项
export interface CardItem {
  id: string
  type: CardType
  data: any
  timestamp: number
  request_id: string
}
```

### `src/store/journeyStore.ts` — 重写

完全重写为卡片状态管理 store：
- `cards: CardItem[]` — 按时间顺序的卡片列表
- `journeyStatus: JourneyStatus` — 当前旅程状态
- `addCard(card)` — 添加卡片（自动生成 id 和 timestamp）
- `updateCard(id, updates)` — 更新已有卡片（用于 skill 完成状态更新）
- `setJourneyStatus(status)` — 更新旅程状态
- `selectPlan(planId)` — 选择方案（调用 chatWs.send journey_action）
- `confirmDeparture()` — 确认出发
- `sendClarifyReply(answer)` — 回复澄清
- `reset()` — 重置

**关键设计**：
- 卡片通过 `request_id` 与消息关联，同一轮请求的消息和卡片共享同一个 request_id
- Skill 卡片的 id 用 `skill_<task_id>` 格式，方便通过 updateCard 更新状态
- 去重：添加卡片时检查相同 type + request_id 是否已存在，存在则更新而非新增

---

## Phase 2：useChatWebSocket Hook 增强

### 文件修改

**`src/hooks/useChatWebSocket.ts`**

新增监听：
- `card_update` → 根据 payload.type 添加对应卡片到 journeyStore
- `state_change` → 更新 journeyStore.journeyStatus
- `clarify_question` → 添加 clarify 类型卡片
- `skill_start` → 添加 skill 类型卡片（状态 pending）
- `skill_result` → 更新对应 skill 卡片（更新状态和结果）
- `error` → 添加 error 类型卡片

新增导出方法：
- `selectPlan(planId: string)`
- `confirmDeparture()`
- `sendClarifyReply(answer: string)`

---

## Phase 3：卡片组件实现

### 文件结构

```
src/components/Cards/
├── index.ts              # 统一导出
├── CardItem.tsx          # 卡片包裹器（根据 type 渲染对应组件）
├── Cards.css             # 共享卡片样式
├── ProposalCard.tsx      # 方案选择卡片
├── ProposalCard.css
├── JourneyReadyCard.tsx  # 行程就绪卡片
├── JourneyReadyCard.css
├── RouteCard.tsx         # 路线概要卡片
├── RouteCard.css
├── EtaCard.tsx           # 实时 ETA 卡片
├── EtaCard.css
├── ParkingCard.tsx       # 停车推荐卡片
├── ParkingCard.css
├── ArrivingCard.tsx      # 到达引导卡片
├── ArrivingCard.css
├── ClarifyCard.tsx       # 澄清追问卡片
├── ClarifyCard.css
├── SkillStatusCard.tsx   # Skill 执行状态卡片
├── SkillStatusCard.css
├── ErrorCard.tsx         # 错误提示卡片
├── ErrorCard.css
├── StateBanner.tsx       # 状态横幅
└── StateBanner.css
```

### 设计规范（沿用现有深色主题）

所有卡片共享基础样式（定义在 Cards.css 的 `.card-base`）：
- 背景：`rgba(20, 25, 40, 0.9)` + `backdrop-filter: blur(8px)`
- 边框：`1px solid rgba(99, 102, 41, 0.2)` —→ 应为 `rgba(99, 102, 241, 0.2)`（indigo 20%）
- 圆角：`12px`
- 内边距：`16px`
- 标题字重：600，14px，`#fff`
- 正文字号：13px，`#e2e8f0`
- 次要文字：12px，`#94a3b8`
- 间距节奏：4px / 8px / 12px / 16px
- 过渡：`all 0.25s ease`

### 各卡片详细设计

**ProposalCard**（3 套方案横向排列）：
- 标题行：「为您推荐了 3 套方案」
- 下方 3 张方案卡横向滚动（flex + overflow-x）
- 每张方案卡：title（14px 粗体）、summary（12px 灰色）、ETA+距离（13px indigo）、pros 列表、推荐理由
- 底部「选择此方案」按钮
- 选中状态：边框高亮 + 背景 indigo tint + 选中标记

**JourneyReadyCard**（完整行程卡片）：
- 顶部：目的地名称 + 状态标签「行程就绪」
- 路线信息：距离 / 时长 / 策略 / 过路费（四宫格或横向排列）
- ETA：剩余时间（大号数字）+ 到达时间 + 交通状态指示器
- 停车推荐：Top 1 停车场名称 + 步行时间
- 提醒列表：最多 2 条，展开查看更多
- 底部主按钮：「确认出发」（渐变主按钮）

**RouteCard**（路线概要）：
- 图标 + 「路线已规划」
- 距离 / 时长 / 策略 / 过路费 横向排列
- 简洁单行式设计

**EtaCard**（实时 ETA）：
- 大号剩余时间（分钟）+ 到达时间
- 交通状态标签（绿色 smooth / 黄色 slow / 红色 congested）
- 置信区间提示
- 可更新（新数据来时平滑过渡）

**ParkingCard**（停车推荐）：
- 标题「附近停车场推荐」
- 列表：3 个停车场，每行显示名称、距离/步行时间、价格/空位
- 推荐标记（星星/徽章）

**ArrivingCard**（到达引导）：
- 顶部：「即将到达」+ 目的地名称
- 停车引导：推荐停车场 + 步行路线提示
- 到达祝福/总结语

**ClarifyCard**（澄清追问）：
- 问题图标 + 问题文本
- 如有 candidates，显示可选列表（可点击选择）
- 底部输入框或快速回复选项

**SkillStatusCard**（Skill 执行状态）：
- 默认折叠成单行：「正在规划路线...」(加载中) / 「✓ 路线规划完成」
- 可展开查看详细参数和结果
- 加载中显示旋转图标
- 错误状态显示红色 + 错误信息

**ErrorCard**（错误提示）：
- 红色图标 + 错误代码 + 错误消息
- 建议操作（重试/检查网络）

**StateBanner**（顶部状态栏）：
- ChatPanel header 下方的窄条
- 显示当前旅程状态（理解中/规划中/行程中等）
- 左侧状态指示器（小圆点 + 状态文字）
- 右侧可能显示进度或 ETA
- 平滑淡入淡出切换

---

## Phase 4：ChatPanel 集成卡片渲染

### 文件修改

**`src/components/ChatPanel/ChatPanel.tsx`**

修改内容：
1. header 下方添加 StateBanner
2. 消息列表中，每条消息后渲染关联的卡片
3. 卡片按时间顺序插入，同一 request_id 的卡片紧跟在对应消息后面
4. 卡片支持点击交互（调用 journeyStore 的方法）

**渲染策略**：
- 维护一个按时间排序的「消息+卡片」混合列表
- 或者：消息列表保持原样，但每条消息组件内部查找对应 request_id 的卡片并在其后渲染
- MVP 采用简单方案：在消息列表下方独立渲染卡片列表（按时间排序）

**`src/components/ChatPanel/ChatPanel.css`**

添加卡片区域样式：
- `.cards-area` — 卡片容器
- `.card + .card` — 卡片间距 12px

---

## Phase 5：地图叠加层完善

### 文件修改

**`src/components/MapView/MapView.tsx`**

增强内容：
1. 根据 journeyStore 中的路线 polyline 绘制路线
2. 标记目的地位置（不同图标）
3. 到达阶段显示停车场 marker
4. 右上角 ETA 悬浮卡片（EtaCard 的精简版）
5. 路线缩放：有路线时自动缩放到路线范围

**`src/hooks/useAmap.ts`**

增加工具方法：
- `drawPolyline(id, path, options)` — 绘制/更新路线
- `removePolyline(id)` — 移除路线
- `addMarker(id, position, content, options)` — 添加自定义 marker
- `removeMarker(id)` — 移除 marker
- `fitToBounds(path)` — 缩放到包含路径的视野

---

## Phase 6：联调验证 + 收尾

### 验证场景

1. **意图驱动推荐**：输入「找个地方吃饭」
   - → StateBanner 显示「理解中」
   - → Skill 状态卡片（POI 推荐）
   - → ProposalCard（3 套方案）
   - → 点击选择 → JourneyReadyCard
   - → 点击确认出发 → EtaCard → 状态变为「行程中」

2. **目标驱动路线**：输入「去虹桥机场」
   - → 目的地确认（可能有 clarify card）
   - → 路线卡片
   - → 方案卡片
   - → 行程就绪卡片

3. **澄清追问**：输入「等下出去」
   - → ClarifyCard 显示追问
   - → 回复后继续流程，卡片更新

4. **重规划**（通过 GCP 面板触发）
   - → ETA 卡片更新数值
   - → Skill 状态卡片显示重规划
   - → 路线卡片更新

5. **到达场景**（模拟剩余时间<5分钟）
   - → ArrivingCard 显示到达引导 + 停车
   - → 状态变为「已到达」

### 收尾

- 更新 `README.md` 标记 Sprint 5 完成
- 更新 `SPRINT_PROGRESS.md` 记录进度
- 更新 `package.json` 如有新增依赖（应该不需要）

---

## 关键设计决策

1. **卡片嵌入消息流**：所有卡片作为对话内容的一部分显示在聊天面板中，保持对话式交互的自然感
2. **journeyStore 集中管理**：所有卡片状态通过 zustand 管理，WebSocket Hook 只负责事件→store 映射，组件只消费 store
3. **CSS 纯手写，沿用现有主题**：不引入新的 UI 库，保持代码量精简，视觉风格统一
4. **卡片与消息通过 request_id 关联**：同一轮对话产生的消息和卡片共享 request_id，可视觉分组
5. **Skill 状态卡片可折叠**：执行中显示加载状态，完成后可折叠为单行摘要
6. **幂等渲染**：通过 `type + request_id` 做去重 key，避免同一卡片重复推送导致重复渲染

---

## 文件清单

| 操作 | 文件 |
|---|---|
| 修改 | `src/types/index.ts` — 新增卡片类型定义 |
| 修改 | `src/store/journeyStore.ts` — 重写为卡片状态管理 |
| 修改 | `src/hooks/useChatWebSocket.ts` — 增强事件监听 |
| 新建 | `src/components/Cards/index.ts` |
| 新建 | `src/components/Cards/CardItem.tsx` + `Cards.css` |
| 新建 | `src/components/Cards/ProposalCard.tsx` + `.css` |
| 新建 | `src/components/Cards/JourneyReadyCard.tsx` + `.css` |
| 新建 | `src/components/Cards/RouteCard.tsx` + `.css` |
| 新建 | `src/components/Cards/EtaCard.tsx` + `.css` |
| 新建 | `src/components/Cards/ParkingCard.tsx` + `.css` |
| 新建 | `src/components/Cards/ArrivingCard.tsx` + `.css` |
| 新建 | `src/components/Cards/ClarifyCard.tsx` + `.css` |
| 新建 | `src/components/Cards/SkillStatusCard.tsx` + `.css` |
| 新建 | `src/components/Cards/ErrorCard.tsx` + `.css` |
| 新建 | `src/components/Cards/StateBanner.tsx` + `.css` |
| 修改 | `src/components/ChatPanel/ChatPanel.tsx` — 集成卡片 |
| 修改 | `src/components/ChatPanel/ChatPanel.css` — 卡片区域样式 |
| 修改 | `src/components/MapView/MapView.tsx` — 路线/停车/Eta 叠加 |
| 修改 | `src/hooks/useAmap.ts` — 路线绘制工具方法 |
| 修改 | `README.md` — Sprint 5 标记 |
| 修改 | `SPRINT_PROGRESS.md` — 进度更新 |
