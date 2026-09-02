"""Skill_Route_Master —— 路线规划中枢。

封装高德路径规划能力，支持：
- 单目的地路线规划（route_single）
- 多目的地顺序路线（route_multi）
- 重规划（route_reroute）
- 绕路检测（route_detour_check）
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from ..adapters.amap import AmapError, get_amap
from ..core.logging import get_logger
from ..utils.polyline import decode_amap_polyline
from .base import BaseSkill, SkillResult, SkillStatus

logger = get_logger(__name__)


def _normalize_polyline(encoded: str) -> str:
    """将高德 v5 API 返回的 polyline 解码为 "lon,lat;lon,lat;..." 纯文本格式。

    高德 v5 驾车规划返回的 polyline 字段可能是：
    1. 编码格式（base64-like 可变长度整数编码，从 ASCII 63 开始）
    2. 已经是 "lon,lat;lon,lat;..." 的纯文本格式（某些情况下）

    统一输出纯文本格式，方便前端直接解析。
    """
    if not encoded:
        return ""
    # 如果已经是纯文本格式（包含分号或逗号+数字），直接返回
    if ";" in encoded:
        return encoded
    # 尝试解码
    try:
        points = decode_amap_polyline(encoded)
        if points:
            return ";".join(f"{lon},{lat}" for lon, lat in points)
    except Exception as e:
        logger.warning("Failed to decode amap polyline: %s", e)
    return ""


class RouteMasterSkill(BaseSkill):
    """路线规划中枢 Skill。"""

    name = "route_master"
    description = (
        "路线规划与导航服务。支持单目的地驾车路线、多目的地顺序路线、"
        "路线重规划、绕路检测。返回路线距离、时间、收费、polyline 和途经点信息。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["route_single", "route_multi", "route_reroute", "route_detour_check"],
                "description": "执行的操作类型",
            },
            "origin": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "起点纬度"},
                    "lon": {"type": "number", "description": "起点经度"},
                    "name": {"type": "string", "description": "起点名称"},
                },
                "required": ["lat", "lon"],
                "description": "起点位置",
            },
            "destination": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                    "name": {"type": "string"},
                },
                "required": ["lat", "lon"],
                "description": "终点位置（单路线时使用）",
            },
            "destinations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number"},
                        "lon": {"type": "number"},
                        "name": {"type": "string"},
                    },
                },
                "description": "多目的地列表（route_multi 时使用）",
            },
            "strategy": {
                "type": "string",
                "enum": ["time_first", "no_toll", "shortest", "balance"],
                "default": "time_first",
                "description": "路线策略",
            },
            "current_position": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                },
                "description": "当前位置（重规划/绕路检测时使用）",
            },
            "original_route_polyline": {
                "type": "string",
                "description": "原始路线 polyline（绕路检测用）",
            },
            "threshold_km": {
                "type": "number",
                "default": 2.0,
                "description": "绕路检测阈值（公里）",
            },
            "reason": {
                "type": "string",
                "description": "重规划原因（如 traffic_jam、user_request）",
            },
        },
        "required": ["action", "origin"],
    }
    gcp_dependencies = [
        "vehicle.position",
        "user_profile.travel_preferences.route_preference",
    ]

    # 用户偏好 → 高德 strategy 的映射
    # 高德 strategy 参数: 0=速度优先 1=费用优先 2=距离优先 3=不走高速
    #                      10=躲避拥堵 11=躲避拥堵&避开收费 12=躲避拥堵&不走高速
    PREFERENCE_TO_STRATEGY = {
        "time_first": 0,    # 速度优先
        "no_toll": 11,       # 躲避拥堵&避开收费
        "shortest": 2,       # 距离优先
        "balance": 10,       # 躲避拥堵（均衡）
    }

    async def execute(
        self,
        params: Dict[str, Any],
        gcp_slice: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        action = params.get("action", "route_single")

        # 从 GCP 获取用户偏好，作为默认策略
        default_strategy = "time_first"
        try:
            profile_pref = gcp_slice["user_profile"]["travel_preferences"]["route_preference"]
            if profile_pref in self.PREFERENCE_TO_STRATEGY:
                default_strategy = profile_pref
        except (KeyError, TypeError):
            pass

        strategy_name = params.get("strategy", default_strategy)

        try:
            if action == "route_single":
                return await self._route_single(params, strategy_name)
            elif action == "route_multi":
                return await self._route_multi(params, strategy_name)
            elif action == "route_reroute":
                return await self._route_reroute(params, strategy_name)
            elif action == "route_detour_check":
                return await self._route_detour_check(params)
            else:
                return SkillResult.error(f"unknown action: {action}")
        except AmapError as e:
            logger.error("route_master amap error: %s", e)
            return SkillResult.error(str(e), "路线规划服务暂时不可用")
        except Exception as e:
            logger.exception("route_master error: %s", e)
            return SkillResult.error(str(e), "路线规划出错")

    # ==================== route_single ====================

    async def _route_single(
        self, params: Dict[str, Any], strategy_name: str
    ) -> SkillResult:
        origin = params.get("origin")
        dest = params.get("destination")
        strategy = self.PREFERENCE_TO_STRATEGY.get(strategy_name, 0)

        if not origin or not dest:
            # 参数不足，返回估算路线
            logger.warning("route_single missing origin/destination, using estimate")
            return SkillResult.success({
                "distance_km": 10.0,
                "duration_min": 30,
                "toll_cny": 0,
                "strategy": strategy_name,
                "polyline": "",
                "steps": [],
                "tmcs": [],
            }, "路线估算（缺少位置信息）")

        amap = get_amap()
        route_data = await amap.direction_driving(
            origin=(origin["lon"], origin["lat"]),
            destination=(dest["lon"], dest["lat"]),
            strategy=strategy,
        )

        paths = route_data.get("paths", [])
        if not paths:
            return SkillResult.no_result("未找到可行路线")

        # 取第一条（最优）路线
        path = paths[0]
        distance_km = float(path.get("distance", 0)) / 1000

        # v5 API: duration/tolls 在 cost 子字典里
        cost = path.get("cost", {}) or {}
        duration_sec = float(cost.get("duration", path.get("duration", 0) or 0))
        duration_min = round(duration_sec / 60)
        toll_cny = float(cost.get("tolls", path.get("tolls", 0) or 0))

        steps = path.get("steps", [])

        # v5 API: polyline 在每个 step 中，需要拼接完整路线（同时解码）
        polyline_parts = []
        for step in steps:
            step_poly = step.get("polyline", "")
            if step_poly:
                decoded = _normalize_polyline(step_poly)
                if decoded:
                    polyline_parts.append(decoded)
        polyline = ";".join(polyline_parts)
        tmcs = []
        for step in steps:
            step_tmcs = step.get("tmcs", [])
            if isinstance(step_tmcs, list):
                # 将 v5 格式转换为标准格式
                for t in step_tmcs:
                    tmcs.append({
                        "status": t.get("tmc_status", "未知"),
                        "length": t.get("tmc_distance", 0),
                    })
        traffic_summary = self._aggregate_tmcs(tmcs)

        result = {
            "route_id": f"route_{int(time.time())}",
            "distance_km": round(distance_km, 2),
            "duration_min": duration_min,
            "toll_cny": toll_cny,
            "polyline": polyline,
            "strategy": strategy_name,
            "origin_name": origin.get("name", ""),
            "destination_name": dest.get("name", ""),
            "steps_count": len(steps),
            "traffic_summary": traffic_summary,
            "alternative_routes_count": len(paths) - 1,
        }

        message = (
            f"已为您规划从{origin.get('name', '当前位置')}到{dest.get('name', '目的地')}的路线，"
            f"全程约{distance_km:.1f}公里，预计{duration_min}分钟"
            + (f"，高速费{toll_cny:.0f}元" if toll_cny > 0 else "")
            + f"。{traffic_summary['description']}"
        )

        return SkillResult.success(result, message)

    # ==================== route_multi ====================

    async def _route_multi(
        self, params: Dict[str, Any], strategy_name: str
    ) -> SkillResult:
        origin = params["origin"]
        destinations = params.get("destinations", [])
        if not destinations:
            return SkillResult.error("destinations cannot be empty")

        strategy = self.PREFERENCE_TO_STRATEGY.get(strategy_name, 0)
        amap = get_amap()

        # 将目的地作为 waypoints 传给高德（最多 16 个）
        waypoints = [(d["lon"], d["lat"]) for d in destinations]
        if len(waypoints) > 16:
            waypoints = waypoints[:16]
            logger.warning("route_multi: truncating waypoints to 16")

        final_dest = destinations[-1]
        route_data = await amap.direction_driving(
            origin=(origin["lon"], origin["lat"]),
            destination=(final_dest["lon"], final_dest["lat"]),
            strategy=strategy,
            waypoints=waypoints[:-1] if len(waypoints) > 1 else None,
        )

        paths = route_data.get("paths", [])
        if not paths:
            return SkillResult.no_result("未找到可行路线")

        path = paths[0]
        total_distance_km = float(path.get("distance", 0)) / 1000
        # v5 API: duration 在 cost 子字典里
        cost = path.get("cost", {}) or {}
        total_duration_sec = float(cost.get("duration", path.get("duration", 0) or 0))
        total_duration_min = round(total_duration_sec / 60)

        # 计算各段（leg）的大致信息
        # 高德的多途经点返回是一条完整路线，这里粗略按途经点数量均分
        # 更精确的做法需要按 step 拆分 polyline，MVP 阶段简化处理
        num_legs = len(destinations)
        avg_distance = total_distance_km / num_legs if num_legs > 0 else 0
        avg_duration = total_duration_min / num_legs if num_legs > 0 else 0

        legs = []
        current = origin
        for i, dest in enumerate(destinations):
            legs.append({
                "leg_index": i,
                "from_name": current.get("name", ""),
                "to_name": dest.get("name", ""),
                "distance_km": round(avg_distance, 2),
                "duration_min": round(avg_duration),
            })
            current = dest

        # v5 API: polyline 在每个 step 中，需要拼接（同时解码）
        multi_poly_parts = []
        for step in path.get("steps", []):
            step_poly = step.get("polyline", "")
            if step_poly:
                decoded = _normalize_polyline(step_poly)
                if decoded:
                    multi_poly_parts.append(decoded)
        multi_polyline = ";".join(multi_poly_parts)

        result = {
            "route_id": f"route_multi_{int(time.time())}",
            "total_distance_km": round(total_distance_km, 2),
            "total_duration_min": total_duration_min,
            "legs_count": len(legs),
            "legs": legs,
            "polyline": multi_polyline,
            "strategy": strategy_name,
            "waypoints": destinations,
        }

        # 生成途经点名称列表
        dest_names = " → ".join([d.get("name", f"第{i+1}站") for i, d in enumerate(destinations)])
        message = (
            f"已为您规划多目的地路线：{origin.get('name', '起点')} → {dest_names}，"
            f"全程约{total_distance_km:.1f}公里，预计{total_duration_min}分钟。"
        )

        return SkillResult.success(result, message)

    # ==================== route_reroute ====================

    async def _route_reroute(
        self, params: Dict[str, Any], strategy_name: str
    ) -> SkillResult:
        current = params.get("current_position") or params.get("origin")
        dest = params.get("destination")
        reason = params.get("reason", "traffic")

        if not current or not dest:
            return SkillResult.error("current_position and destination are required")

        strategy = self.PREFERENCE_TO_STRATEGY.get(strategy_name, 0)
        amap = get_amap()

        # 计算新路线
        route_data = await amap.direction_driving(
            origin=(current["lon"], current["lat"]),
            destination=(dest["lon"], dest["lat"]),
            strategy=strategy,
        )

        paths = route_data.get("paths", [])
        if not paths:
            return SkillResult.no_result("未找到替代路线")

        path = paths[0]
        new_distance_km = float(path.get("distance", 0)) / 1000
        # v5 API: duration 在 cost 子字典里
        cost = path.get("cost", {}) or {}
        new_duration_sec = float(cost.get("duration", path.get("duration", 0) or 0))
        new_duration_min = round(new_duration_sec / 60)

        # 获取原来的剩余距离/时间（从 context 或估算）
        original_remaining_km = None
        original_remaining_min = None
        context = params.get("context") or {}
        if isinstance(context, dict):
            original_remaining_km = context.get("remaining_distance_km")
            original_remaining_min = context.get("remaining_duration_min")

        # v5 API: polyline 在每个 step 中（同时解码为纯文本格式）
        reroute_poly_parts = []
        for step in path.get("steps", []):
            step_poly = step.get("polyline", "")
            if step_poly:
                decoded = _normalize_polyline(step_poly)
                if decoded:
                    reroute_poly_parts.append(decoded)
        reroute_polyline = ";".join(reroute_poly_parts)

        result = {
            "route_id": f"reroute_{int(time.time())}",
            "new_distance_km": round(new_distance_km, 2),
            "new_duration_min": new_duration_min,
            "polyline": reroute_polyline,
            "reason": reason,
            "delta_distance_km": None,
            "delta_duration_min": None,
            "is_faster": None,
        }

        # 如果有原始数据，计算差值
        if original_remaining_km is not None:
            result["delta_distance_km"] = round(new_distance_km - original_remaining_km, 2)
        if original_remaining_min is not None:
            result["delta_duration_min"] = new_duration_min - original_remaining_min
            result["is_faster"] = new_duration_min < original_remaining_min

        # 生成播报文本
        dest_name = dest.get("name", "目的地")
        if result["delta_duration_min"] is not None:
            delta = result["delta_duration_min"]
            if delta > 0:
                msg = f"因{_reason_text(reason)}，已为您重新规划路线前往{dest_name}，预计比原来多{delta}分钟。"
            else:
                msg = f"因{_reason_text(reason)}，已为您重新规划路线前往{dest_name}，预计可节省{abs(delta)}分钟。"
        else:
            msg = f"因{_reason_text(reason)}，已为您重新规划路线前往{dest_name}，全程约{new_distance_km:.1f}公里，预计{new_duration_min}分钟。"

        return SkillResult.success(result, msg)

    # ==================== route_detour_check ====================

    async def _route_detour_check(self, params: Dict[str, Any]) -> SkillResult:
        current = params.get("current_position")
        polyline = params.get("original_route_polyline", "")
        threshold_km = params.get("threshold_km", 2.0)

        if not current:
            return SkillResult.error("current_position is required")
        if not polyline:
            # 没有路线就无法检测，返回正常
            return SkillResult.success({
                "is_detour": False,
                "detour_distance_km": 0.0,
                "suggestion": "当前在规划路线上",
            }, "当前在规划路线上行驶")

        # 简化版绕路检测：计算当前位置到终点的距离与原路线剩余距离的比值
        # 精确实现需要 polyline 解析和点到线段距离计算
        # MVP 阶段：用 distance API 计算当前到终点的直线距离，与原路线剩余距离比较
        amap = get_amap()
        destination = params.get("destination")

        if not destination:
            # 没有终点信息，跳过精确检测
            return SkillResult.success({
                "is_detour": False,
                "detour_distance_km": 0.0,
                "suggestion": "路线检测中",
            }, "路线检测中")

        # 用直线距离粗略估算
        dist_results = await amap.distance(
            origins=[(current["lon"], current["lat"])],
            destination=(destination["lon"], destination["lat"]),
            type_=1,  # 驾车距离
        )

        if not dist_results:
            return SkillResult.success({
                "is_detour": False,
                "detour_distance_km": 0.0,
                "suggestion": "路线检测中",
            }, "路线检测中")

        current_distance_km = float(dist_results[0].get("distance", 0)) / 1000

        # 获取原始剩余距离
        context = params.get("context") or {}
        original_remaining_km = context.get("remaining_distance_km") if isinstance(context, dict) else None

        is_detour = False
        detour_km = 0.0
        suggestion = "当前在规划路线上行驶"

        if original_remaining_km is not None:
            # 如果当前驾车距离比原剩余距离多出阈值，则认为绕路
            detour_km = current_distance_km - original_remaining_km
            if detour_km > threshold_km:
                is_detour = True
                suggestion = f"当前位置偏离规划路线约{detour_km:.1f}公里，建议重新规划路线"

        result = {
            "is_detour": is_detour,
            "detour_distance_km": round(detour_km, 2),
            "current_distance_km": round(current_distance_km, 2),
            "threshold_km": threshold_km,
            "suggestion": suggestion,
        }

        return SkillResult.success(result, suggestion)

    # ==================== 辅助方法 ====================

    # 高德 v5 TMC 状态码映射
    TMC_STATUS_MAP = {
        "畅通": "smooth",
        "缓行": "slow",
        "拥堵": "congested",
        "严重拥堵": "severe",
        "未知": "unknown",
    }
    # 拥堵类状态（用于聚合计算）
    CONGESTED_STATUSES = {"拥堵", "严重拥堵"}
    SLOW_STATUSES = {"缓行"}

    @staticmethod
    def _aggregate_tmcs(tmcs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """聚合路线 tmc 分段，得出整体交通状态。"""
        if not tmcs:
            return {
                "overall": "unknown",
                "description": "暂无交通信息",
                "congested_distance_m": 0,
            }

        status_count = {"畅通": 0, "缓行": 0, "拥堵": 0, "严重拥堵": 0, "未知": 0}
        total_length = 0
        congested_length = 0
        slow_length = 0

        for tmc in tmcs:
            status = tmc.get("status", "未知")
            try:
                length = float(tmc.get("length", 0))
            except (ValueError, TypeError):
                length = 0
            total_length += length
            if status in RouteMasterSkill.CONGESTED_STATUSES:
                congested_length += length
            elif status in RouteMasterSkill.SLOW_STATUSES:
                slow_length += length
            status_count[status] = status_count.get(status, 0) + 1

        # 判断整体状态
        congested_ratio = congested_length / total_length if total_length > 0 else 0
        slow_ratio = slow_length / total_length if total_length > 0 else 0

        if congested_ratio > 0.5:
            overall = "severe"
            desc = "路线严重拥堵"
        elif congested_ratio > 0.2:
            overall = "congested"
            desc = "部分路段拥堵"
        elif congested_ratio > 0.05 or slow_ratio > 0.2:
            overall = "slow"
            desc = "局部路段缓行"
        else:
            overall = "smooth"
            desc = "道路畅通"

        return {
            "overall": overall,
            "description": desc,
            "total_segments": len(tmcs),
            "congested_distance_m": round(congested_length, 0),
            "status_count": status_count,
        }


def _reason_text(reason: str) -> str:
    """将原因 code 转换为中文描述。"""
    mapping = {
        "traffic_jam": "前方拥堵",
        "traffic": "前方交通变化",
        "user_request": "您的要求",
        "accident": "前方事故",
        "road_closure": "前方道路封闭",
    }
    return mapping.get(reason, "路线调整")


# 全局实例
_route_master: Optional[RouteMasterSkill] = None


def get_route_master() -> RouteMasterSkill:
    global _route_master
    if _route_master is None:
        _route_master = RouteMasterSkill()
    return _route_master
