"""Agent 各节点的 Prompt 模板。

所有 Prompt 以 builder 函数形式导出，动态注入 GCP 上下文和可用 Skill 信息。
遵循原则：中文、简洁专业、适合驾驶场景、结构化输出用 JSON Schema 约束。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


# ==================== 基础系统 Prompt ====================

def build_system_prompt(
    gcp_snapshot: Optional[Dict[str, Any]] = None,
    skill_descriptions: Optional[List[Dict[str, Any]]] = None,
    user_query: Optional[str] = None,
) -> str:
    """构建完整系统提示词。

    包含：角色定义 + 能力边界 + GCP 上下文 + Skill 能力清单。
    user_query 用于判断是否需要注入航班等敏感上下文。
    """
    skill_section = ""
    if skill_descriptions:
        items = "\n".join(
            f"- **{s['name']}**：{s['description']}"
            for s in skill_descriptions
        )
        skill_section = f"""
## 可用工具（Skills）

你可以通过调用以下工具来获取实时信息和执行任务：
{items}

你可以在需要时调用这些工具。调用工具时使用 function calling 格式。
"""

    gcp_section = ""
    if gcp_snapshot:
        gcp_section = _build_gcp_context(gcp_snapshot, user_query)

    return f"""你是一位专业的座舱 AI Journey Copilot（旅程副驾驶），为用户提供智能出行规划与陪伴服务。

## 角色定位
- 你是用户在车内的出行伙伴，专注于「从出发到到达」的全链路旅程服务
- 你可以规划路线、推荐餐厅/景点、查询航班、播报天气、智能提醒
- 你的目标是让每一次出行都省心、高效、有温度

## 说话风格
- 简洁专业：用词精炼，不啰嗦，驾驶场景下避免长篇大论
- 有温度：称呼用户为「您好」或自然的中文对话，不用机器人口吻
- 主动提供选项：当有多种方案时，用清晰的结构呈现（如「有两个建议…」）
- 适合语音播报：句式简短，避免长难句和标点密集的内容
- 中文回复为主

## 能力边界
- 你可以调用工具来获取实时信息（路线、POI、天气等）
- 不确定的信息不要编造，主动说明「需要查询一下」
- 涉及安全驾驶的建议要谨慎，始终把安全放在第一位

## 两类出行需求
1. **目标驱动型（Goal-driven）**：用户有明确的目的地，如「去虹桥机场」「回家」
   → 重点是路线规划、到达时间、停车安排
