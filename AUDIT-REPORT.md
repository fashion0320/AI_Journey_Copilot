# AI Journey Copilot — MVP 需求审计报告

> 审计日期：2026-08-28
> 审计范围：`/Users/wenhao.shen/ai-journey-copilot` 全项目（后端 + 前端）
> 审计方式：静态代码审查，逐文件核对 MVP 规格说明书

---

## 目录

1. [总体评估](#1-总体评估)
2. [一、服务提供者 & 对话交互](#一服务提供者--对话交互)
3. [二、Agent 核心能力](#二agent-核心能力)
4. [三、5 个 MVP Skills 详细审计](#三5-个-mvp-skills-详细审计)
5. [四、全局上下文平台（GCP）](#四全局上下文平台gcp)
6. [五、CPSP 外部服务](#五cpsp-外部服务)
7. [六、系统架构](#六系统架构)
8. [七、状态机（10 个状态）](#七状态机10-个状态)
9. [八、UI 组件](#八ui-组件)
10. [九、缺失功能汇总清单](#九缺失功能汇总清单)

---

## 1. 总体评估

| 维度 | 完成度 | 说明 |
|------|--------|------|
| 后端架构 & 状态机 | ⚠️ 85% | 10 节点 LangGraph 完整，中断/恢复机制完整，部分边缘场景未覆盖 |
| 5 个 Skills 实现 | ⚠️ 75% | 核心能力齐全，部分 spec 中定义的 action/output 字段缺失 |
| GCP 上下文平台 | ✅ 90% | 8 模块模型完整，字段级订阅 + 事件扇出机制完善 |
| 外部服务集成 | ⚠️ 70% | 高德/豆包/Tavily 适配器齐全，但语音 WS 通路未接通 |
| 前端多模态 UI | ⚠️ 60% | 地图/聊天/GCP 面板核心功能可用，语音交互完全缺失 |
| 语音交互（ASR/TTS） | ❌ 20% | 后端适配器已写好，但 WebSocket 通路和前端 UI 均未接通 |

**总体结论**：核心 MVP 骨架（状态机 + Skills + GCP + 地图 UI）已基本搭建完成，可以跑通「文字输入 → 意图识别 → 方案推荐 → 行程规划 → 模拟行驶」的主流程。但语音交互、部分 Skill 的高级功能、GCP 事件驱动重规划、前端语音 UI 等功能尚未实现，距离完整 MVP 还有约 25-30% 的工作量。

---

## 一、服务提供者 & 对话交互

### 1.1 文本对话

| 需求 | 状态 | 证据 |
|------|------|------|
| WebSocket 文本消息通道 | ✅ 完整实现 | `backend/app/api/ws.py:27` — `TEXT_INPUT` 消息处理，调用 orchestrator.start_journey() |
| 流式 token 输出 | ✅ 完整实现 | `backend/app/adapters/claude.py` — `chat_stream()` 生成器，`TEXT_DELTA` 事件；前端 `journeyStore` 接收 `token_stream` |
| 多轮对话历史 | ✅ 实现 | `backend/app/adapters/claude.py:485` — `ConversationManager`，max_history=20，带 trim 逻辑 |
| 对话历史持久化 | ❌ 未实现 | 仅内存存储，连接断开即丢失；`JourneyStore` 只存元数据摘要 |

### 1.2 语音交互（ASR + TTS）

| 需求 | 状态 | 证据 |
|------|------|------|
| 火山引擎 ASR 适配器 | ⚠️ 后端已实现但未接入 | `backend/app/adapters/volc_asr.py` — 完整双向流式二进制协议实现（560 行），但 `app/api/ws.py:70-78` 中 `AUDIO_START/AUDIO_CHUNK/AUDIO_STOP` 均返回 `NOT_IMPLEMENTED` |
| 火山引擎 TTS 适配器 | ⚠️ 后端已实现但未接入 | `backend/app/adapters/volc_tts.py` — HTTP 非流式 MP3 返回（212 行），但 WS 通道未调用 |
| 前端麦克风录音 | ❌ 未实现 | `frontend/src/components/ChatPanel/ChatPanel.tsx` — 只有文本输入框 + 发送按钮，无麦克风按钮 |
| 前端音频播放 | ❌ 未实现 | 前端无 `Audio` 播放逻辑，无 TTS 音频处理 |
| 端到端语音对话通路 | ❌ 未打通 | 前后端均未实现语音 WS 消息的实际处理 |

### 1.3 LLM 能力

| 需求 | 状态 | 证据 |
|------|------|------|
| Anthropic Claude API 集成 | ✅ 完整实现 | `backend/app/adapters/claude.py` — `AsyncAnthropic` 客户端，流式 + 工具调用循环 |
| Tool Use（函数调用） | ✅ 完整实现 | `claude.py:169` — `_handle_tool_use()` 自动工具调用循环，max_tool_loops=5 |
| Web Search Beta | ✅ 已配置 | `claude.py:32` — `beta.headers` 包含 `web_search_20250305`；但实际未在对话中主动启用（仅配置了 header） |

---

## 二、Agent 核心能力

### 2.1 意图识别与理解

| 需求 | 状态 | 证据 |
|------|------|------|
| 目标驱动型（Goal-driven）识别 | ✅ 完整实现 | `backend/app/agent/nodes/understanding.py` — Claude JSON 解析 + 关键词规则降级 |
| 意图驱动型（Intent-driven）识别 | ✅ 完整实现 | 同上，支持 dining/drinks/coffee/shopping/leisure 等意图分类 |
| 槽位抽取 | ✅ 实现 | `prompts.py:127` — `INTENT_OUTPUT_SCHEMA` 定义 8 个槽位字段 |
| 低置信度澄清 | ✅ 实现 | `understanding.py:37` — clarify_count >= 2 时放弃并道歉；`prompts.py:226` — 澄清追问 prompt |
| 意图澄清（最多 2 轮） | ✅ 完整实现 | `graph.py:143` — interrupt_after=["clarifying", ...]；`understanding.py:37` — 2 轮上限 |
| 规则降级（无 LLM 时） | ✅ 完整实现 | `understanding.py:257` — `_fallback_understanding()` 关键词匹配 |

### 2.2 目的地确认 & 方案推荐

| 需求 | 状态 | 证据 |
|------|------|------|
| 目的地 POI 解析（消歧） | ✅ 完整实现 | `nodes/destination_confirm.py` — 调用 `local_poi.poi_resolve`，0/1/多候选分别处理 |
| 多候选选择澄清 | ✅ 实现 | 多候选时推入 `clarify_question`，用户选择后确认 |
| 3 套差异化方案推荐 | ✅ 完整实现 | `nodes/recommending.py` — 目标驱动型并行 3 种路线策略 + 停车；意图驱动型 POI 推荐 |
| Claude 方案整合 | ✅ 实现 | `prompts.py:272` — `PROPOSAL_OUTPUT_SCHEMA`，含 recommended_index |
| 方案选择确认 | ✅ 实现 | `orchestrator.py` — `handle_journey_action("select_plan")` |

### 2.3 旅程编排 & 持续感知

| 需求 | 状态 | 证据 |
|------|------|------|
| Skill 任务编排（DAG 波次执行） | ✅ 完整实现 | `agent/skill_executor.py` — `execute_plan()` 实现 DAG wave 调度，支持 parallel_group + deps |
| 并行执行 | ✅ 实现 | `skill_executor.py:105` — `run_parallel()` 用 `asyncio.gather` |
| 串行执行 | ✅ 实现 | `skill_executor.py:92` — `run_serial()` 传递 `prev_{task_id}` 上下文 |
| Claude 任务拆解 | ⚠️ 已禁用 | `nodes/planning.py` — 注释说明"Claude 分解容易产生幻觉"，改用硬编码 `_build_default_plan()` |
| GCP 实时感知（30s 轮询） | ⚠️ 部分实现 | `orchestrator.py` — `_gcp_monitor_loop()` 每 30s 检查 ETA；但只有 ETA 检查，未监听 GCP 事件队列的实时变化 |
| 事件驱动重规划 | ❌ 未实现 | GCP `store.py` 有事件扇出机制，但 orchestrator 未订阅；`replan_reason` 只能通过人工或定时 ETA 检查触发 |
| 重规划触发判断 Prompt | ⚠️ 已定义未使用 | `prompts.py:418` — `build_replan_detection_prompt()` 存在，但代码中未调用 |
| Demo 模拟行驶 | ✅ 完整实现 | `orchestrator.py` — `_advance_demo_vehicle()`，`JOURNEY_DEMO_SIMULATION=1` 时每 30s 推进 ~12% |

---

## 三、5 个 MVP Skills 详细审计

### 3.1 RouteMaster（路线大师）

**Spec 要求**：4 个 action — `route_single` / `route_multi` / `route_optimize_order` / `route_reroute` / `route_detour_check`

| 需求 | 状态 | 证据 |
|------|------|------|
| `route_single` 单起点单终点路线 | ✅ 完整实现 | `skills/route_master.py:130` — `_execute_route_single()`，支持 4 种策略 |
| `route_multi` 多点路线 | ⚠️ 部分实现 | `route_master.py:211` — `_execute_route_multi()`，但仅支持简单多点（未明确途经点 vs 终点区分） |
| `route_optimize_order` 途经点顺序优化 | ❌ 未实现 | 代码中无此 action；spec 明确要求但完全缺失 |
| `route_reroute` 动态绕路/重算 | ✅ 实现 | `route_master.py:262` — `_execute_route_reroute()` |
| `route_detour_check` 绕路检测 | ✅ 实现 | `route_master.py:308` — `_execute_detour_check()` |
| 高德 v5 驾车路线 API | ✅ 完整实现 | `adapters/amap.py:162` — `direction_driving()`，含 polyline/tmcs/cost/navi show_fields |
| 路线策略映射 | ✅ 实现 | `route_master.py:83` — `_strategy_to_amap_code()`，4 种偏好 → 高德 strategy code |
| 多段 polyline 归一化 | ✅ 实现 | `route_master.py:425` — `_normalize_polyline()`，逐段解码后拼接 |
| TMC 路况聚合 | ✅ 实现 | `route_master.py:458` — `_aggregate_tmcs()`，按拥堵段比例计算 overall_status |
| GCP 依赖声明 | ✅ 实现 | `gcp_dependencies = ["vehicle.position", "user_profile.travel_preferences.route_preference"]` |

**缺失项**：`route_optimize_order` 途经点顺序优化（spec 明确列出，代码完全缺失）

### 3.2 DynamicETA（动态 ETA）

**Spec 要求**：`eta_query` / `eta_arrival_alert` / 连续推送能力（monitoring_started, alert_will_fire_at）

| 需求 | 状态 | 证据 |
|------|------|------|
| `eta_query` ETA 查询 | ✅ 完整实现 | `skills/dynamic_eta.py:102` — `_execute_eta_query()`，高德距离 API + 置信带 |
| `eta_arrival_alert` 到达提醒设置 | ⚠️ 部分实现 | `dynamic_eta.py:156` — `_execute_eta_arrival_alert()`，但只返回静态结果，没有实际的定时提醒机制 |
| `eta_delta_check` ETA 变化检测 | ✅ 实现 | `dynamic_eta.py:203` — `_execute_eta_delta_check()` |
| 置信区间（Confidence Band） | ✅ 实现 | `dynamic_eta.py:71` — `_confidence_band_pct()`，按路况等级返回 5%-30% |
| 连续推送 / 监控启动 | ❌ 未实现 | spec 要求的 `monitoring_started` / `alert_will_fire_at` 输出字段缺失；没有后台持续监控机制 |
| 多维度 ETA（路况/天气/事件） | ⚠️ 部分实现 | 只考虑了路况等级的置信带，未单独考虑天气和事件因素 |
| GCP 依赖声明 | ✅ 实现 | `gcp_dependencies = ["vehicle.position", "vehicle.speed_kmh", "traffic.on_route.overall_status"]` |

**缺失项**：连续推送能力（monitoring_started / alert_will_fire_at）、ETA 后台持续监控机制

### 3.3 SmartRemind（智能提醒）

**Spec 要求**：7 种 remind_type — `pre_departure` / `weather` / `in_journey` / `event_change` / `transit_dynamic` / `pre_arrival` / `remind_journey_type`

| 需求 | 状态 | 证据 |
|------|------|------|
| `pre_departure` 出发前提醒 | ✅ 完整实现 | `skills/smart_remind.py:112` — `_remind_pre_departure()`，含路线长度/时长/天气检查/路况 |
| `weather` 天气提醒 | ✅ 完整实现 | `smart_remind.py:149` — `_remind_weather()`，三级 severity（danger/warning/info）+ 驾驶建议 |
| `in_journey` 行程中提醒 | ✅ 实现 | `smart_remind.py:189` — `_remind_in_journey()`，含剩余时间/路况更新 |
| `event_change` 事件变更提醒 | ✅ 实现 | `smart_remind.py:217` — `_remind_event_change()`，含用户建议 |
| `transit_dynamic` 交通枢纽动态 | ✅ 实现 | `smart_remind.py:248` — `_remind_transit_dynamic()`，航班状态中文映射 + 延误提醒 |
| `pre_arrival` 到达前提醒 | ✅ 完整实现 | `smart_remind.py:291` — `_remind_pre_arrival()`，含停车引导/到达时间/整理提醒 |
| `remind_journey_type` 行程类型提醒 | ❌ 未实现 | spec 列出第 7 种类型，代码中完全缺失 |
| TTS 友好文本生成 | ✅ 实现 | 所有 remind 函数都生成自然的中文播报文本 |
| 严重度分级 | ✅ 实现 | 天气提醒有 danger/warning/info 三级 |
| GCP 依赖声明 | ✅ 实现 | `gcp_dependencies = ["weather.live", "time.datetime_iso", "transit"]` |

**缺失项**：`remind_journey_type`（行程类型提醒，spec 的第 7 种类型）

### 3.4 LocalPOI（本地 POI 推荐）

**Spec 要求**：多路召回（高德 3 结构化 + Web 3 氛围 → Web 映射到高德 → 6 统一包 → Agent 选 3）

| 需求 | 状态 | 证据 |
|------|------|------|
| `poi_recommend` POI 推荐 | ✅ 完整实现 | `skills/local_poi.py:165` — `_execute_poi_recommend()`，多路召回 + AI 排序 |
| `poi_resolve` 名称解析 | ✅ 完整实现 | `local_poi.py:310` — `_execute_poi_resolve()`，用于目的地消歧 |
| `poi_compare` 多 POI 对比 | ✅ 实现 | `local_poi.py:353` — `_execute_poi_compare()` |
| `poi_search` 关键词搜索 | ✅ 实现 | `local_poi.py:393` — `_execute_poi_search()` |
| 高德结构化召回（3 路） | ✅ 实现 | `local_poi.py:76` — 文本搜索 + 周边类型搜索 + 周边关键词搜索 |
| Web 搜索氛围召回（Tavily） | ✅ 实现 | `local_poi.py:116` — Tavily 双 query 搜索，有 API key 时启用 |
| Web 结果 → 高德映射 | ❌ 未实现 | spec 要求"Web 候选映射到高德 POI"，但代码直接合并所有候选到一个池子，没有做 Web→Amap 的关联映射 |
| 6 个统一候选包 → Agent 选 3 | ⚠️ 实现方式不同 | 代码用 Claude AI 评分从所有候选中选 Top N，不是 spec 描述的"6 统一包 → Agent 选 3"流程 |
| 天气过滤（恶劣天气排除户外） | ✅ 实现 | `local_poi.py:440` — `_filter_by_weather()` |
| 用户画像偏好匹配 | ✅ 实现 | 评分时考虑 lifestyle_preferences |
| 无 Tavily 时降级 | ✅ 实现 | 无 API key 时只使用高德结果 |
| GCP 依赖声明 | ✅ 实现 | `gcp_dependencies = ["vehicle.position", "weather.live.weather", "user_profile.lifestyle_preferences"]` |

**缺失项**：Web 结果到高德 POI 的映射环节（spec 中的关键流程，当前实现跳过了此步骤）

### 3.5 ParkingFind（停车查找）

**Spec 要求**：`parking_search` / `parking_transit_hub`

| 需求 | 状态 | 证据 |
|------|------|------|
| `parking_search` 普通停车场搜索 | ✅ 完整实现 | `skills/parking_find.py:120` — `_execute_parking_search()`，高德周边搜索 + 类型过滤 |
| `parking_transit_hub` 交通枢纽停车 | ✅ 完整实现 | `parking_find.py:234` — `_execute_transit_hub()`，虹桥/浦东机场硬编码数据 |
| 虹桥 T2/T1、浦东 T1/T2 数据 | ✅ 实现 | `parking_find.py:22` — 4 个机场的停车场/楼层/费用/步行时间详细数据 |
| 偏好排序（便捷/便宜/均衡） | ✅ 实现 | `parking_find.py:314` — `_sort_by_preference()`，3 种排序策略 |
| 枢纽自动检测 | ✅ 实现 | `parking_find.py:337` — `_detect_transit_hub()`，从目的地名称识别 |
| 停车场类型码覆盖 | ✅ 实现 | 覆盖 150900-150906 全部 7 类停车场 |
| GCP 依赖声明 | ✅ 实现 | `gcp_dependencies = ["user_profile.travel_preferences.parking_preference"]` |

---

## 四、全局上下文平台（GCP）

### 4.1 8 大模块覆盖

| 模块 | 状态 | 证据 |
|------|------|------|
| vehicle 车辆信号 | ✅ 完整实现 | `gcp/models.py:31` — `VehicleSignals`，含 position/gear/speed/fuel/ignition/mileage |
| in_cabin 舱内感知 | ✅ 模型完整 | `gcp/models.py:57` — `InCabinPerception`，含 driver/passengers/behavior/dangerous_behavior；**但前端 GCP 面板无此 Tab** |
| time 时间上下文 | ✅ 完整实现 | `gcp/models.py:80` — `TimeContext`，含 datetime/time_bucket/weekday/weekend/holiday/season，9 个时段 |
| weather 天气 | ✅ 完整实现 | `gcp/models.py:100` — `WeatherContext`，含 live + forecasts（3 天预报） |
| traffic 交通 | ✅ 完整实现 | `gcp/models.py:136` — `TrafficContext`，含 on_route + region |
| journey 行程状态 | ✅ 完整实现 | `gcp/models.py:171` — `JourneyState`，含 status/route/progress/eta 等 |
| transit 交通枢纽 | ✅ 完整实现 | `gcp/models.py:208` — `TransitContext`，完整航班信息（航班号/状态/延误/航站楼/登机口/STA/ATA 等） |
| user_profile 用户画像 | ✅ 完整实现 | `gcp/models.py:237` — `UserProfile`，含 travel_preferences + lifestyle_preferences + frequent_pois + family_members |

### 4.2 GCP 机制

| 需求 | 状态 | 证据 |
|------|------|------|
| 字段级订阅（Field-level subscription） | ✅ 完整实现 | `gcp/store.py:95` — `subscribe()` 支持点路径（如 "vehicle.position.lat"），回调精确匹配 |
| 模块级前缀监听 | ✅ 实现 | `store.py:138` — 前缀匹配（如 "vehicle.*"） |
| 事件扇出队列（Fan-out） | ✅ 完整实现 | `store.py:163` — `subscribe_events()` / `unsubscribe_events()`，每个 WS 客户端独立队列 |
| 原子更新 + 异步锁 | ✅ 实现 | `store.py:62` — `update()` 方法，带 `_lock` asyncio 锁 |
| 扁平化字典工具 | ✅ 实现 | `store.py:205` — `flatten_dict()` |
| Skill 切片（slice_for_skill） | ✅ 实现 | `gcp/models.py:283` — `GlobalContext.slice_for_skill()`，按字段路径提取子集 |

### 4.3 GCP 可视化控制面板

| 需求 | 状态 | 证据 |
|------|------|------|
| 车辆信号 Tab | ✅ 实现 | `frontend/src/components/GcpPanel/GcpPanel.tsx:96` — 显示位置/档位/速度/油量/点火/里程 |
| 时间上下文 Tab | ✅ 实现 | 同上 — 时段/星期/季节/日期/周末 |
| 天气 Tab | ✅ 实现 | 同上 — 天气状况下拉/温度/风/城市 |
| 交通 Tab | ✅ 实现 | 同上 — on_route 整体状态/最严重拥堵/总延误/平均速度 |
| 航班（交通枢纽）Tab | ✅ 实现 | 同上 — 航班号/状态/延误/航站楼/STA/ATA |
| 场景预设 Tab | ✅ 实现 | 同上 — Amy 接机/Claire 闺蜜夜 两个预设 + 两个用户画像切换 |
| in_cabin 舱内感知 Tab | ❌ 缺失 | 后端模型完整，但前端无此 Tab |
| user_profile 画像编辑 Tab | ❌ 缺失 | 只有预设切换按钮，无可视化编辑界面 |
| 行程状态显示 & 控制 | ❌ 缺失 | GCP 面板无 journey 状态展示，也没有手动触发状态变更的控制 |
| 实时事件流显示 | ❌ 缺失 | 只有状态快照，没有事件流日志面板 |

---

## 五、CPSP 外部服务

### 5.1 高德地图（Amap）

| 需求 | 状态 | 证据 |
|------|------|------|
| 地理编码（geocode） | ✅ 实现 | `adapters/amap.py:71` — `geocode()` / `geocode_to_lnglat()` |
| 逆地理编码（regeocode） | ✅ 实现 | `amap.py:88` — `regeocode()` |
| POI 文本搜索（v5） | ✅ 实现 | `amap.py:105` — `place_text()` |
| POI 周边搜索（v5） | ✅ 实现 | `amap.py:122` — `place_around()` |
| POI 详情（v5） | ✅ 实现 | `amap.py:138` — `place_detail()` |
| 驾车路线规划（v5） | ✅ 完整实现 | `amap.py:162` — `direction_driving()`，含 polyline/tmcs/cost/navi show_fields |
| 距离测量 API | ✅ 实现 | `amap.py:222` — `distance()` |
| 天气查询 | ✅ 实现 | `amap.py:238` — `weather_live()` / `weather_forecast()` |
| 交通态势（矩形/圆形/道路） | ✅ 实现 | `amap.py:268` — `traffic_status_rectangle()` / `circle()` / `road()` |
| 前端 JS API v2.0 | ✅ 完整实现 | `frontend/src/hooks/useAmap.ts` — 异步加载，marker/polyline/circle/fitBounds 全套 |
| 暗色地图样式 | ✅ 实现 | `useAmap.ts:45` — `mapStyle: 'amap://styles/darkblue'` |

### 5.2 火山引擎（豆包）语音

| 需求 | 状态 | 证据 |
|------|------|------|
| 流式 ASR（WebSocket 二进制协议） | ⚠️ 适配器已实现 | `adapters/volc_asr.py` — 完整自定义帧协议（4 字节头 + sequence + payload_size + gzip JSON），支持 PCM 16kHz；**但 WS 通路未接入** |
| ITN（逆文本归一化） | ✅ 已配置 | `volc_asr.py:121` — `enable_itn=True` |
| 标点符号 | ✅ 已配置 | `volc_asr.py:122` — `enable_punctuation=True` |
| 非流式二次纠错 | ✅ 已配置 | `volc_asr.py:123` — `enable_nonstream_rescore=True` |
| 非流式 TTS（HTTP MP3） | ⚠️ 适配器已实现 | `adapters/volc_tts.py` — 24kHz 采样，128kbps 码率，女声；**但 WS 通路未接入** |
| 语音参数调节（语速/音量/音调） | ✅ 实现 | `volc_tts.py:61` — `_convert_ratio()` |

### 5.3 Web 搜索（Tavily）

| 需求 | 状态 | 证据 |
|------|------|------|
| 通用搜索 API | ✅ 完整实现 | `adapters/tavily.py` — `search()`，支持 basic/advanced depth, images, answer, domain filtering |
| POI 专用搜索（web → POI 格式转换） | ✅ 实现 | `tavily.py:172` — `search_poi_web()`，标准化为 POI 格式并打分 |
| POI 信息补充提取 | ✅ 实现 | `tavily.py:230` — `extract_poi_info()` |
| Claude 原生 Web Search | ⚠️ 仅配置未使用 | `adapters/claude.py:32` — 配置了 `web_search_20250305` beta header，但实际对话中未调用 |

---

## 六、系统架构

### 6.1 后端架构

| 需求 | 状态 | 证据 |
|------|------|------|
| FastAPI 服务框架 | ✅ 完整实现 | `app/main.py` — FastAPI lifespan + CORS + 路由注册 |
| 双 WebSocket 通道（chat + gcp） | ✅ 完整实现 | `app/api/ws.py` — `/ws/chat` 聊天通道；`/ws/gcp` GCP 事件广播通道 |
| JourneyOrchestrator 编排器 | ✅ 完整实现 | `agent/orchestrator.py` — 634 行，管理 graph/stream/SkillExecutor/GCP 监控 |
| LangGraph 状态机 | ✅ 完整实现 | `agent/graph.py` — StateGraph + MemorySaver + interrupt_after |
| SkillExecutor 执行器 | ✅ 完整实现 | `agent/skill_executor.py` — 串行/并行/DAG 波次执行 |
| ContextStore 上下文存储 | ✅ 完整实现 | `gcp/store.py` — 单例 + 字段订阅 + 事件扇出 |
| JourneyStore 行程存储 | ✅ 内存实现 | `agent/store.py` — 内存存储 + MemorySaver 包装，MVP 阶段够用 |
| 配置管理 | ✅ 实现 | `app/core/config.py` — pydantic-settings，从环境变量读取 |
| 日志系统 | ✅ 实现 | `app/core/logging.py` |
| 统一异常处理 | ✅ 实现 | `app/core/exception_handlers.py` |

### 6.2 前端架构

| 需求 | 状态 | 证据 |
|------|------|------|
| React 18 + TypeScript + Vite | ✅ 完整实现 | `frontend/package.json` |
| Zustand 状态管理 | ✅ 完整实现 | `frontend/src/store/` — journeyStore/chatStore/gcpStore |
| 组件化架构 | ✅ 实现 | `components/` — GcpPanel/MapView/ChatPanel + 各卡片组件 |
| WebSocket 客户端封装 | ✅ 实现 | `hooks/useChatWebSocket.ts` / `hooks/useGcpWebSocket.ts` |
| 高德地图 Hook | ✅ 完整实现 | `hooks/useAmap.ts` — 异步加载 + marker/polyline/circle/fitBounds |

---

## 七、状态机（10 个状态）

| 状态节点 | 状态 | 证据 | 说明 |
|----------|------|------|------|
| 0. understanding 理解意图 | ✅ 完整实现 | `nodes/understanding.py` — 400 行 | Claude + 规则降级双路径 |
| 1. clarifying 澄清追问 | ✅ 完整实现 | `nodes/clarifying.py` — 132 行 | 最多 2 轮，超限道歉结束 |
| 2. destination_confirm 目的地确认 | ✅ 完整实现 | `nodes/destination_confirm.py` — 193 行 | 0/1/多候选分支处理 |
| 3. recommending 方案推荐 | ✅ 完整实现 | `nodes/recommending.py` — 355 行 | 目标驱动(3路线+停车)/意图驱动(POI推荐) 双路径 |
| 4. planning 任务规划 | ⚠️ 部分实现 | `nodes/planning.py` — 391 行 | Claude 拆解已禁用，用硬编码默认计划 |
| 5. ready 行程就绪 | ✅ 完整实现 | `nodes/ready.py` — 167 行 | 组装 JourneyPlan，推卡片，同步 GCP |
| 6. in_progress 行程中 | ⚠️ 部分实现 | `nodes/in_progress.py` — 94 行 | 只更新 ETA，缺少完整的持续感知逻辑 |
| 7. replanning 重规划 | ⚠️ 部分实现 | `nodes/replanning.py` — 221 行 | 关键字匹配触发 skill 重跑，未使用 LLM 判断 |
| 8. arriving 即将到达 | ✅ 完整实现 | `nodes/arriving.py` — 131 行 | 停车搜索 + 到达提醒 |
| 9. completed 行程完成 | ✅ 完整实现 | `nodes/completed.py` — 80 行 | 生成总结 + 偏好学习 + 历史记录 |

**中断点配置**：`graph.py:143` — `interrupt_after=["clarifying", "recommending", "ready", "in_progress"]` ✅

**状态流转完整性**：10 个节点全部注册，边关系完整，可正常流转。

**注意**：`JourneyStatus` 枚举有 13 个值（含 idle/ended 等），但核心状态机是 10 个节点，符合 spec 要求。

---

## 八、UI 组件

### 8.1 行程规划 UI（Journey Plan UI）

| 组件 | 状态 | 证据 |
|------|------|------|
| 路线卡片（RouteCard） | ✅ 实现 | `components/Cards/RouteCard.tsx` |
| ETA 卡片（EtaCard） | ✅ 实现 | `components/Cards/EtaCard.tsx` |
| 方案卡片（ProposalCard） | ✅ 实现 | `components/Cards/ProposalCard.tsx` — 3 套方案选择 |
| 停车卡片（ParkingCard） | ✅ 实现 | `components/Cards/ParkingCard.tsx` |
| 行程就绪卡片（JourneyReadyCard） | ✅ 实现 | `components/Cards/JourneyReadyCard.tsx` |
| 到达引导卡片（ArrivingCard） | ✅ 实现 | `components/Cards/ArrivingCard.tsx` |
| 澄清卡片（ClarifyCard） | ✅ 实现 | `components/Cards/ClarifyCard.tsx` — 支持文字追问 + 候选列表选择 |
| 技能状态卡片（SkillStatusCard） | ✅ 实现 | `components/Cards/SkillStatusCard.tsx` |
| 错误卡片（ErrorCard） | ✅ 实现 | `components/Cards/ErrorCard.tsx` |
| 状态横幅（StateBanner） | ✅ 实现 | `components/Cards/StateBanner.tsx` — 显示当前状态 |

### 8.2 地图组件

| 功能 | 状态 | 证据 |
|------|------|------|
| 车辆位置标记 | ✅ 实现 | `MapView.tsx` — 靛蓝色圆形车辆 marker |
| 路线 Polyline | ✅ 实现 | `MapView.tsx` — 靛蓝色 6px 路线，自动适配视野 |
| 目的地标记 | ✅ 实现 | 红橙渐变 + 📍 emoji |
| 停车场标记 | ✅ 实现 | 绿色 P（推荐）/ 灰色 P（其他），最多 5 个 |
| ETA 悬浮层 | ✅ 实现 | 右上角 ETA mini overlay，含剩余时间/到达时间/路况点 |
| 行程状态 chip | ✅ 实现 | 顶部状态 chip + 用户 chip |
| 自动视野适配 | ✅ 实现 | 首次渲染路线时 fitBounds，idle 时重置 |

### 8.3 聊天面板

| 功能 | 状态 | 证据 |
|------|------|------|
| 消息列表 | ✅ 实现 | `ChatPanel.tsx` — 消息 + 卡片混合列表，按时间排序 |
| 流式文本显示 | ✅ 实现 | token_stream 事件驱动的流式显示 |
| 建议 chip | ✅ 实现 | 欢迎界面 3 个快捷建议 |
| 文本输入 | ✅ 实现 | textarea + 发送按钮 |
| 连接状态指示 | ✅ 实现 | 顶部连接状态灯 |
| 语音输入按钮 | ❌ 缺失 | 无麦克风按钮 |
| 语音播放控制 | ❌ 缺失 | 无 TTS 播放/暂停/停止控制 |
| 消息时间戳 | ❌ 缺失 | 消息显示无时间戳 |
| 滚动到底部 | ✅ 实现 | 自动滚动到底部 |

### 8.4 GCP 控制面板

参见 [4.3 GCP 可视化控制面板](#43-gcp-可视化控制面板)

---

## 九、缺失功能汇总清单

### P0 — 核心流程阻断（必须修复才能算完整 MVP）

| # | 缺失功能 | 影响范围 | 涉及文件 |
|---|----------|----------|----------|
| 1 | **语音交互端到端通路未打通** | 核心交互方式，spec 明确要求语音 | 后端 `api/ws.py`（AUDIO 消息返回 NOT_IMPLEMENTED）；前端 ChatPanel（无录音/播放 UI） |
| 2 | **GCP 事件驱动重规划未实现** | 持续感知能力的核心 | orchestrator 未订阅 GCP 事件队列；replan_detection_prompt 定义了但未使用 |
| 3 | **路线大师缺少 route_optimize_order** | Skill 功能完整性 | `skills/route_master.py` — spec 列出的 action 缺失 |
| 4 | **动态 ETA 缺少连续推送/监控能力** | Skill 功能完整性 | `skills/dynamic_eta.py` — 只有查询，没有后台监控和主动推送 |

### P1 — 重要功能缺失（影响体验但不阻断主流程）

| # | 缺失功能 | 影响范围 | 涉及文件 |
|---|----------|----------|----------|
| 5 | **智能提醒缺少 remind_journey_type** | Skill 功能完整性 | `skills/smart_remind.py` — 第 7 种类型缺失 |
| 6 | **LocalPOI 缺少 Web→Amap 映射流程** | 推荐质量 | `skills/local_poi.py` — 直接合并候选池，跳过 spec 要求的映射步骤 |
| 7 | **Planning 节点 Claude 拆解禁用** | 智能性 | `nodes/planning.py` — 用硬编码默认计划替代 LLM 拆解 |
| 8 | **GCP 面板缺少 in_cabin Tab** | 面板完整性 | `components/GcpPanel/GcpPanel.tsx` — 后端模型已有，前端未展示 |
| 9 | **GCP 面板缺少 user_profile 编辑 Tab** | 面板完整性 | 同上 — 只有预设切换，无可视化编辑 |
| 10 | **GCP 面板缺少 journey 状态展示/控制** | 面板完整性 | 无行程状态可视化和手动控制 |

### P2 — 体验优化（建议补充）

| # | 缺失功能 | 影响范围 | 涉及文件 |
|---|----------|----------|----------|
| 11 | **对话历史持久化** | 数据留存 | `agent/store.py` — 仅内存，连接断开丢失 |
| 12 | **聊天消息时间戳** | UI 体验 | `ChatPanel.tsx` |
| 13 | **GCP 事件流日志面板** | 调试/展示 | 前端无事件流显示 |
| 14 | **Claude 原生 Web Search 实际调用** | 搜索能力 | `adapters/claude.py` — 配置了 beta header 但未在对话中使用 |
| 15 | **前端 polyline 解码（encoded 格式）** | 地图显示 | `journeyStore.ts:decodeAmapPolyline` — 只支持明文分号格式，v5 API 返回的 encoded 格式无法解析 |
| 16 | **更多场景预设** | Demo 丰富度 | `gcp/presets.py` — 目前只有 2 个场景 |

---

## 附录：文件级统计

### 后端（Python）

| 模块 | 文件数 | 大致行数 | 完成度 |
|------|--------|----------|--------|
| GCP（模型/存储/预设） | 4 | ~820 行 | 95% |
| Agent（状态机/节点/编排） | 15+ | ~3000 行 | 85% |
| Skills（5 个 Skill） | 7 | ~2800 行 | 75% |
| Adapters（5 个适配器） | 5 | ~2000 行 | 80% |
| API（WS/REST） | 4 | ~500 行 | 70% |
| Core（配置/日志/工具） | 6 | ~400 行 | 90% |
| **合计** | **40+** | **~9500 行** | **82%** |

### 前端（React/TS）

| 模块 | 文件数 | 大致行数 | 完成度 |
|------|--------|----------|--------|
| 组件（面板/卡片/地图） | 15+ | ~2500 行 | 70% |
| Store（Zustand） | 3 | ~600 行 | 80% |
| Hooks（WS/地图） | 3 | ~500 行 | 85% |
| 类型定义 | 1 | ~400 行 | 95% |
| 服务/页面/工具 | 5 | ~300 行 | 75% |
| **合计** | **27+** | **~4300 行** | **75%** |

---

> 报告生成时间：2026-08-28
> 审计方式：静态代码审查，基于 `ai-journey-copilot` 当前代码库快照
