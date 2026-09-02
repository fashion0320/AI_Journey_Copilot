"""parking_confirm 节点：搜索停车场 + 展示停车卡片（带确认出发按钮）。

- 目标驱动型：destination 已在 destination_confirm 阶段解析好
- 意图驱动型：需要从选中方案对应的 POI 结果中解析目的地坐标
- 并行执行：parking_find（搜索停车场）
- 推送 parking 卡片，graph 在此中断等待 confirm_departure
"""

from __future__ import annotations

from typing import Any, Dict

from langgraph.types import RunnableConfig

from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def parking_confirm_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """停车确认节点：搜索停车场并展示停车卡片，等待用户确认出发。"""
    configurable = config.get("configurable", {}) if config else {}
    skill_executor = configurable.get("skill_executor")
    stream_queue = configurable.get("stream_queue")
    gcp_store = configurable.get("gcp_store")

    # 1. 获取目的地坐标（优先用 state.destination 中已解析的坐标）
    destination = state.get("destination") or {}
    dest_lat = destination.get("lat", 0)
    dest_lon = destination.get("lon", 0)
    dest_name = state.get("destination_name", "目的地")

    # 2. 如果还没有精确坐标（intent_driven 场景，方案是 POI 名称），
    #    从选中方案对应的 POI 结果中解析
    if not dest_lat or not dest_lon:
        resolved = _resolve_destination_from_state(state)
        if resolved:
            destination = {**destination, **resolved}
            dest_lat = resolved.get("lat", 0)
            dest_lon = resolved.get("lon", 0)
            dest_name = resolved.get("name", dest_name)
            logger.info(
                "parking_confirm: resolved destination for intent_driven: name=%s",
                dest_name,
            )

    if not dest_lat or not dest_lon:
        logger.warning("parking_confirm: no destination coordinates, skipping parking search")
        return {
            "parking": {"parking_lots": []},
            "journey_status": "parking_confirm",
        }

    # 3. 获取起点（车辆当前位置）
    origin = _get_origin(gcp_store, state)

    # 4. 搜索停车场
    parking_data: Dict[str, Any] = {}
    if skill_executor:
        try:
            result = await skill_executor.execute_skill(
                "parking_find", "parking_search",
                {
                    "destination_position": {"lat": dest_lat, "lon": dest_lon},
                    "radius_m": 500,
                    "limit": 3,
                    "destination_name": dest_name,
                },
                task_id="parking_confirm_search",
            )
            if result.get("status") == "success":
                raw_data = result.get("data") or result.get("result") or {}
                if isinstance(raw_data, dict):
                    parking_data = raw_data
                else:
                    logger.warning("parking_confirm: unexpected result format: %s", type(raw_data))
        except Exception as e:
            logger.error("parking_confirm: parking search error: %s", e)

    # 5. 兜底：如果没搜到，给一个空列表
    if not parking_data.get("parking_lots"):
        parking_data["parking_lots"] = []
        parking_data["total_found"] = 0
        parking_data["destination_name"] = dest_name
        parking_data["recommended_index"] = 0

    # 6. 推送 parking 卡片（带确认出发按钮，由前端 ParkingCard 实现）
    if stream_queue and parking_data.get("parking_lots"):
        try:
            stream_queue.put_nowait(("card_update", {
                "type": "parking",
                "data": parking_data,
            }))
        except Exception:
            pass

    # 7. 返回结果
    return {
        "parking": parking_data,
        "destination": destination if destination else state.get("destination", {}),
        "destination_name": dest_name,
        "journey_status": "parking_confirm",
    }


def _resolve_destination_from_state(state: AgentState) -> Dict[str, Any]:
    """从 state 中解析目的地坐标（intent_driven 场景使用）。

    优先级：
    1. state.destination（已有坐标）
    2. 选中方案对应的 POI 推荐结果
    3. POI 结果中的第一个候选
    """
    selected_id = state.get("selected_plan_id")
    proposals = state.get("proposals") or []
    selected_plan = None
    if selected_id:
        selected_plan = next((p for p in proposals if p.get("id") == selected_id), None)
    if not selected_plan and proposals:
        selected_plan = proposals[0]

    if not selected_plan:
        return {}

    selected_title = (selected_plan.get("title", "") or "").strip()

    # 从 recommend_phase 的 skill_results 中找 POI 结果
    skill_results = state.get("skill_results") or {}
    rec_phase = skill_results.get("recommend_phase") or {}
    poi_rec = rec_phase.get("poi_recommend") or {}
    poi_data = {}
    if isinstance(poi_rec, dict):
        poi_data = poi_rec.get("data") or poi_rec.get("result") or {}
        if not isinstance(poi_data, dict):
            poi_data = {}
    cands = poi_data.get("candidates") or poi_data.get("recommended") or []

    # 精确匹配名称
    for c in cands:
        if not isinstance(c, dict):
            continue
        if c.get("name", "").strip() == selected_title:
            return _extract_candidate_coords(c, selected_title)

    # 取第一个有坐标的候选
    for c in cands:
        if not isinstance(c, dict):
            continue
        result = _extract_candidate_coords(c, selected_title)
        if result.get("lat") and result.get("lon"):
            return result

    return {}


def _extract_candidate_coords(cand: Dict[str, Any], fallback_name: str) -> Dict[str, Any]:
    """从 POI 候选字典中提取坐标信息。"""
    loc = cand.get("location") or cand.get("position") or {}
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

    if not lat or not lon:
        return {}

    return {
        "name": cand.get("name", fallback_name),
        "lat": lat,
        "lon": lon,
        "address": cand.get("address", ""),
        "poi_id": cand.get("id", ""),
    }


def _get_origin(gcp_store: Any, state: AgentState) -> Dict[str, float]:
    """获取起点位置（车辆当前位置）。"""
    if gcp_store:
        try:
            pos = gcp_store.get("vehicle.position") or {}
            if pos and pos.get("lat"):
                return {"lat": float(pos["lat"]), "lon": float(pos["lon"])}
        except Exception:
            pass
    gcp_snapshot = state.get("gcp_snapshot") or {}
    veh_pos = (gcp_snapshot.get("vehicle") or {}).get("position") or {}
    if veh_pos.get("lat"):
        return {"lat": float(veh_pos["lat"]), "lon": float(veh_pos["lon"])}
    return {"lat": 31.2359, "lon": 121.4996}  # 默认陆家嘴