2. **意图驱动型（Intent-driven）**：用户只有需求意图，如「找个地方吃饭」「想喝咖啡」
   → 重点是 POI 推荐、多方案对比、满足用户偏好
{gcp_section}
{skill_section}
"""


# 航班相关关键词和模式
_FLIGHT_KEYWORDS = [
    "航班", "接机", "送机", "飞机", "机场", "接人", "飞", "登机",
    "延误", "航站楼", "登机口", "托运", "行李", "值机", "起飞", "到达",
    "接朋友", "接客户", "接家人",
]
_FLIGHT_NO_PATTERN = __import__('re').compile(r'^[A-Z]{2}\d+$')


def _is_flight_related(query: Optional[str]) -> bool:
    """判断用户 query 是否与航班相关。"""
    if not query:
        return False
    q = query.strip()
    if not q:
        return False
    # 关键词匹配
    if any(kw in q for kw in _FLIGHT_KEYWORDS):
        return True
    # 航班号模式匹配（如 MU5301、CA1234）
    import re
    if re.search(r'[A-Z]{2}\d{3,6}', q):
        return True
    return False


def _build_gcp_context(gcp_snapshot: Dict[str, Any], user_query: Optional[str] = None) -> str:
    """从 GCP 快照构建简洁的上下文字符串。

    只提取出行相关的关键字段，避免 token 浪费。
    航班信息仅在用户 query 与航班相关时注入。
    """
    lines = []

    # 车辆位置
    vehicle = gcp_snapshot.get("vehicle", {})
    pos = vehicle.get("position", {})
    if pos.get("address"):
        lines.append(f"- 当前位置：{pos['address']}（{pos.get('lat'):.4f}, {pos.get('lon'):.4f}）")
    elif pos.get("lat"):
        lines.append(f"- 当前位置：({pos['lat']:.4f}, {pos['lon']:.4f})")

    # 用户画像
    profile = gcp_snapshot.get("user_profile", {})
    if profile.get("name"):
        lines.append(f"- 用户：{profile.get('name')}")
        prefs = profile.get("travel_preferences", {})
        if prefs.get("route_preference"):
            rp_map = {
                "time_first": "时间优先",
                "no_toll": "不走收费路",
                "shortest": "距离最短",
                "balance": "均衡/躲避拥堵",
            }
            lines.append(f"- 路线偏好：{rp_map.get(prefs['route_preference'], prefs['route_preference'])}")

    # 天气
    weather = gcp_snapshot.get("weather", {})
    live = weather.get("live", {})
    if live.get("weather"):
        lines.append(f"- 当前天气：{live.get('weather')}，{live.get('temperature')}℃")

    # 时间
    time_ctx = gcp_snapshot.get("time", {})
    if time_ctx.get("datetime_iso"):
        lines.append(f"- 当前时间：{time_ctx.get('datetime_iso')}")

    # 航班信息：仅当 query 与航班相关时注入
    transit = gcp_snapshot.get("transit", {})
    if transit.get("flight_no") and _is_flight_related(user_query):
        status = transit.get("status", "")
        delay = transit.get("delay_min", 0)
        delay_str = f"（延误{delay}分钟）" if status == "delayed" and delay > 0 else ""
        lines.append(f"- 关联航班：{transit.get('flight_no')} {status}{delay_str}")

    if not lines:
        return ""

    return f"\n## 当前上下文\n{chr(10).join(lines)}\n"


# ==================== 意图识别 Prompt ====================

INTENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent_type": {
            "type": "string",
            "enum": ["goal_driven", "intent_driven", "unknown"],
            "description": "出行需求类型：目标驱动型 / 意图驱动型 / 无法判断",
        },
        "user_intent": {
            "type": "string",
            "description": "具体意图，如 airport_pickup, dining, drinks, coffee, shopping, commute, leisure",
        },
        "confidence": {
            "type": "number",
            "description": "置信度 0~1",
        },
        "is_modification": {
            "type": "boolean",
            "description": "是否为对已有推荐的修改请求（如「太贵了换便宜的」「换个菜系」）",
        },
        "modification_type": {
            "type": "string",
            "enum": ["budget_change", "cuisine_change", "distance_change", "other_preference", "new_request", ""],
            "description": "修改类型：预算修改/菜系修改/距离修改/其他偏好修改/全新请求/非修改",
        },
        "modification_params": {
            "type": "object",
            "description": "修改的具体参数（如 max_price_per_person, cuisine_type, radius_km 等）",
        },
        "destination_name": {
            "type": "string",
            "description": "目的地名称（如果有）",
        },
        "extracted_slots": {
            "type": "object",
            "description": "抽取到的槽位（合并了修改参数后的值）",
            "properties": {
                "departure_time": {"type": "string", "description": "出发时间"},
                "arrival_time": {"type": "string", "description": "希望到达时间"},
                "flight_no": {"type": "string", "description": "航班号"},
                "passenger_count": {"type": "number", "description": "出行人数"},
                "passenger_name": {"type": "string", "description": "乘客/接人姓名"},
                "purpose": {"type": "string", "description": "出行目的"},
                "cuisine_type": {"type": "string", "description": "菜系偏好（用餐场景）"},
                "budget_level": {"type": "string", "description": "预算等级：budget/mid/premium"},
                "max_price_per_person": {"type": "number", "description": "人均最高价格（元）"},
                "companion_type": {"type": "string", "description": "同行人类别：alone/couple/friends/family_kids/family_elders/business"},
                "occasion": {"type": "string", "description": "场景场合：casual/date/business/celebration/family"},
                "other": {"type": "object", "description": "其他槽位"},
            },
        },
        "missing_slots": {
            "type": "array",
            "items": {"type": "string"},
            "description": "缺失的关键槽位名称（只列对方案有实质影响的）",
        },
    },
    "required": ["intent_type", "user_intent", "confidence", "is_modification", "extracted_slots", "missing_slots"],
}


def build_intent_prompt(
    gcp_snapshot: Dict[str, Any],
    user_query: Optional[str] = None,
    chat_history: Optional[List[Dict[str, Any]]] = None,
    existing_slots: Optional[Dict[str, Any]] = None,
    existing_proposals_exist: bool = False,
) -> str:
    """意图识别 + 槽位抽取 prompt。

    要求 Claude 输出严格符合 JSON schema。
    支持多轮对话上下文：传入 chat_history 可以识别修改请求。
    """
    schema_str = json.dumps(INTENT_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)

    # 构建对话上下文部分
    context_section = ""
    if chat_history and len(chat_history) > 0:
        history_lines = []
        for msg in chat_history[-6:]:  # 只看最近6条消息
            # 兼容 dict 和 LangChain Message 对象
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
            else:
                try:
                    msg_type = getattr(msg, "type", "")
                    role_map = {"human": "user", "ai": "assistant", "system": "system"}
                    role = role_map.get(msg_type, msg_type or "")
                    content = getattr(msg, "content", "")
                except Exception:
                    continue
            if isinstance(content, str) and content.strip():
                role_label = "用户" if role == "user" else "助手"
                history_lines.append(f"- {role_label}：{content[:200]}")
        if history_lines:
            context_section = f"""
