"""destination_confirm 节点：目的地消歧确认。

调用 local_poi.poi_resolve 或 amap.place_text 获取候选 POI，
唯一匹配则自动确认，多候选则列出等待用户选择。
"""

from __future__ import annotations

from typing import Any, Dict, List

from langgraph.types import RunnableConfig

from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def destination_confirm_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """目的地确认节点。

    1. 取 destination_name
    2. 调用 poi_resolve 获取候选列表
    3. 0 候选 → 返回空，由路由回到 clarifying
    4. 1 候选 → 自动确认，写入 destination
    5. 多候选 → 写入 destination_candidates，生成选择问题
    """
    configurable = config.get("configurable", {}) if config else {}
    skill_executor = configurable.get("skill_executor")
    stream_queue = configurable.get("stream_queue")
    gcp_store = configurable.get("gcp_store")

    # 如果 destination 已有有效坐标（例如用户从候选列表中选择），直接确认
    existing_dest = state.get("destination") or {}
    dlat = existing_dest.get("lat")
    dlon = existing_dest.get("lon")
    if (dlat is not None and dlon is not None
            and dlat != 0 and dlon != 0):
        return {
            "destination": existing_dest,
            "destination_candidates": [],
            "journey_status": "destination_confirm",
        }

    dest_name = (state.get("destination_name") or "").strip()
    if not dest_name:
        return {
            "destination_candidates": [],
            "destination": {},
            "journey_status": "destination_confirm",
        }

    # 获取车辆位置作为搜索中心
    center_position = None
    if gcp_store:
        try:
            pos = gcp_store.get("vehicle.position") or {}
            if pos and pos.get("lat"):
                center_position = {"lat": pos["lat"], "lon": pos["lon"]}
        except Exception:
            pass

    if not center_position and state.get("gcp_snapshot"):
        gcp = state["gcp_snapshot"]
        pos = (gcp.get("vehicle") or {}).get("position") or {}
        if pos.get("lat"):
            center_position = {"lat": pos["lat"], "lon": pos["lon"]}

    # 调用 local_poi 或 amap 做 POI 搜索
    candidates: List[Dict[str, Any]] = []
    if skill_executor:
        try:
            params = {
                "keyword": dest_name,
                "candidates_count": 5,
            }
            if center_position:
                params["center_position"] = center_position

            result = await skill_executor.execute_skill(
                "local_poi", "poi_resolve", params, task_id="dest_resolve",
            )
            if result.get("status") == "success":
                data = result.get("data") or {}
                raw_cands = data.get("candidates") or []
                for c in raw_cands[:5]:
                    loc = c.get("location") or c.get("position") or {}
                    # location 可能是 string "lon,lat"，统一转为 {lat, lon} dict
                    if isinstance(loc, str) and "," in loc:
                        try:
                            parts = loc.split(",")
                            loc = {"lon": float(parts[0]), "lat": float(parts[1])}
                        except (ValueError, IndexError):
                            loc = {}
                    candidates.append({
                        "id": c.get("id", ""),
                        "name": c.get("name", ""),
                        "address": c.get("address", ""),
                        "location": loc,
                        "distance": c.get("distance", ""),
                        "category": c.get("category", ""),
                    })
        except Exception as e:
            logger.error("poi_resolve error: %s", e)

    # 如果通过 Skill 没拿到结果，尝试直接用 amap（降级）
    if not candidates:
        candidates = await _fallback_geocode(dest_name, gcp_store)

    # 判断结果
    if not candidates:
        # 没找到，需要重新问
        question = f"没有找到「{dest_name}」，可以换个说法吗？"
        if stream_queue:
            try:
                stream_queue.put_nowait(("clarify_question", {"question": question}))
            except Exception:
                pass
        return {
            "destination_candidates": [],
            "destination": {},
            "clarify_question": question,
            "journey_status": "destination_confirm",
        }

    if len(candidates) == 1:
        # 唯一匹配，自动确认
        c = candidates[0]
        loc = c.get("location") or {}
        destination = {
            "name": c.get("name", dest_name),
            "address": c.get("address", ""),
            "lat": loc.get("lat", 0.0),
            "lon": loc.get("lon", 0.0),
            "poi_id": c.get("id", ""),
        }
        return {
            "destination": destination,
            "destination_candidates": candidates,
            "journey_status": "destination_confirm",
        }

    # 多候选，推送给用户选择（通过 clarify_question 事件）
    options = [f"{i+1}. {c['name']}（{c.get('address', '')}）" for i, c in enumerate(candidates[:5])]
    question = f"找到多个「{dest_name}」，您是指哪一个？\n" + "\n".join(options)

    if stream_queue:
        try:
            stream_queue.put_nowait(("clarify_question", {
                "question": question,
                "candidates": candidates[:5],
            }))
        except Exception:
            pass

    return {
        "destination_candidates": candidates[:5],
        "destination": {},  # 等用户选
        "clarify_question": question,
        "journey_status": "destination_confirm",
    }


async def _fallback_geocode(
    dest_name: str, gcp_store: Any,
) -> List[Dict[str, Any]]:
    """降级方案：直接用 amap geocode 解析。"""
    try:
        from ...adapters.amap import get_amap
        amap = get_amap()
        result = await amap.geocode(dest_name)
        geocodes = result.get("geocodes") or []
        candidates = []
        for g in geocodes[:5]:
            location_str = g.get("location", "")
            if location_str:
                parts = location_str.split(",")
                if len(parts) == 2:
                    candidates.append({
                        "id": g.get("adcode", ""),
                        "name": g.get("formatted_address", dest_name),
                        "address": g.get("formatted_address", ""),
                        "location": {"lon": float(parts[0]), "lat": float(parts[1])},
                        "distance": "",
                        "category": g.get("level", ""),
                    })
        return candidates
    except Exception as e:
        logger.error("fallback geocode error: %s", e)
        return []
