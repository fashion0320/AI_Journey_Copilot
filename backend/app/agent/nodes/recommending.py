"""recommending 节点：生成 3 套差异化方案。

- goal_driven：并行调 route_master（3 种策略）+ parking_find，Claude 整合成 3 套路线方案
- intent_driven：调 local_poi.poi_recommend 得到 3 个 POI + 各自路线
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langgraph.types import RunnableConfig

from ..prompts import build_recommend_prompt, build_system_prompt
from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def recommending_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """推荐节点：生成 3 套差异化出行方案。"""
    configurable = config.get("configurable", {}) if config else {}
    claude = configurable.get("claude")
    skill_executor = configurable.get("skill_executor")
    stream_queue = configurable.get("stream_queue")
    gcp_store = configurable.get("gcp_store")

    intent_type = state.get("intent_type", "unknown")
    gcp_snapshot = state.get("gcp_snapshot") or {}
    destination = state.get("destination") or {}
    dest_name = state.get("destination_name", "")
    user_query = state.get("user_query", "")
    is_modification = state.get("is_modification", False)

    # 阶段思考提示
    if stream_queue:
        try:
            if is_modification:
                stream_queue.put_nowait(("thinking_stream", {"text": "正在根据您的新要求重新搜索..."}))
            elif intent_type == "goal_driven":
                stream_queue.put_nowait(("thinking_stream", {"text": "正在查询路线和停车信息..."}))
            else:
                stream_queue.put_nowait(("thinking_stream", {"text": "正在搜索附近的好地方..."}))
        except Exception:
            pass

    # 取车辆位置作为起点
    origin = _get_origin(gcp_store, gcp_snapshot)

    skill_results: Dict[str, Any] = {}

    if intent_type == "goal_driven":
        skill_results = await _recommend_goal_driven(
            skill_executor, origin, destination, gcp_snapshot, dest_name,
        )
    elif intent_type == "intent_driven":
        skill_results = await _recommend_intent_driven(
            skill_executor, origin, state, gcp_snapshot, dest_name,
        )
    else:
        # unknown，返回空方案
        return {
            "proposals": [],
            "journey_status": "recommending",
            "error": "unknown intent type",
        }

    # 用 Claude 整合为 3 套方案
    proposals = []
    if claude and skill_results:
        proposals = await _synthesize_proposals(
            claude, intent_type, destination, skill_results,
            gcp_snapshot, stream_queue, user_query,
        )

    # 如果 Claude 失败或不可用，用 Skill 原始数据构造简单方案
    if not proposals and skill_results:
        proposals = _build_simple_proposals(intent_type, skill_results, dest_name)

    # 推送卡片
    if stream_queue and proposals:
        try:
            stream_queue.put_nowait(("card_update", {
                "type": "proposals",
                "data": proposals,
            }))
        except Exception:
            pass

    # 存储原始 skill 结果供后续节点使用
    route_data = _extract_route_data(skill_results)
    parking_data = _extract_parking_data(skill_results)
    poi_data = _extract_poi_data(skill_results)

    return {
        "proposals": proposals,
        "route": route_data,
        "parking": parking_data,
        "poi_results": poi_data,
        "skill_results": {"recommend_phase": skill_results},
        "journey_status": "recommending",
    }


# ---- goal-driven 方案生成 ----

async def _recommend_goal_driven(
    skill_executor: Any,
    origin: Dict[str, float],
    destination: Dict[str, Any],
    gcp_snapshot: Dict[str, Any],
    dest_name: str,
) -> Dict[str, Any]:
    """目标驱动型：并行调 3 种路线策略 + 停车搜索。"""
    if not skill_executor or not origin or not destination:
        return {}

    dest_pos = {
        "lat": destination.get("lat", 0),
        "lon": destination.get("lon", 0),
    }

    # 用户偏好
    user_pref = "time_first"
    try:
        prefs = (gcp_snapshot.get("user_profile") or {}).get("travel_preferences") or {}
        user_pref = prefs.get("route_preference", "time_first")
    except Exception:
        pass

    # 并行调用 3 种策略 + 停车
    strategies = ["time_first", "no_toll", "shortest"]
    tasks = []
    for i, strategy in enumerate(strategies):
        tasks.append({
            "task_id": f"route_{strategy}",
            "skill": "route_master",
            "action": "route_single",
            "params": {
                "origin": origin,
                "destination": dest_pos,
                "strategy": strategy,
            },
            "parallel_group": "routes",
        })

    tasks.append({
        "task_id": "parking",
        "skill": "parking_find",
        "action": "parking_search",
        "params": {
            "destination_position": dest_pos,
            "radius_m": 500,
            "limit": 3,
        },
        "parallel_group": "routes",
    })

    results = await skill_executor.execute_plan(tasks)
    return results


# ---- intent-driven 方案生成 ----

async def _recommend_intent_driven(
    skill_executor: Any,
    origin: Dict[str, float],
    state: AgentState,
    gcp_snapshot: Dict[str, Any],
    keyword: str,
) -> Dict[str, Any]:
    """意图驱动型：调 poi_recommend 得到 3 个 POI。"""
    if not skill_executor:
        return {}

    # 推断 intent（映射到 local_poi 支持的意图类型）
    user_intent = state.get("user_intent", "dining")
    intent_map = {
        "dining": "dining",
        "drinks": "drinks",   # 喝酒/酒吧/小酌 场景
        "coffee": "coffee",
        "shopping": "shopping",
        "leisure": "leisure",
    }
    poi_intent = intent_map.get(user_intent, "dining")

    # 从槽位中取参数
    slots = state.get("extracted_slots") or {}
    cuisine = slots.get("cuisine_type", "")
    budget = slots.get("budget_level", "")
    companion_type = slots.get("companion_type", "")
    occasion = slots.get("occasion", "")

    # 获取时间上下文
    gcp_time = (gcp_snapshot.get("time") or {})
    time_bucket = gcp_time.get("time_bucket", "") if isinstance(gcp_time, dict) else ""

    result = await skill_executor.execute_skill(
        "local_poi", "poi_recommend",
        {
            "intent": poi_intent,
            "keyword": keyword or cuisine or "",
            "center_position": origin,
            "radius_km": 5,
            "cuisine_type": cuisine,
            "budget_level": budget,
            "companion_type": companion_type,
            "occasion": occasion,
            "time_bucket": time_bucket,
        },
        task_id="poi_recommend",
    )
    return {"poi_recommend": result}


# ---- Claude 整合方案 ----

async def _synthesize_proposals(
    claude: Any,
    intent_type: str,
    destination: Dict[str, Any],
    skill_results: Dict[str, Any],
    gcp_snapshot: Dict[str, Any],
    stream_queue: Any,
    user_query: str = "",
) -> List[Dict[str, Any]]:
    """调用 Claude 将 Skill 结果整合成 3 套差异化方案。"""
    sys_prompt = build_system_prompt(gcp_snapshot, user_query=user_query)
    rec_prompt = build_recommend_prompt(
        intent_type, destination, skill_results, gcp_snapshot,
        user_query=user_query,
    )
    messages = [{"role": "user", "content": rec_prompt}]

    full_text_parts: list[str] = []
    try:
        # 推送思考提示
        if stream_queue:
            try:
                stream_queue.put_nowait(("thinking_stream", {"text": "正在为您筛选和整合推荐方案..."}))
            except Exception:
                pass
        # 不推送 token_stream：此节点输出 JSON（方案列表），避免原始 JSON 泄露到聊天
        async for evt in claude.chat_stream(messages, system=sys_prompt):
            evt_type = evt.type.value if hasattr(evt.type, "value") else evt.type
            if evt_type == "text_delta":
                full_text_parts.append(evt.text)

        full_text = "".join(full_text_parts).strip()

        # 提取 thinking 内容
        thinking_text, json_text = _extract_rec_thinking(full_text)
        if thinking_text and stream_queue:
            try:
                # 推送结构化思考过程卡片（持久化展示）
                stream_queue.put_nowait(("card_update", {
                    "type": "thought_process",
                    "data": {"content": thinking_text},
                }))
                # 推送思考结束（关闭"思考中..."动画气泡）
                stream_queue.put_nowait(("thinking_end", {}))
            except Exception:
                pass

        result = _parse_proposal_json(json_text)
        if result is None:
            result = _parse_proposal_json(full_text)
        if result:
            return result.get("proposals", [])
    except Exception as e:
        logger.error("synthesize proposals error: %s", e)

    return []


def _extract_rec_thinking(text: str) -> tuple[str, str]:
    """从推荐结果中提取 <thinking> 标签内容。"""
    THINKING_OPEN = "<thinking>"
    THINKING_CLOSE = "</thinking>"
    open_idx = text.find(THINKING_OPEN)
    close_idx = text.find(THINKING_CLOSE)
    if open_idx >= 0 and close_idx > open_idx:
        thinking = text[open_idx + len(THINKING_OPEN):close_idx].strip()
        remaining = (text[:open_idx] + text[close_idx + len(THINKING_CLOSE):]).strip()
        return thinking, remaining
    return "", text


def _parse_proposal_json(text: str) -> Dict[str, Any] | None:
    """解析方案推荐 JSON。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.find("```", start)
        if end > start:
            try:
                return json.loads(text[start:end].strip())
            except json.JSONDecodeError:
                pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