## 对话历史（最近消息）
{chr(10).join(history_lines)}
"""

    existing_slots_section = ""
    if existing_slots:
        existing_slots_section = f"""
## 当前已识别的信息
{json.dumps(existing_slots, ensure_ascii=False, indent=2)}
"""

    modification_guidance = ""
    if existing_proposals_exist:
        modification_guidance = """
## 多轮修改识别（重要）
系统已经为用户推荐过方案。现在用户又说了新的一句话，你需要判断：
1. **is_modification=true**：用户在已有推荐基础上提出修改，例如：
   - 「都太贵了，来个人均150以下的」→ budget_change, modification_params={{max_price_per_person: 150, budget_level: "budget"}}
   - 「换个日料吧」→ cuisine_change, modification_params={{cuisine_type: "日料"}}
   - 「近一点的」→ distance_change, modification_params={{}}
   - 「太吵了，找个安静点的」→ other_preference, modification_params={{occasion: "quiet"}}
   这种情况下，保持原有的 intent_type 和 user_intent 不变，将修改参数合并到 extracted_slots 中。
2. **modification_type="new_request"**：用户提出了全新的、与之前无关的需求，例如：
   - 之前推荐了餐厅，现在说「我想回家」→ 新请求，清空之前的意图重新判断
3. **is_modification=false**：用户不是在修改，可能是在选择方案或确认（由其他机制处理）
"""

    return f"""你是一位专业的出行意图理解助手。请分析用户的出行需求，识别意图类型并抽取关键槽位。

## 输出格式要求
请先在 <thinking> 标签中用结构化的方式展示你的思考过程，然后输出 JSON。
思考格式：
**思考过程：**
1. **用户意图分析：** [分析用户的出行意图类型，结合当前时间、驾驶场景说明判断依据]
2. **用户偏好映射：** [结合用户画像和偏好（如路线偏好、生活偏好），说明哪些偏好与本次出行相关]
3. **关键信息提取：** [提取到的关键槽位和依据]
4. **多轮修改判断（如适用）：** [如果是已有推荐后的新输入，说明是否为修改请求及类型]

每点1-2句简洁中文，不要太长。

例如：
<thinking>
**思考过程：**
1. **用户意图分析：** 用户说"晚上下班后想和闺蜜喝一杯"，明确提到了"喝一杯"，属于意图驱动型的drinks场景。当前时间是周三傍晚18点左右，正是下班时间，符合约饭小酌的典型场景。
2. **用户偏好映射：** 用户画像中生活偏好包含清吧和精酿啤酒，同行人是闺蜜，属于朋友聚会场景，风格偏好轻松小资。
3. **关键信息提取：** user_intent=drinks, companion_type=friends, 时间段为傍晚下班后，预算未提及用默认。
4. **多轮修改判断：** 首次交互，不涉及修改。
</thinking>
{{JSON结果}}

