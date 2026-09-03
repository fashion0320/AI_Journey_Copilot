# AI Journey Copilot MVP

具备 Agent 任务理解、规划与编排能力的座舱 AI Journey Copilot Web Demo。

## 技术栈

- **后端**: Python 3.11+ / FastAPI / LangGraph / Claude
- **前端**: React 18 + TypeScript + Vite + Zustand
- **地图**: 高德地图 Web 服务 API（后端） + 高德 JS API v2.0（前端）
- **语音**: 豆包语音（火山引擎）- 流式 ASR + TTS
- **实时通信**: WebSocket（双通道：对话流 + GCP 事件流）

## 快速开始

### 1. 配置环境变量

```bash
cd backend
cp .env.example .env
# 填入高德 key、豆包语音 key、Anthropic API key

cd ../frontend
cp .env.example .env
# 填入高德 JS API key
```

### 2. 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

后端 API 文档: http://localhost:8000/docs

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问: http://localhost:5173

## 公网部署（Vercel + Render）

可以将产品部署到公网，通过链接分享给其他人访问。

### 前置准备

1. 将代码推送到 GitHub
2. 注册 [Render](https://render.com) 账号（免费）
3. 注册 [Vercel](https://vercel.com) 账号（免费）

### Step 1: 部署后端到 Render

1. Render 控制台 → New → Web Service → 连接你的 GitHub 仓库
2. 配置：
   - **Name**: 自定义（如 `ai-journey-copilot-backend`）
   - **Region**: Singapore（延迟低）
   - **Root Directory**: `backend`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT`
   - **Instance Type**: Free（免费版）
3. 环境变量（Environment Variables）：

   | Key | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | 你的 Claude API Key |
   | `AMAP_KEY` | 你的高德 Web 服务 Key |
   | `VOLCENGINE_API_KEY` | 火山引擎通用 Key |
   | `VOLCENGINE_ASR_API_KEY` | 火山 ASR Key（可复用上面） |
   | `VOLCENGINE_TTS_API_KEY` | 火山 TTS Key（可复用上面） |
   | `TAVILY_API_KEY` | Tavily 搜索 Key |
   | `CORS_ORIGINS` | `*`（先允许所有，等 Vercel 域名拿到后改紧） |
   | `JOURNEY_DEMO_SIMULATION` | `1` |
   | `LOG_LEVEL` | `info` |

4. 点击 Deploy，等待部署完成（约 2-5 分钟）
5. 部署成功后会拿到公网域名：`https://xxx.onrender.com`
6. 验证：访问 `https://xxx.onrender.com/health` 返回 `{"status":"ok"}`

> 注意：Render 免费版有冷启动（第一次访问需 30-60 秒唤醒），之后正常。

### Step 2: 部署前端到 Vercel

1. Vercel 控制台 → Add New → Project → 导入你的 GitHub 仓库
2. 配置：
   - **Framework Preset**: Vite（自动检测）
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`
3. 环境变量（Environment Variables）：

   | Key | Value |
   |---|---|
   | `VITE_API_BASE_URL` | `https://xxx.onrender.com`（Step 1 拿到的 Render 域名） |
   | `VITE_WS_BASE_URL` | `wss://xxx.onrender.com` |
   | `VITE_AMAP_JS_KEY` | 你的高德 JS API Key |

4. 点击 Deploy，等待构建完成（约 1-2 分钟）
5. 部署成功后拿到公网域名：`https://xxx.vercel.app`

### Step 3: 收尾

1. **收紧 CORS**：回到 Render，把 `CORS_ORIGINS` 从 `*` 改为 `https://xxx.vercel.app`，保存后会自动重新部署
2. **高德域名白名单**：登录高德开放平台控制台，在 JS API Key 的安全设置中添加 `xxx.vercel.app` 到域名白名单（如果之前只配置了 localhost）
3. **测试**：打开 `https://xxx.vercel.app`，验证对话、地图、卡片渲染正常

### 环境变量说明

| 变量 | 位置 | 用途 |
|---|---|---|
| `DEBUG=1` | 后端 | 开启热重载（仅开发用） |
| `ENABLE_TEST_ROUTERS=1` | 后端 | 启用测试 API 端点（/api/test/*），生产默认关闭 |

## 项目结构

```
ai-journey-copilot/
├── backend/
│   ├── app/
│   │   ├── core/           # 配置、日志、错误处理、WebSocket 消息定义
│   │   ├── gcp/            # Global Context Panel - 8大模块数据模型 + 存储 + 模拟数据
│   │   ├── adapters/       # CPSP 适配器（高德、豆包语音、Claude）
│   │   ├── skills/         # 5 个核心 Skill
│   │   ├── agent/          # LangGraph 状态机 + Agent Core
│   │   ├── api/            # REST + WebSocket 路由
│   │   └── main.py
│   ├── data/               # 模拟数据
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── components/     # UI 组件（对话、地图、卡片、GCP面板）
    │   ├── hooks/          # WebSocket、地图等自定义 hooks
    │   ├── services/       # API、WebSocket 客户端
    │   ├── store/          # Zustand 状态管理
    │   ├── pages/          # 页面
    │   └── types/          # TypeScript 类型定义
    └── package.json
```

## Sprint 进度

### ✅ Sprint 1: 项目骨架 + GCP 数据层
- 后端 FastAPI 项目初始化，目录结构完整
- 前端 React + TS + Vite 项目初始化
- GCP 8 大模块 Pydantic Schema
- ContextStore 单例 + 字段级订阅 + 事件队列
- Amy / Claire 两个用户画像 + 两个场景预设（接机/聚会）
- GCP REST API（查询/更新/预设/画像）
- GCP WebSocket 实时推送
- 前端布局：对话面板 + 地图 + GCP 控制面板
- WebSocket 双通道（对话 / GCP）客户端封装
- 前后端联调通过，可访问 http://localhost:5173

### ✅ Sprint 2: CPSP Adapters 对接
- 高德地图 7 个 API 封装（地理编码、关键字搜索、周边搜索、ID查询、驾车路径规划、天气、交通态势）
- 豆包语音 ASR/TTS 流式对接
- Claude + Web Search 封装（流式对话、tool-use loop、ConversationManager）

### ✅ Sprint 3: 5 个 Skills 实现
- BaseSkill 基类 + SkillResult + 统一工具定义导出
- Route_Master：单/多目的地路线规划、重规划、绕路检测（高德 v5 API）
- Dynamic_ETA：实时 ETA 计算、到达预警、ETA 偏移检测
- Smart_Remind：6 种场景化提醒（出发前/天气/行程中/事件/航班/到达前），含 TTS 文案
- Local_POI：多路召回 + Claude AI 评分 + 天气过滤 + POI 消歧/对比
- Parking_Find：普通停车场搜索 + 交通枢纽模式（虹桥/浦东机场预设）
- REST 测试端点（`/api/test/skills/*`），所有 Skill 可通过 API 独立验证

### ✅ Sprint 4: LangGraph 状态机 + Agent Core
- AgentState 完整状态定义（TypedDict，共 20+ 字段，含意图/目的地/方案/规划/行程生命周期）
- 6 个 Prompt builder 函数（系统/意图/澄清/推荐/规划/重规划）
- JourneyStore + MemorySaver 包装（单例、行程生命周期管理、历史记录、偏好更新）
- SkillExecutor（串行/并行/DAG wave execution，统一事件推送）
- 10 个 LangGraph 节点 + 4 个条件路由函数：
  - understanding（意图识别 + 槽位抽取 + Claude/降级）
  - clarifying（追问生成 + 3 轮限制 + interrupt）
  - destination_confirm（POI 消歧 + geocode 降级）
  - recommending（目标驱动/意图驱动 双路径 + 3 套方案）
  - planning（任务拆解 + DAG 执行 + 结果汇总）
  - ready（JourneyPlan 卡片 + 出发前总结 + interrupt）
  - in_progress（ETA 更新 + 卡片推送）
  - replanning（增量重规划 + 按原因分类 Skill 重算）
  - arriving（停车搜索 + 到达提醒）
  - completed（历史保存 + 偏好更新 + GCP 同步）
- JourneyOrchestrator（WS ↔ LangGraph 桥梁：stream_queue 流式事件、GCP 同步、后台 GCP 监听）
- /ws/chat 完整接入 Orchestrator（TEXT_INPUT / CLARIFY_REPLY / JOURNEY_ACTION）
- 中断点 interrupt_after = [clarifying, recommending, ready]
- GCP 后台监控：30s 周期 ETA 检查 + 事件队列监听，自动触发重规划/到达判定

### ✅ Sprint 5: 前端 UI 卡片 + 完善
- 类型系统：CardType / CardItem + 各卡片数据类型（ProposalData / JourneyReadyData / EtaData / ParkingData / ArrivingData / ClarifyData / SkillStatusData / ErrorData）
- journeyStore 重写：卡片状态管理 + 幂等去重（type + request_id）+ 交互方法（selectPlan / confirmDeparture / sendClarifyReply / selectCandidate）+ 派生 selector（路线polyline / 目的地 / ETA / 停车场）
- useChatWebSocket 增强：监听 card_update / state_change / clarify_question / skill_start / skill_result / error 事件并映射到 store
- 10 个卡片组件：
  - ProposalCard（3套方案选择）/ JourneyReadyCard（行程就绪+确认出发）/ RouteCard（路线概要）
  - EtaCard（实时ETA）/ ParkingCard（停车推荐）/ ArrivingCard（到达引导）
  - ClarifyCard（澄清追问+候选选择）/ SkillStatusCard（可折叠执行状态）/ ErrorCard（错误提示）
  - StateBanner（顶部状态横幅）
- ChatPanel 集成：StateBanner 顶部状态栏 + 卡片列表内嵌消息流
- MapView 增强：路线polyline绘制 / 目的地Marker / 停车场Marker / 右上角浮动ETA卡片 / 自动缩放至路线范围
- useAmap Hook 新增：setPolyline / fitToPoints（基于Bounds）/ removeOverlay 工具方法
- 深色主题统一设计（卡片样式、毛玻璃背景、indigo 主色调）

### ✅ Sprint 6: 全链路联调
- 数据契约对齐（停车场坐标字段、polyline 格式、卡片 payload）
- POI 候选选择流程修复（新增 `select_candidate` journey_action）
- Planning 节点任务拆解可靠性修复（内置规则计划替代 Claude 拆解）
- 卡片推送去重（节点自管，orchestrator 只推送状态变化）
- 前端交互状态优化（方案选中/规划中/出发确认后禁用）
- 消息与卡片按时间合并渲染
- 端到端完整流程测试通过

### 🚧 Sprint 7: Demo 场景验证 + 优化（进行中）
- 地图 polyline 编码格式修复（AMap v5 编码 → 明文解码）
- 地图覆盖层竞态条件修复（路线/目的地/停车 marker 响应式更新）
- 重规划功能修复（参数补全 + 字段归一化）
- Demo 自动模拟模式（车辆位置沿路线自动推进）
- 欢迎页 + 快捷建议按钮
- 卡片/消息入场动画
- 端到端场景验证（进行中）

## GCP 模块

| 模块 | 说明 |
|---|---|
| vehicle | 车辆信号（位置、挡位、车速、油量等） |
| in_cabin | 舱内感知（乘员、行为） |
| time | 时间上下文（时段、星期、节假日、季节） |
| weather | 天气实况与预报 |
| traffic | 交通状态（路线交通 + 区域交通） |
| journey | 旅程状态（进度、ETA、路线等） |
| transit | 航班信息（虚拟数据） |
| user_profile | 用户画像（出行偏好 + 生活服务偏好） |
