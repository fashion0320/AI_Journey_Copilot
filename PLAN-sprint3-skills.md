# Sprint 3：5 个 Skills 实现计划

## Context

Sprint 1（项目骨架 + GCP）和 Sprint 2（CPSP Adapters：高德、豆包语音、Claude）均已完成。现在进入 Sprint 3，实现 5 个核心 Skill。Skill 是 Agent 可调用的原子能力单元，每个 Skill 有统一的输入输出 Schema，可被 Claude 通过 tool-use 调用，也可被 Orchestrator（Sprint 4）串并行编排。

**关键约束**：当前 Claude API 代理不支持内置 web_search 工具，因此 Skill_Local_POI 中的 Web Search 双路召回暂用"纯高德+评分"方式实现，预留 web_search 接口（后续可接入第三方搜索 API 替换）。

## 架构设计

### BaseSkill 基类

所有 Skill 继承自 `BaseSkill`，统一以下规范：

```python
class BaseSkill:
    name: str                    # Skill 名称（tool-use 中的 tool name）
    description: str             # 描述（用于 system prompt 中的 tool 描述）
    input_schema: dict           # JSON Schema 输入格式
    gcp_dependencies: List[str]  # 声明依赖的 GCP 字段路径
    
    async def execute(self, params: dict, gcp_slice: dict, context: dict) -> SkillResult:
        """执行 Skill，返回结构化结果。"""
```

**SkillResult 结构**：
```python
@dataclass
class SkillResult:
    status: str          # "success" / "error" / "partial"
    data: dict           # 结构化结果数据（供 Agent/UI 使用）
    message_template: str  # 语音播报模板（TTS 用，带占位符）
    error: str | None = None
    card_data: dict | None = None  # UI 卡片数据（Sprint 5 用）
```

### Skill Tool 注册

提供 `get_skill_tools()` 函数，将所有 Skill 转换为 Anthropic tool-use 格式的工具定义列表，供 Sprint 4 Agent 使用。

### Skill handoff 机制

Skill 间的交接通过 Orchestrator 编排（Sprint 4），Sprint 3 阶段每个 Skill 独立，可单独测试验证。

## 5 个 Skill 详细设计

### 1. Skill_Route_Master（路线规划中枢）

**文件**: `app/skills/route_master.py`

**职责**：封装高德路径规划能力，支持单/多目的地、重规划、绕路检测。

**方法**:
- `route_single` — 单目的地路线规划
  - 输入：`{origin: {lat, lon}, destination: {lat, lon}, strategy?: "time_first"|"no_toll"|"shortest"|"balance"}`
  - 输出：`{route_id, distance_km, duration_min, toll_cny, polyline, waypoints, tmcs_summary, strategy}`
- `route_multi` — 多目的地顺序路线
  - 输入：`{origin, destinations: [{lat, lon, name}], strategy?, fixed_order?: bool}`
  - 输出：`{total_distance_km, total_duration_min, legs: [{distance, duration, polyline, waypoint_name}], overall_polyline}`
- `route_reroute` — 重规划
  - 输入：`{current_position, original_route_id, destination, reason?}`
  - 输出：`{new_route, delta_distance_km, delta_duration_min, reason}`
- `route_detour_check` — 绕路检测
  - 输入：`{current_position, original_route_polyline, threshold_km?}`
  - 输出：`{is_detour: bool, detour_distance_km, suggestion}`

**关键逻辑**:
- 用户偏好（`user_profile.travel_preferences.route_preference`）映射到高德 strategy 参数：
  - `time_first` → strategy=0（速度优先）
  - `no_toll` → strategy=3（不走高速+无收费）
  - `shortest` → strategy=2（距离优先）
  - `balance` → strategy=10（躲避拥堵）
- polyline 解析：高德 polyline 是经纬度编码字符串，保留原始格式（前端地图直接使用）
- tmcs 聚合：从返回的 tmcs 分段中聚合交通状态（smooth/slow/congested/severe），计算总延误时间
- 距离计算使用高德 distance API 做辅助

### 2. Skill_Dynamic_ETA（动态到达时间）

**文件**: `app/skills/dynamic_eta.py`

**职责**：基于当前车辆位置和路线计算剩余到达时间，检测 ETA 变化。

**方法**:
- `eta_query` — 查询当前 ETA
  - 输入：`{current_position, route_polyline, destination, total_distance_km?, total_duration_min?}`
  - 输出：`{remaining_min, remaining_km, eta_arrival_time, confidence_band_min, traffic_level}`
- `eta_arrival_alert` — 到达前 N 分钟预警
  - 输入：`{remaining_min, alert_before_min: int=5}`
  - 输出：`{should_alert: bool, message}`
- `eta_delta_check` — ETA 偏移检测
  - 输入：`{previous_eta_min, current_eta_min, threshold_min?: int=5}`
  - 输出：`{has_delta: bool, delta_min, delta_percent, is_significant, direction: "faster"|"slower"}`