## 任务要求
1. 判断是「目标驱动型」还是「意图驱动型」出行
   - 目标驱动型：有明确目的地（地址、地名、场所名等），如「去虹桥机场」「回家」
   - 意图驱动型：只有需求或目的，没有明确目的地，如「找个地方吃饭」「想喝一杯」
2. 抽取用户已提供的信息，不要过度推断
3. **关键原则：对于意图驱动型场景，missing_slots 必须为空数组**
   - 用户说「喝一杯」「和闺蜜吃饭」已经足够明确，可以直接推荐
   - 人数（passenger_count）、时间、预算（budget_level）都不是必须的，不要列入 missing_slots
   - 菜系偏好（cuisine_type）也不是必须的，直接给多样选择即可
   - 只有目标驱动型且用户没有说去哪里时，才需要在 missing_slots 中列 "destination"
4. user_intent 取值：travel, dining, drinks, coffee, shopping, leisure, airport_pickup
   - drinks：喝酒、喝一杯、小酌、去酒吧、微醺 等场景
   - dining：吃饭、餐厅、用餐 等场景
   - coffee：喝咖啡、咖啡厅 等场景
   - shopping：逛街、购物 等场景
   - leisure：休闲娱乐、玩、景点 等场景
5. 输出严格的 JSON 格式，不要有任何额外文字
{modification_guidance}
## 槽位说明
- departure_time / arrival_time：时间信息（支持相对时间，如「8点」「半小时后」）
- flight_no：航班号（接机场景）
- passenger_count：人数（注意：仅抽取，不作为缺失信息追问）
- cuisine_type：菜系/品类（仅抽取，不作为缺失信息追问）
- budget_level：预算等级（仅抽取，不追问）：budget（经济型/便宜）/ mid（中等）/ premium（高档）
- max_price_per_person：人均最高价格（元），从用户表达中推断（如"人均150以下"→150）
- companion_type：同行人类别（仅抽取，不追问）：
  - alone: 一个人；couple: 两人约会；friends: 朋友/闺蜜聚会
  - family_kids: 带小孩；family_elders: 带老人；business: 商务应酬
  - 例如「和闺蜜聚」→ friends，「带爸妈」→ family_elders，「约会」→ couple
- occasion：场景场合（仅抽取，不追问）：casual（随意）/ date（约会）/ business（商务）/ celebration（庆祝）/ family（家庭）

## 判断示例
- 「今晚想和闺蜜喝一杯」→ intent_driven, user_intent=drinks, is_modification=false, missing_slots=[]
- 「我想去吃饭」→ intent_driven, user_intent=dining, is_modification=false, missing_slots=[]
- 「接人」→ goal_driven, user_intent=airport_pickup, is_modification=false, missing_slots=["destination"]
- 「去虹桥机场」→ goal_driven, user_intent=travel, is_modification=false, destination_name="虹桥机场", missing_slots=[]
- （已有餐厅推荐后）「都太贵了，人均150以下的」→ intent_driven, user_intent=dining, is_modification=true, modification_type=budget_change, missing_slots=[]
- （已有餐厅推荐后）「换个日料」→ intent_driven, user_intent=dining, is_modification=true, modification_type=cuisine_change, extracted_slots={{cuisine_type: "日料"}}

## 输出格式
请直接输出 JSON 对象，不要有任何额外文字或 markdown 标记：

{schema_str}
{context_section}{existing_slots_section}
## 当前上下文（作为参考）
{_build_gcp_context(gcp_snapshot, user_query)}

现在，请分析用户的最新输入。"""


# ==================== 澄清追问 Prompt ====================

def build_clarify_prompt(
    missing_slots: List[str],
    current_understanding: Dict[str, Any],
    gcp_snapshot: Dict[str, Any],
    user_query: Optional[str] = None,
) -> str:
    """生成澄清追问 prompt。

    原则：
    - 一次只问一个最关键的问题
    - 问题简洁，适合语音播报和驾驶场景
    - 如果有多个缺失，选影响最大的那个先问
    - 用自然的中文对话方式，不要机器人口吻
    """
    slots_desc = "、".join(missing_slots)
    intent = current_understanding.get("user_intent", "出行")
    dest = current_understanding.get("destination_name", "")

    dest_part = f"目的地是「{dest}」，" if dest else ""

    return f"""用户想安排一次{intent}出行，{dest_part}但缺少一些关键信息。