# ---- 简单方案构造（降级） ----

def _build_simple_proposals(
    intent_type: str, skill_results: Dict[str, Any], dest_name: str,
) -> List[Dict[str, Any]]:
    """无 Claude 时的降级方案构造。"""
    proposals = []

    if intent_type == "goal_driven":
        strategies = ["time_first", "no_toll", "shortest"]
        labels = ["最快路线", "不走高速", "最短路线"]
        for i, (strategy, label) in enumerate(zip(strategies, labels)):
            key = f"route_{strategy}"
            r = skill_results.get(key) or {}
            data = (r.get("result") if isinstance(r, dict) and "result" in r else r) or {}
            data = data.get("data", data) if isinstance(data, dict) else {}
            proposals.append({
                "id": f"plan_{i+1}",
                "title": label,
                "summary": f"前往{dest_name}的{label}",
                "eta_min": data.get("duration_min", 0),
                "distance_km": data.get("distance_km", 0),
                "strategy": strategy,
                "parking_hint": "",
                "pros": [label],
                "cons": [],
                "reason": f"推荐{label}，适合您的出行需求",
                "source": "amap",
            })

    elif intent_type == "intent_driven":
        r = skill_results.get("poi_recommend") or {}
        data = (r.get("result") if isinstance(r, dict) and "result" in r else r) or {}
        data = data.get("data", data) if isinstance(data, dict) else {}
        cands = data.get("candidates") or data.get("recommended") or []
        for i, c in enumerate(cands[:3]):
            # 用距离估算 ETA（假设平均车速约 30km/h，市区道路）
            dist_km = c.get("distance_km", 0) or 0
            if isinstance(dist_km, (int, float)) and dist_km > 0:
                eta_min = max(5, round(dist_km / 30 * 60))
            else:
                eta_min = 20  # 兜底默认值
            proposals.append({
                "id": f"plan_{i+1}",
                "title": c.get("name", f"方案{i+1}"),
                "summary": c.get("address", ""),
                "eta_min": eta_min,
                "distance_km": dist_km,
                "strategy": "",
                "parking_hint": "",
                "pros": [c.get("category", "")],
                "cons": [],
                "reason": c.get("reason", ""),
                "source": c.get("source", "amap"),
            })

    return proposals