**关键逻辑**:
- 基于高德距离测量 API 计算剩余距离和时间
- confidence_band 计算：根据交通状态波动范围估算（smooth ±5%，slow ±10%，congested ±20%，severe ±30%）
- ETA 偏移检测用于触发 Smart_Remind 和动态重规划（Sprint 4）

### 3. Skill_Smart_Remind（智能提醒中枢）

**文件**: `app/skills/smart_remind.py`

**职责**：场景化提醒生成，输出 TTS 播报文案和前端消息。

**方法**:
- `remind_pre_departure` — 出发前提醒
  - 输入：`{destination, weather, departure_time, items_to_bring?: []}`
  - 输出：`{title, message, tts_text, priority: "high"|"medium"|"low"}`
- `remind_weather` — 恶劣天气提醒
  - 输入：`{weather_live, is_driving: true}`
  - 输出：`{title, message, tts_text, severity: "info"|"warning"|"danger"}`
- `remind_in_journey` — 行程中播报（ETA 变化等）
  - 输入：`{eta_delta_min, current_traffic, next_stop}`
  - 输出：`{title, message, tts_text, urgency: "normal"|"elevated"}`
- `remind_event_change` — 事件变化提醒（航班延误等）
  - 输入：`{event_type: "flight", event_name, old_value, new_value, impact?}`
  - 输出：`{title, message, tts_text, action_suggestion?}`
- `remind_transit_dynamic` — 航班/高铁动态联动
  - 输入：`{flight_no, status, delay_min, terminal, gate?}`
  - 输出：`{title, message, tts_text, updated_eta?}`
- `remind_pre_arrival` — 到达前提醒
  - 输入：`{destination, parking_info?, eta_min, next_action?}`
  - 输出：`{title, message, tts_text, preparation_tips: []}`

**关键逻辑**:
- 每个提醒类型都生成 `tts_text` 字段，直接喂给 TTS 播报
- 消息模板使用自然语言，适合语音播报（短句、避免复杂标点）
- 优先级/严重程度用于前端 UI 展示（Sprint 5）
- 提醒生命周期管理（注册/触发/取消）由 Sprint 4 Orchestrator 负责

### 4. Skill_Local_POI（本地 POI 推荐）

**文件**: `app/skills/local_poi.py`

**职责**：POI 召回、筛选、综合评分推荐。

**方法**:
- `poi_recommend` — 推荐 POI（核心方法）
  - 输入：`{intent: "dining"|"coffee"|"shopping"|"leisure"|"parking"|"custom", keyword?: str, center_position, radius_km?: 3, user_profile?}`
  - 输出：`{candidates: [{id, name, address, location, distance, category, rating?, price?, tags, source: "amap"}], recommended: [top3_indices], reasoning: str}`
- `poi_resolve` — POI 消歧（当搜索词模糊时，列出候选供用户确认）
  - 输入：`{keyword, center_position, candidates_count?: 5}`
  - 输出：`{candidates: [{id, name, address, distance, category}]}`
- `poi_compare` — 候选对比
  - 输入：`{poi_ids: []}`
  - 输出：`{comparison: [{id, name, pros, cons, score}]}`

**关键逻辑**:
- **搜索词生成**：根据 intent + 用户画像 + 时间 + 天气生成差异化搜索关键词
  - 例：intent="dining" + Claire画像 + 晚餐时间 + 晴天 → ["bistro", "法餐", "氛围感餐厅", "静安寺附近餐厅"]
- **多路召回**（MVP 先用高德 3 路）：
  - 路1：关键词搜索（主关键词）
  - 路2：周边搜索 + 类型过滤
  - 路3：关键词 + 周边排序
  - ~~路4-6：Web Search 双路~~（TODO：API 代理不支持 Claude web_search，预留接口，后续接入第三方搜索）
- **去重合并**：按 ID 和名称相似度去重
- **Agent 综合评分**（用 Claude 做评分）：
  - 输入：候选列表 + 用户画像 + 当前上下文（时间/天气/位置）
  - 输出：评分排序 + top3 + 推荐理由
  - 使用 Claude 函数调用或结构化输出（JSON mode）
- **天气过滤**：雨/雷/风≥5级时屏蔽露台/户外类型的 POI
- **降级策略**：高德失败 → 返回空结果 + 建议扩大搜索半径

### 5. Skill_Parking_Find（停车场搜索）

**文件**: `app/skills/parking_find.py`

**职责**：目的地周边停车场搜索与推荐。

**方法**:
- `parking_search` — 普通停车场搜索
  - 输入：`{destination_position, radius_m?: 500, user_preference?: "convenience"|"cheap"|"balance", limit?: 5}`
  - 输出：`{parking_lots: [{id, name, address, location, distance_m, walk_min, price_info?, total_spaces?, type}], sorted_by: "distance"|"price"|"convenience"}`
