"""Skill_Dynamic_ETA —— 动态到达时间计算。

基于当前车辆位置和路线，计算剩余到达时间、置信区间，
并检测 ETA 变化以触发提醒或重规划。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..adapters.amap import AmapError, get_amap
from ..core.logging import get_logger
from .base import BaseSkill, SkillResult, SkillStatus

logger = get_logger(__name__)


class DynamicETASkill(BaseSkill):
    """动态 ETA Skill。"""

    name = "dynamic_eta"
    description = (
        "实时到达时间（ETA）计算服务。"
        "根据当前位置和目的地计算剩余距离和到达时间，"
        "提供置信区间估计、ETA 变化检测和到达预警。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["eta_query", "eta_arrival_alert", "eta_delta_check"],
                "default": "eta_query",
                "description": "执行的操作类型",
            },
            "current_position": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                },
                "required": ["lat", "lon"],
                "description": "当前车辆位置",
            },
            "destination": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                    "name": {"type": "string"},
                },
                "required": ["lat", "lon"],
                "description": "目的地位置",
            },
            "route_polyline": {
                "type": "string",
                "description": "当前路线 polyline（用于更精确 ETA）",
            },
            "total_distance_km": {
                "type": "number",
                "description": "全程总距离（公里）",
            },
            "total_duration_min": {
                "type": "number",
                "description": "全程总时间（分钟）",
            },
            "previous_eta_min": {
                "type": "number",
                "description": "上一次的 ETA 剩余分钟（delta 检测用）",
            },
            "threshold_min": {
                "type": "number",
                "default": 5,
                "description": "ETA 变化告警阈值（分钟）",
            },
            "alert_before_min": {
                "type": "number",
                "default": 5,
                "description": "到达前多少分钟触发预警",
            },
        },
        "required": ["action", "current_position", "destination"],
    }
    gcp_dependencies = [
        "vehicle.position",
        "vehicle.speed_kmh",
        "traffic.on_route.overall_status",
    ]

    # 不同交通状态下的置信区间（分钟 / 小时 = %）
    CONFIDENCE_BAND_RATIO = {
        "smooth": 0.05,     # ±5%
        "slow": 0.10,       # ±10%
        "congested": 0.20,  # ±20%
        "severe": 0.30,     # ±30%
        "unknown": 0.15,
    }

    async def execute(
        self,
        params: Dict[str, Any],
        gcp_slice: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        action = params.get("action", "eta_query")

        try:
            if action == "eta_query":
                return await self._eta_query(params, gcp_slice)
            elif action == "eta_arrival_alert":
                return await self._eta_arrival_alert(params, gcp_slice)
            elif action == "eta_delta_check":
                return self._eta_delta_check(params)
            else:
                return SkillResult.error(f"unknown action: {action}")
        except AmapError as e:
            logger.error("dynamic_eta amap error: %s", e)
            return SkillResult.error(str(e), "到达时间计算暂时不可用")
        except Exception as e:
            logger.exception("dynamic_eta error: %s", e)
            return SkillResult.error(str(e), "ETA 计算出错")

    # ==================== eta_query ====================

    async def _eta_query(
        self, params: Dict[str, Any], gcp_slice: Dict[str, Any]
    ) -> SkillResult:
        current = params.get("current_position")
        dest = params.get("destination")
        if not current or not dest:
            # 参数不足，返回估算值
            logger.warning("eta_query missing current_position or destination, using estimate")
            now = datetime.now()
            return SkillResult.success({
                "remaining_min": 30,
                "remaining_km": 10.0,
                "eta_arrival_time": (now + timedelta(minutes=30)).strftime("%H:%M"),
                "traffic_level": "unknown",
                "confidence_band_min": 10,
            }, "ETA 估算（缺少位置信息）")

        amap = get_amap()

        # 方法1：用距离测量 API 计算驾车距离和时间（更准确）
        try:
            dist_results = await amap.distance(
                origins=[(current["lon"], current["lat"])],
                destination=(dest["lon"], dest["lat"]),
                type_=1,  # 驾车距离
            )
            if dist_results:
                distance_m = float(dist_results[0].get("distance", 0))
                duration_sec = float(dist_results[0].get("duration", 0))
                remaining_km = distance_m / 1000
                remaining_min = round(duration_sec / 60)
            else:
                remaining_km = 0.0
                remaining_min = 0
        except Exception as e:
            logger.warning("distance API failed, using estimate: %s", e)
            # 方法2：用直线距离 * 系数估算
            remaining_km = self._haversine_km(
                current["lat"], current["lon"], dest["lat"], dest["lon"]
            )
            # 假设平均车速 40km/h
            remaining_min = round(remaining_km / 40 * 60)

        # 计算到达时间
        now = datetime.now()
        arrival_time = now + timedelta(minutes=remaining_min)
        eta_arrival_time = arrival_time.strftime("%H:%M")

        # 获取交通状态，计算置信区间
        traffic_status = "unknown"
        try:
            traffic_data = gcp_slice.get("traffic", {})
            if traffic_data:
                on_route = traffic_data.get("on_route", {})
                if on_route:
                    traffic_status = on_route.get("overall_status", "unknown")
        except (KeyError, TypeError):
            pass

        confidence_ratio = self.CONFIDENCE_BAND_RATIO.get(traffic_status, 0.15)
        confidence_band_min = max(1, round(remaining_min * confidence_ratio))

        # 估算交通等级
        traffic_level = self._estimate_traffic_level(remaining_km, remaining_min)

        result = {
            "remaining_min": remaining_min,
            "remaining_km": round(remaining_km, 2),
            "eta_arrival_time": eta_arrival_time,
            "confidence_band_min": confidence_band_min,
            "traffic_level": traffic_level,
            "traffic_status": traffic_status,
            "destination_name": dest.get("name", ""),
        }

        dest_name = dest.get("name", "目的地")
        if remaining_min >= 60:
            hours = remaining_min // 60
            mins = remaining_min % 60
            eta_text = f"预计{hours}小时{mins}分钟后到达{dest_name}，到达时间约{eta_arrival_time}"
        else:
            eta_text = f"预计{remaining_min}分钟后到达{dest_name}，到达时间约{eta_arrival_time}"

        return SkillResult.success(result, eta_text)

    # ==================== eta_arrival_alert ====================

    async def _eta_arrival_alert(
        self, params: Dict[str, Any], gcp_slice: Dict[str, Any]
    ) -> SkillResult:
        """到达前 N 分钟预警。"""
        # 先查询当前 ETA
        eta_result = await self._eta_query(params, gcp_slice)
        if eta_result.status != SkillStatus.SUCCESS:
            return eta_result

        remaining_min = eta_result.data["remaining_min"]
        alert_before_min = params.get("alert_before_min", 5)
        dest_name = params["destination"].get("name", "目的地")

        should_alert = remaining_min <= alert_before_min and remaining_min > 0

        if should_alert:
            message = f"即将到达{dest_name}，还有约{remaining_min}分钟，请做好下车准备。"
        else:
            message = f"距离{dest_name}还有约{remaining_min}分钟。"

        result = {
            "should_alert": should_alert,
            "remaining_min": remaining_min,
            "alert_before_min": alert_before_min,
            "message": message,
        }

        return SkillResult.success(result, message)

    # ==================== eta_delta_check ====================

    def _eta_delta_check(self, params: Dict[str, Any]) -> SkillResult:
        """检测 ETA 是否发生显著变化。"""
        previous_eta = params.get("previous_eta_min")
        current_eta = params.get("current_eta_min")
        threshold = params.get("threshold_min", 5)

        if previous_eta is None or current_eta is None:
            return SkillResult.error("previous_eta_min and current_eta_min are required")

        delta = current_eta - previous_eta
        has_delta = abs(delta) >= threshold
        direction = "slower" if delta > 0 else "faster"
        delta_percent = round(abs(delta) / previous_eta * 100, 1) if previous_eta > 0 else 0

        # 判断是否显著（绝对值 > 阈值 且 百分比 > 10%）
        is_significant = has_delta and delta_percent > 10

        result = {
            "has_delta": has_delta,
            "is_significant": is_significant,
            "delta_min": delta,
            "delta_percent": delta_percent,
            "direction": direction,
            "previous_eta_min": previous_eta,
            "current_eta_min": current_eta,
            "threshold_min": threshold,
        }

        if not has_delta:
            message = "到达时间无明显变化"
        elif direction == "slower":
            message = f"预计到达时间推迟了{abs(delta)}分钟，可能受到路况影响。"
        else:
            message = f"预计到达时间提前了{abs(delta)}分钟。"

        return SkillResult.success(result, message)

    # ==================== 辅助方法 ====================

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """计算两点间的大圆距离（公里）。"""
        import math
        R = 6371.0  # 地球半径（公里）
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    @staticmethod
    def _estimate_traffic_level(distance_km: float, duration_min: float) -> str:
        """根据平均速度估算交通等级。"""
        if duration_min <= 0:
            return "unknown"
        avg_speed_kmh = distance_km / (duration_min / 60)
        if avg_speed_kmh >= 50:
            return "smooth"
        elif avg_speed_kmh >= 30:
            return "slow"
        elif avg_speed_kmh >= 15:
            return "congested"
        else:
            return "severe"


# 全局实例
_dynamic_eta: Optional[DynamicETASkill] = None


def get_dynamic_eta() -> DynamicETASkill:
    global _dynamic_eta
    if _dynamic_eta is None:
        _dynamic_eta = DynamicETASkill()
    return _dynamic_eta