# ---- 辅助函数 ----

def _get_origin(gcp_store: Any, gcp_snapshot: Dict[str, Any]) -> Dict[str, float]:
    """获取起点位置（车辆当前位置）。"""
    if gcp_store:
        try:
            pos = gcp_store.get("vehicle.position") or {}
            if pos and pos.get("lat"):
                return {"lat": float(pos["lat"]), "lon": float(pos["lon"])}
        except Exception:
            pass
    if gcp_snapshot:
        pos = (gcp_snapshot.get("vehicle") or {}).get("position") or {}
        if pos.get("lat"):
            return {"lat": float(pos["lat"]), "lon": float(pos["lon"])}
    return {"lat": 31.2359, "lon": 121.4996}  # 默认陆家嘴


def _extract_route_data(skill_results: Dict[str, Any]) -> Dict[str, Any]:
    """从 Skill 结果中提取第一条路线数据。"""
    # 优先取 time_first 的结果
    for key in ["route_time_first", "route_balance", "route_shortest", "route_no_toll"]:
        r = skill_results.get(key)
        if r and isinstance(r, dict):
            result = r.get("result", r) if "result" in r else r
            if isinstance(result, dict) and result.get("data"):
                return result["data"]
    return {}


def _extract_parking_data(skill_results: Dict[str, Any]) -> Dict[str, Any]:
    r = skill_results.get("parking")
    if r and isinstance(r, dict):
        result = r.get("result", r) if "result" in r else r
        if isinstance(result, dict):
            return result.get("data", result)
    return {}


def _extract_poi_data(skill_results: Dict[str, Any]) -> Dict[str, Any]:
    r = skill_results.get("poi_recommend")
    if r and isinstance(r, dict):
        result = r.get("result", r) if "result" in r else r
        if isinstance(result, dict):
            return result.get("data", result)
    return {}