缺失的信息有：{slots_desc}

## 任务
请生成一个追问问题，向用户确认最关键的那项信息。

## 要求
- 一次只问一个问题，不要一次问多个
- 问题要简洁、口语化，适合语音播报（车载场景）
- 用自然的对话口吻，不要「请提供…」这种生硬表达
- 不要问人数、预算、出发时间这些非必要信息
- 如果缺失的是 destination（目的地），问「您想去哪里呢？」即可

举个好例子：
- 「您想去哪里呢？」
- 「有什么菜系偏好吗？」
- 「是想找清吧还是热闹点的地方？」

请直接输出问题文本，不要有其他内容。

当前上下文：
{_build_gcp_context(gcp_snapshot, user_query)}"""


# ==================== 方案推荐 Prompt ====================

PROPOSAL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "maxItems": 3,
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "方案唯一标识，如 plan_1"},
                    "title": {"type": "string", "description": "方案标题，简洁有力"},
                    "summary": {"type": "string", "description": "一句话概要"},
                    "eta_min": {"type": "number", "description": "预计用时（分钟）"},
                    "distance_km": {"type": "number", "description": "距离（公里）"},
                    "strategy": {"type": "string", "description": "路线策略：time_first/no_toll/shortest/balance"},
                    "parking_hint": {"type": "string", "description": "停车提示"},
                    "pros": {"type": "array", "items": {"type": "string"}, "description": "优点列表（2-3条）"},
                    "cons": {"type": "array", "items": {"type": "string"}, "description": "缺点/注意事项（1-2条）"},
                    "reason": {"type": "string", "description": "推荐理由，结合用户画像和当前场景"},
                    "source": {"type": "string", "description": "数据来源，如 amap_route_v5"},
                },
                "required": ["id", "title", "summary", "eta_min", "distance_km", "pros", "cons", "reason"],
            },
        },
        "recommended_index": {
            "type": "number",
            "description": "推荐方案的索引（0-based）",
        },
    },
    "required": ["proposals", "recommended_index"],
}


def build_recommend_prompt(
    intent_type: str,
    destination: Dict[str, Any],
    skill_results: Dict[str, Any],
    gcp_snapshot: Dict[str, Any],
    user_query: Optional[str] = None,
) -> str:
    """方案推荐 prompt。

    将 Skill 返回的原始数据整合成 3 套差异化方案。
    """
    schema_str = json.dumps(PROPOSAL_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    results_str = json.dumps(skill_results, ensure_ascii=False, indent=2)
    dest_name = destination.get("name", destination.get("address", "目的地"))

    intent_desc = "目标驱动型（路线规划）" if intent_type == "goal_driven" else "意图驱动型（POI推荐）"

    return f"""你是一位专业的出行方案顾问。请根据以下信息，为用户生成 3 套差异化的出行方案。

## 输出格式要求
请先在 <thinking> 标签中用结构化方式展示你的推荐思路（2-3点，简洁说明），然后输出 JSON。
思考格式：
**推荐思考：**
1. **方案策略：** [说明整体推荐策略和筛选逻辑]
2. **差异化对比：** [说明3套方案各自的特点和适用场景]
3. **偏好匹配：** [哪些方案更匹配用户画像偏好]

每点1-2句简洁中文。
例如：
<thinking>
**推荐思考：**
1. **方案策略：** 基于用户想和朋友聚餐的需求，从搜索结果中筛选评分4.5以上、人均80-150元的餐厅。
2. **差异化对比：** 推荐3家风格不同的——一家创意菜（环境好适合拍照）、一家家常菜（性价比高分量足）、一家日料（氛围安静适合聊天）。
3. **偏好匹配：** 创意菜最符合用户"喜欢拍照打卡"的生活偏好。
</thinking>
{{JSON结果}}

## 场景类型
{intent_desc}
目的地：{dest_name}

## 原始数据
以下是从各个工具获取的原始结果：

{results_str}

## 任务要求
1. 生成 3 套有明显差异的方案（路线策略不同 / POI 风格不同）
2. 每套方案要有明确的优缺点和推荐理由
3. 推荐理由要结合用户画像和当前场景（天气、时间、路况等）
4. 数据要准确，基于原始结果，不要编造
5. 选择一个最推荐的方案（recommended_index）

