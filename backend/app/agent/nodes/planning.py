"""planning 节点：任务拆解 + Skill 编排执行。

1. 基于选定方案，调用 Claude 做任务拆解
2. 生成 DAG 任务计划（task_id, skill, action, params, deps, parallel_group）
3. 调用 SkillExecutor.execute_plan 执行
4. 汇总结果到 state
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langgraph.types import RunnableConfig

from ..prompts import build_plan_decompose_prompt, build_system_prompt
from ..state import AgentState
from ...skills import get_all_skills
from ...core.logging import get_logger

logger = get_logger(__name__)


async def planning_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """规划节点：拆解任务并执行。"""
    configurable = config.get("configurable", {}) if config else {}
    claude = configurable.get("claude")
    skill_executor = configurable.get("skill_executor")
    stream_queue = configurable.get("stream_queue")
    gcp_snapshot = state.get("gcp_snapshot") or {}

    # 获取选定方案
    proposals = state.get("proposals") or []
    selected_id = state.get("selected_plan_id") or (proposals[0]["id"] if proposals else "")
    selected_plan = next(
        (p for p in proposals if p.get("id") == selected_id),
        proposals[0] if proposals else {},
    )

    # 目标驱动型：没有 proposals（从 parking_confirm 直接过来），
    # 构造一个默认方案（最快路线）
    intent_type = state.get("intent_type", "goal_driven")
    if not selected_plan and intent_type == "goal_driven":
        dest_name = state.get("destination_name", "目的地")
        selected_plan = {
            "id": "plan_default",
            "title": f"前往{dest_name}",
            "strategy": "time_first",
            "eta_min": 0,
            "distance_km": 0,
        }

    # 构造 Skill 描述列表
    skill_descriptions = []
    try:
        skills = get_all_skills()
        for name, skill in skills.items():
            skill_descriptions.append({
                "name": name,
                "description": skill.description,
            })
    except Exception:
        skill_descriptions = [
            {"name": "route_master", "description": "路线规划，支持 route_single/route_multi/route_reroute"},
            {"name": "dynamic_eta", "description": "动态 ETA 计算，支持 eta_query/eta_delta_check"},
            {"name": "smart_remind", "description": "智能提醒，支持 pre_departure/weather/in_journey/pre_arrival"},
            {"name": "local_poi", "description": "POI 推荐与搜索，支持 poi_recommend/poi_resolve"},
            {"name": "parking_find", "description": "停车场搜索，支持 parking_search/parking_transit_hub"},
        ]

    # 任务拆解
    # MVP 阶段优先使用内置规则计划（更可靠），避免 Claude 生成的 action 名称不匹配
    task_plan = _build_default_plan(selected_plan, state)

    # 可选：用 Claude 优化计划（仅在有 Claude 且内置计划有效时补充）
    # 暂时禁用：Claude 经常生成不存在的 skill/action 名称，导致全部失败
    # if claude and skill_descriptions:
    #     claude_plan = await _decompose_with_claude(...)
    #     if claude_plan:
    #         task_plan = claude_plan

    # 执行计划
    skill_results: Dict[str, Any] = {}
    if skill_executor and task_plan:
        try:
            skill_results = await skill_executor.execute_plan(
                task_plan, context={"selected_plan": selected_plan},
            )
        except Exception as e:
            logger.exception("execute_plan error: %s", e)
            skill_results = {}

    # 汇总结果
    route_data = _collect_route(skill_results)
    eta_data = _collect_eta(skill_results)
    # 如果 parking_confirm 已搜到停车场，保留已有数据；否则用 planning 阶段的搜索结果
    existing_parking = state.get("parking") or {}
    parking_data = existing_parking if existing_parking.get("parking_lots") else _collect_parking(skill_results)
    reminders = _collect_reminders(skill_results)
    poi_data = _collect_poi(skill_results)

    # 获取当前目的地（如果 state 中已有 lat/lon 则直接使用）
    dest = state.get("destination") or {}

    # 先尝试解析目的地坐标（fallback route 构造需要）
    if not dest.get("lat"):
        resolved_dest = _resolve_destination_from_proposals(selected_plan, state, skill_results)
        if resolved_dest:
            dest = {**dest, **resolved_dest}

    # 获取起点坐标（用于构造 fallback polyline）
    origin_pos = {"lat": 31.2359, "lon": 121.4996}  # 默认陆家嘴
    gcp = state.get("gcp_snapshot") or {}
    veh_pos = (gcp.get("vehicle") or {}).get("position") or {}
    if isinstance(veh_pos, dict) and veh_pos.get("lat"):
        origin_pos = {"lat": float(veh_pos["lat"]), "lon": float(veh_pos["lon"])}
    elif hasattr(veh_pos, "lat") and veh_pos.lat:
        origin_pos = {"lat": float(veh_pos.lat), "lon": float(veh_pos.lon)}

    # 如果没有 route 数据，用方案里的
    eta_min = selected_plan.get("eta_min", 0)
    if isinstance(eta_min, str):
        try:
            eta_min = int(eta_min)
        except (ValueError, TypeError):
            eta_min = 0
    dist_km = selected_plan.get("distance_km", 0)
    if isinstance(dist_km, str):
        try:
            dist_km = float(dist_km)
        except (ValueError, TypeError):
            dist_km = 0

    if not route_data and eta_min:
        # 构造包含 polyline 的 fallback route（起点→终点直线），
        # 确保 demo 模式下车辆可以沿路线推进
        fallback_polyline = ""
        if dest.get("lat") and dest.get("lon"):
            fallback_polyline = f"{origin_pos['lon']},{origin_pos['lat']};{dest['lon']},{dest['lat']}"
        route_data = {
            "duration_min": int(eta_min) if eta_min else 0,
            "distance_km": float(dist_km) if dist_km else 0,
            "strategy": selected_plan.get("strategy", ""),
            "polyline": fallback_polyline,
        }

    # 如果 route_data 有 distance 但缺少 polyline，补一个简单 polyline（demo 推进需要）
    if route_data and not route_data.get("polyline") and dest.get("lat") and dest.get("lon"):
        route_data["polyline"] = f"{origin_pos['lon']},{origin_pos['lat']};{dest['lon']},{dest['lat']}"
    if not eta_data and eta_min:
        eta_data = {
            "remaining_min": int(eta_min) if eta_min else 30,
            "eta_arrival_time": "",
            "confidence_band_min": 5,
            "traffic_level": "smooth",
        }
    # 兜底：如果完全没有 ETA 数据，设置一个合理默认值，防止误判为到达
    if not eta_data:
        eta_data = {
            "remaining_min": 30,
            "eta_arrival_time": "",
            "confidence_band_min": 5,
            "traffic_level": "unknown",
        }

    # 关键修复：intent_driven 场景下，从 POI 结果中解析出目的地坐标，写回 state.destination
    # 否则 in_progress 阶段拿不到 destination，ETA 无法计算和递减
    dest_updates: Dict[str, Any] = {}
    if not dest.get("lat"):
        resolved_dest = _resolve_destination_from_proposals(selected_plan, state, skill_results)
        if resolved_dest:
            dest_updates["destination"] = resolved_dest
            dest_updates["destination_name"] = resolved_dest.get("name", state.get("destination_name", ""))
            # 确保后续节点（in_progress/orchestrator sync_to_gcp）能拿到目的地
            logger.info(
                "planning: resolved destination for intent_driven: name=%s, lat=%s, lon=%s",
                resolved_dest.get("name"), resolved_dest.get("lat"), resolved_dest.get("lon"),
            )

    # 推送初始 ETA 和路线卡片，确保用户点击"确认出发"后立即看到反馈
    # （避免等待第一次 periodic_eta_check 才有 ETA 显示）
    if stream_queue:
        try:
            # 推送路线卡片
            if route_data and route_data.get("distance_km") is not None:
                stream_queue.put_nowait(("card_update", {
                    "type": "route",
                    "data": route_data,
                }))
            # 推送 ETA 卡片
            if eta_data and eta_data.get("remaining_min") is not None:
                stream_queue.put_nowait(("card_update", {
                    "type": "eta",
                    "data": eta_data,
                }))
        except Exception:
            pass

    return {
        "task_plan": task_plan,
        "skill_results": {"planning_phase": skill_results},
        "route": route_data,
        "eta": eta_data,
        "parking": parking_data,
        "reminders": reminders,
        "poi_results": poi_data,
        "journey_status": "planning",
        **dest_updates,
    }


async def _decompose_with_claude(
    claude: Any,
    selected_plan: Dict[str, Any],
    skill_descriptions: List[Dict[str, Any]],
    gcp_snapshot: Dict[str, Any],
    stream_queue: Any,
    user_query: str = "",
) -> List[Dict[str, Any]]:
    """用 Claude 做任务拆解。"""
    sys_prompt = build_system_prompt(gcp_snapshot, user_query=user_query)
    decompose_prompt = build_plan_decompose_prompt(
        selected_plan, skill_descriptions, gcp_snapshot, user_query=user_query,
    )
    messages = [{"role": "user", "content": decompose_prompt}]

    full_text_parts: list[str] = []
    try:
        # 不推送 token_stream：此节点输出 JSON（任务拆解计划），避免原始 JSON 泄露到聊天
        async for evt in claude.chat_stream(messages, system=sys_prompt):
            evt_type = evt.type.value if hasattr(evt.type, "value") else evt.type
            if evt_type == "text_delta":
                full_text_parts.append(evt.text)

        full_text = "".join(full_text_parts).strip()
        result = _parse_plan_json(full_text)
        if result:
            return result.get("tasks", [])
    except Exception as e:
        logger.error("plan decompose claude error: %s", e)

    return []


def _parse_plan_json(text: str) -> Dict[str, Any] | None:
    """解析任务计划 JSON。"""
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


def _build_default_plan(
    selected_plan: Dict[str, Any], state: AgentState,
) -> List[Dict[str, Any]]:
    """内置规则生成的默认任务计划。"""
    plan: List[Dict[str, Any]] = []
    dest = state.get("destination") or {}
    intent_type = state.get("intent_type", "goal_driven")
    strategy = selected_plan.get("strategy", "time_first")

    # 尝试从方案中获取目的地位置（intent_driven 场景）
    dest_pos = None
    if dest.get("lat"):
        dest_pos = {"lat": dest.get("lat", 0), "lon": dest.get("lon", 0)}
    else:
        # 从 POI 结果中查找选中方案对应的位置
        poi_data = state.get("poi_results") or {}
        recommend_results = (state.get("skill_results") or {}).get("recommend_phase") or {}
        poi_rec = recommend_results.get("poi_recommend") or {}
        poi_rec_data = (poi_rec.get("data") or {}) if isinstance(poi_rec, dict) else {}
        cands = poi_rec_data.get("candidates") or poi_rec_data.get("recommended") or []
        selected_title = selected_plan.get("title", "")
        for c in cands:
            if c.get("name") == selected_title or len(plan) == 0:
                loc = c.get("location") or c.get("position") or {}
                if isinstance(loc, dict) and loc.get("lat"):
                    dest_pos = {"lat": float(loc["lat"]), "lon": float(loc.get("lon", loc.get("lng", 0)))}
                    break
                if isinstance(loc, str) and "," in loc:
                    parts = loc.split(",")
                    try:
                        dest_pos = {"lat": float(parts[1]), "lon": float(parts[0])}
                    except (ValueError, IndexError):
                        pass
                    break

    # 获取起点（车辆位置）
    origin_pos = {"lat": 31.2359, "lon": 121.4996}  # 默认陆家嘴
    gcp = state.get("gcp_snapshot") or {}
    veh_pos = (gcp.get("vehicle") or {}).get("position") or {}
    if isinstance(veh_pos, dict) and veh_pos.get("lat"):
        origin_pos = {"lat": float(veh_pos["lat"]), "lon": float(veh_pos["lon"])}
    elif hasattr(veh_pos, "lat") and veh_pos.lat:
        origin_pos = {"lat": float(veh_pos.lat), "lon": float(veh_pos.lon)}

    # 检查 parking 数据是否已在 parking_confirm 阶段获取到
    existing_parking = state.get("parking") or {}
    has_parking = bool(existing_parking.get("parking_lots"))

    # 第一组并行：路线 + 停车（如果 parking_confirm 已搜过则跳过停车搜索）
    if dest_pos and dest_pos.get("lat"):
        plan.append({
            "task_id": "t_route",
            "skill": "route_master",
            "action": "route_single",
            "params": {
                "origin": origin_pos,
                "destination": dest_pos,
                "strategy": strategy,
            },
            "deps": [],
            "parallel_group": "group1",
        })
        if not has_parking:
            plan.append({
                "task_id": "t_parking",
                "skill": "parking_find",
                "action": "parking_search",
                "params": {
                    "destination_position": dest_pos,
                    "radius_m": 500,
                    "limit": 3,
                },
                "deps": [],
                "parallel_group": "group1",
            })

        # 第二组：ETA + 提醒（依赖路线）
        plan.append({
            "task_id": "t_eta",
            "skill": "dynamic_eta",
            "action": "eta_query",
            "params": {
                "current_position": origin_pos,
                "destination": dest_pos,
            },
            "deps": ["t_route"],
            "parallel_group": "group2",
        })
        plan.append({
            "task_id": "t_remind",
            "skill": "smart_remind",
            "action": "pre_departure",
            "params": {
                "destination": selected_plan.get("title", ""),
                "departure_time": "",
            },
            "deps": ["t_route"],
            "parallel_group": "group2",
        })
    else:
        # 无法获取精确目的地，仅生成出发前提醒
        plan.append({
            "task_id": "t_remind",
            "skill": "smart_remind",
            "action": "pre_departure",
            "params": {
                "destination": selected_plan.get("title", ""),
                "departure_time": "",
            },
            "deps": [],
            "parallel_group": "group1",
        })

    return plan


def _collect_route(results: Dict[str, Any]) -> Dict[str, Any]:
    """从结果中收集路线数据。"""
    # 优先匹配已知 key
    for key in ["t_route", "route_time_first", "route_balance",
                "route_shortest", "route_no_toll", "route_master"]:
        r = results.get(key)
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("distance_km") and data.get("polyline"):
                return data
    # 兜底：遍历所有结果找包含 polyline 的 route
    for key, r in results.items():
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("distance_km") and data.get("polyline"):
                return data
    # 再兜底：只有 distance_km 也返回
    for key, r in results.items():
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("distance_km"):
                return data
    return {}


def _collect_eta(results: Dict[str, Any]) -> Dict[str, Any]:
    for key in ["t_eta", "dynamic_eta"]:
        r = results.get(key)
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("remaining_min") is not None:
                return data
    # 兜底：遍历所有结果找 eta
    for r in results.values():
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("remaining_min") is not None:
                return data
    return {}


def _collect_parking(results: Dict[str, Any]) -> Dict[str, Any]:
    for key in ["t_parking", "parking", "parking_find"]:
        r = results.get(key)
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("parking_lots"):
                return data
    # 兜底
    for r in results.values():
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("parking_lots"):
                return data
    return {}


def _collect_reminders(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    reminders = []
    for key in ["t_remind", "smart_remind"]:
        r = results.get(key)
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("tts_text"):
                reminders.append(data)
    if not reminders:
        for r in results.values():
            if r and isinstance(r, dict):
                data = r.get("data") if "data" in r else r
                if isinstance(data, dict) and data.get("tts_text"):
                    reminders.append(data)
    return reminders


def _collect_poi(results: Dict[str, Any]) -> Dict[str, Any]:
    for key in ["t_poi", "local_poi", "poi_recommend"]:
        r = results.get(key)
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict) and data.get("candidates"):
                return data
    return {}


def _resolve_destination_from_proposals(
    selected_plan: Dict[str, Any],
    state: AgentState,
    skill_results: Dict[str, Any],
) -> Dict[str, Any]:
    """从方案/POI结果/路线结果中解析出目的地坐标（intent_driven 场景使用）。

    优先级：
    1. 路线结果（t_route）中的终点坐标
    2. POI 推荐结果中匹配选中方案名称的候选
    3. POI 推荐结果中的第一个有坐标的候选
    """
    selected_title = (selected_plan.get("title", "") or "").strip()

    # 优先从路线结果中取终点
    for key in ["t_route"]:
        r = skill_results.get(key)
        if r and isinstance(r, dict):
            data = r.get("data") if "data" in r else r
            if isinstance(data, dict):
                # 尝试取终点坐标（route_master 结果可能包含 destination 字段）
                dest_info = data.get("destination") or {}
                if isinstance(dest_info, dict) and dest_info.get("lat"):
                    return {
                        "name": selected_title or dest_info.get("name", "目的地"),
                        "lat": float(dest_info["lat"]),
                        "lon": float(dest_info.get("lon", dest_info.get("lng", 0))),
                        "address": dest_info.get("address", ""),
                        "poi_id": dest_info.get("poi_id", ""),
                    }

    # 从 POI 推荐结果中匹配
    # 先从 recommending 阶段的 skill_results 里找
    poi_data: Dict[str, Any] = {}
    recommend_results = (state.get("skill_results") or {}).get("recommend_phase") or {}
    poi_rec = recommend_results.get("poi_recommend") or {}
    if poi_rec:
        poi_rec_data = (poi_rec.get("data") or {}) if isinstance(poi_rec, dict) else {}
        cands = poi_rec_data.get("candidates") or poi_rec_data.get("recommended") or []
    else:
        # 从 planning 阶段自己的结果里找
        for key in ["poi_recommend", "t_poi", "local_poi"]:
            r = skill_results.get(key)
            if r and isinstance(r, dict):
                data = r.get("data") if "data" in r else r
                if isinstance(data, dict):
                    cands = data.get("candidates") or data.get("recommended") or data.get("results") or []
                    if cands:
                        break
        else:
            cands = []

    # 精确匹配名称
    for c in cands:
        if not isinstance(c, dict):
            continue
        if c.get("name", "").strip() == selected_title:
            loc = c.get("location") or c.get("position") or {}
            lat, lon = 0.0, 0.0
            if isinstance(loc, dict) and loc.get("lat"):
                lat = float(loc["lat"])
                lon = float(loc.get("lon", loc.get("lng", 0)))
            elif isinstance(loc, str) and "," in loc:
                parts = loc.split(",")
                try:
                    lon = float(parts[0])
                    lat = float(parts[1])
                except (ValueError, IndexError):
                    pass
            if lat and lon:
                return {
                    "name": c.get("name", selected_title),
                    "lat": lat,
                    "lon": lon,
                    "address": c.get("address", ""),
                    "poi_id": c.get("id", ""),
                }

    # 退而求其次：取第一个有坐标的候选
    for c in cands:
        if not isinstance(c, dict):
            continue
        loc = c.get("location") or c.get("position") or {}
        lat, lon = 0.0, 0.0
        if isinstance(loc, dict) and loc.get("lat"):
            lat = float(loc["lat"])
            lon = float(loc.get("lon", loc.get("lng", 0)))
        elif isinstance(loc, str) and "," in loc:
            parts = loc.split(",")
            try:
                lon = float(parts[0])
                lat = float(parts[1])
            except (ValueError, IndexError):
                pass
        if lat and lon:
            return {
                "name": c.get("name", selected_title or "目的地"),
                "lat": lat,
                "lon": lon,
                "address": c.get("address", ""),
                "poi_id": c.get("id", ""),
            }

    return {}