- `parking_transit_hub` — 交通枢纽模式（机场/火车站）
  - 输入：`{hub_name: "虹桥T2"|"浦东T1"|..., destination_position, preference?: "P+parking"|"dropoff"}`
  - 输出：`{parking_lots: [{...}], recommended_index, entry_hint, terminal_guide}`

**关键逻辑**:
- 使用高德周边搜索 + 类型过滤（停车场类型 code 如 150000/150100 等）
- 步行时间估算：distance_m / 80m每分钟（成人步行速度）
- 用户偏好排序：
  - `convenience` — 按距离升序（近的在前）
  - `cheap` — 按价格升序（如有价格信息）
  - `balance` — 综合评分（距离*0.6 + 价格*0.4）
- 交通枢纽模式：识别已知枢纽（虹桥/浦东机场、各大火车站），提供航站楼指引
  - 机场：区分 P1/P2/P3/长途等停车场，标注接客/送客通道
  - 火车站：区分南北广场停车场

## 辅助文件

### `app/skills/base.py` — Skill 基类与公共类型
- `BaseSkill` 抽象基类
- `SkillResult` 数据类
- `SkillStatus` 枚举

### `app/skills/registry.py` — Skill 注册与工具导出
- `ALL_SKILLS` 列表
- `get_skill(name) -> BaseSkill` — 按名称获取 Skill
- `get_tools() -> List[dict]` — 导出所有 Skill 的 Anthropic 工具定义

### `app/skills/__init__.py` — 包导出

## REST 测试端点

新建 `app/api/skill_test.py`，提供每个 Skill 的测试接口：
- `POST /api/test/skills/route/single` — 测试路线规划
- `POST /api/test/skills/route/multi` — 测试多目的地路线
- `POST /api/test/skills/eta/query` — 测试 ETA 计算
- `POST /api/test/skills/remind/weather` — 测试天气提醒
- `POST /api/test/skills/poi/recommend` — 测试 POI 推荐
- `POST /api/test/skills/parking/search` — 测试停车场搜索

## 关键文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/skills/base.py` | 新建 | BaseSkill 基类 + SkillResult + 公共类型 |
| `app/skills/route_master.py` | 新建 | Skill_Route_Master |
| `app/skills/dynamic_eta.py` | 新建 | Skill_Dynamic_ETA |
| `app/skills/smart_remind.py` | 新建 | Skill_Smart_Remind |
| `app/skills/local_poi.py` | 新建 | Skill_Local_POI |
| `app/skills/parking_find.py` | 新建 | Skill_Parking_Find |
| `app/skills/registry.py` | 新建 | Skill 注册与工具导出 |
| `app/skills/__init__.py` | 修改 | 从空文件更新为导出接口 |
| `app/api/skill_test.py` | 新建 | Skill 测试 REST 端点 |
| `app/api/__init__.py` | 修改 | 注册 skill_test 路由 |
| `README.md` | 修改 | 标记 Sprint 3 完成 |

## 可复用的现有代码

- **AmapClient**（`app/adapters/amap.py`）：地理编码、POI 搜索、路径规划、距离测量、天气、交通态势 — 所有 Skill 都直接复用。
- **GCP models**（`app/gcp/models.py`）：Position、TravelPreferences、UserProfile、WeatherLive 等类型直接作为 Skill 输入/输出数据结构参考。
- **ContextStore**（`app/gcp/store.py`）：Skill 执行时通过 `gcp_slice` 获取所需上下文，`GlobalContext.slice_for_skill()` 已实现。
- **ClaudeClient**（`app/adapters/claude.py`）：POI 综合评分用 Claude 做推理，复用 `chat_stream`/`chat` 方法。
- **错误处理**（`app/core/errors.py`）：`AppError` 基类、`ApiResponse` 响应模型。
- **Adapter 模式**（`app/adapters/amap.py`）：lazy singleton + `get_logger` + settings 读取，Skills 遵循相似模式。

## 验证方式

1. **启动后端**：`uvicorn app.main:app --reload`，确认所有 Skill 模块无导入错误。
2. **Skill_Route_Master**：
   - 调用 `/api/test/skills/route/single`，输入陆家嘴→虹桥机场，验证返回路线距离、时间、polyline 正常。
3. **Skill_Dynamic_ETA**：
   - 调用 `/api/test/skills/eta/query`，验证剩余时间计算正确。
4. **Skill_Smart_Remind**：
   - 调用 `/api/test/skills/remind/weather`，输入雨天场景，验证生成合理的提醒文案。
5. **Skill_Local_POI**：
   - 调用 `/api/test/skills/poi/recommend`，输入"静安寺餐厅"+ Claire 画像，验证返回 3 个差异化推荐及理由。
6. **Skill_Parking_Find**：
   - 调用 `/api/test/skills/parking/search`，输入虹桥机场位置，验证返回停车场列表。
7. **注册验证**：
   - 调用 `get_tools()` 确认所有 Skill 都已注册为 Anthropic 工具格式。