## 输出格式
请直接输出 JSON，不要有额外文字：

{schema_str}

## 当前上下文
{_build_gcp_context(gcp_snapshot, user_query)}"""


# ==================== 任务拆解 Prompt ====================

TASK_PLAN_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务唯一ID，如 t1"},
                    "skill": {"type": "string", "description": "Skill 名称：route_master/dynamic_eta/smart_remind/local_poi/parking_find"},
                    "action": {"type": "string", "description": "Skill 中的 action/remind_type"},
                    "params": {"type": "object", "description": "调用参数"},
                    "deps": {"type": "array", "items": {"type": "string"}, "description": "依赖的前置 task_id 列表"},
                    "parallel_group": {"type": "string", "description": "并行组标识，同组任务并行执行"},
                },
                "required": ["task_id", "skill", "action", "params", "deps", "parallel_group"],
            },
        },
    },
    "required": ["tasks"],
}


def build_plan_decompose_prompt(
    selected_plan: Dict[str, Any],
    available_skills: List[Dict[str, Any]],
    gcp_snapshot: Dict[str, Any],
    user_query: Optional[str] = None,
) -> str:
    """任务拆解 prompt。

    将选定方案拆解为 Skill 执行任务，标注串并行关系。
    """
    schema_str = json.dumps(TASK_PLAN_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    plan_str = json.dumps(selected_plan, ensure_ascii=False, indent=2)
    skills_str = "\n".join(
        f"- {s['name']}：{s['description']}" for s in available_skills
    )

    return f"""你是一位任务编排专家。请根据选定的出行方案，将其拆解为具体的 Skill 执行任务。

## 选定方案
{plan_str}

## 可用 Skills
{skills_str}

## 任务要求
1. 把方案需要的所有 Skill 调用拆解为任务列表
2. 每个任务指定 skill 名称、action、参数
3. 标注任务间的依赖关系（deps）
4. 可以并行的任务放在同一个 parallel_group
5. 常见并行组合：
   - route + parking 可以并行
   - eta + remind 可以并行（如果 route 已完成）
6. 出发前提醒必须等路线和ETA确定后才能生成

## 输出格式
请直接输出 JSON，不要有额外文字：

{schema_str}

## 当前上下文
{_build_gcp_context(gcp_snapshot, user_query)}"""


# ==================== 重规划触发判断 Prompt ====================

REPLAN_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "need_replan": {"type": "boolean", "description": "是否需要重规划"},
        "reason": {"type": "string", "description": "重规划原因（如果需要）"},
        "severity": {"type": "string", "enum": ["low", "medium", "high"], "description": "影响程度"},
        "affected_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "受影响需要重新执行的 Skill 列表",
        },
        "user_notification": {"type": "string", "description": "需要通知用户的文案（如果需要）"},
    },
    "required": ["need_replan", "reason", "severity", "affected_skills"],
}


def build_replan_detection_prompt(
    gcp_changes: Dict[str, Any],
    current_route: Dict[str, Any],
    current_eta: Dict[str, Any],
) -> str:
    """判断 GCP 变化是否需要重规划。"""
    schema_str = json.dumps(REPLAN_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    changes_str = json.dumps(gcp_changes, ensure_ascii=False, indent=2)
    route_str = json.dumps(current_route, ensure_ascii=False, indent=2)
    eta_str = json.dumps(current_eta, ensure_ascii=False, indent=2)

    return f"""你是一位行程监控助手。请判断 GCP 上下文的变化是否需要触发行程重规划。

## GCP 变化
{changes_str}

## 当前路线
{route_str}

## 当前ETA
{eta_str}

## 判断标准
- ETA 增加超过 10 分钟 → 需要重规划（high）
- 航班延误超过 15 分钟 → 需要重规划（medium）
- 出现严重天气（暴雨/暴雪/台风等）→ 需要重规划（high）
- 发生交通事故/道路封闭 → 需要重规划（high）
- 小幅度变化（<5分钟，<2公里）→ 不需要重规划
- 仅信息更新但不影响行程 → 不需要重规划

## 输出格式
请直接输出 JSON：

{schema_str}"""
