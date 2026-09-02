"""Skill_Parking_Find —— 停车场搜索与推荐。

支持普通目的地周边停车场搜索和交通枢纽（机场/火车站）模式。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..adapters.amap import AmapError, get_amap
from ..core.logging import get_logger
from .base import BaseSkill, SkillResult, SkillStatus

logger = get_logger(__name__)


class ParkingFindSkill(BaseSkill):
    """停车场搜索 Skill。"""

    name = "parking_find"
    description = (
        "停车场搜索与推荐服务。"
        "查找目的地周边的停车场，支持普通模式和交通枢纽模式（机场/火车站）。"
        "返回停车场名称、地址、距离、步行时间、价格等信息，"
        "并根据用户偏好（便利/便宜/均衡）排序推荐。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["parking_search", "parking_transit_hub"],
                "default": "parking_search",
                "description": "搜索模式：普通搜索或交通枢纽模式",
            },
            "destination_position": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                },
                "required": ["lat", "lon"],
                "description": "目的地位置",
            },
            "destination_name": {
                "type": "string",
                "description": "目的地名称（用于自动识别交通枢纽）",
            },
            "radius_m": {
                "type": "integer",
                "default": 500,
                "description": "搜索半径（米），默认 500 米",
            },
            "user_preference": {
                "type": "string",
                "enum": ["convenience", "cheap", "balance"],
                "default": "convenience",
                "description": "排序偏好：便利优先/价格优先/均衡",
            },
            "limit": {
                "type": "integer",
                "default": 5,
                "description": "返回结果数量",
            },
            "hub_name": {
                "type": "string",
                "description": "交通枢纽名称（交通枢纽模式使用，如'虹桥T2'、'浦东T1'）",
            },
            "preference": {
                "type": "string",
                "enum": ["parking", "dropoff"],
                "default": "parking",
                "description": "交通枢纽模式偏好：停车/送客",
            },
        },
        "required": ["action", "destination_position"],
    }
    gcp_dependencies = [
        "user_profile.travel_preferences.parking_preference",
    ]

    # 高德停车场类型编码（交通设施服务-停车场）
    PARKING_TYPE = "150900|150901|150902|150903|150904|150905|150906"

    # 已知交通枢纽数据（MVP 阶段硬编码，后续可接入数据库）
    TRANSIT_HUBS = {
        "虹桥T2": {
            "name": "虹桥国际机场T2航站楼",
            "parking_lots": [
                {
                    "name": "P7停车库（到达）",
                    "position": {"lat": 31.1954, "lon": 121.3353},
                    "walk_min": 5,
                    "price": "8元/小时",
                    "type": "underground",
                    "entry_hint": "沿虹渝高架方向进入P7入口",
                    "recommended_for": "接人/停车",
                },
                {
                    "name": "P8停车库（出发）",
                    "position": {"lat": 31.1968, "lon": 121.3378},
                    "walk_min": 3,
                    "price": "8元/小时",
                    "type": "underground",
                    "entry_hint": "沿出发层方向进入P8入口",
                    "recommended_for": "送机/短时停车",
                },
            ],
            "terminal_guide": "虹桥T2到达层在2楼，接机请在到达口等候",
        },
        "虹桥T1": {
            "name": "虹桥国际机场T1航站楼",
            "parking_lots": [
                {
                    "name": "P1停车场",
                    "position": {"lat": 31.1970, "lon": 121.3360},
                    "walk_min": 5,
                    "price": "6元/小时",
                    "type": "ground",
                    "entry_hint": "沿迎宾一路进入",
                    "recommended_for": "接人",
                },
            ],
            "terminal_guide": "虹桥T1航站楼",
        },
        "浦东T2": {
            "name": "浦东国际机场T2航站楼",
            "parking_lots": [
                {
                    "name": "P2停车库",
                    "position": {"lat": 31.1460, "lon": 121.8080},
                    "walk_min": 5,
                    "price": "10元/小时",
                    "type": "underground",
                    "entry_hint": "沿S1迎宾高速进入P2",
                    "recommended_for": "接机/停车",
                },
            ],
            "terminal_guide": "浦东T2到达层",
        },
        "浦东T1": {
            "name": "浦东国际机场T1航站楼",
            "parking_lots": [
                {
                    "name": "P1停车库",
                    "position": {"lat": 31.1470, "lon": 121.8020},
                    "walk_min": 5,
                    "price": "10元/小时",
                    "type": "underground",
                    "entry_hint": "沿S1迎宾高速进入P1",
                    "recommended_for": "接机/停车",
                },
            ],
            "terminal_guide": "浦东T1到达层",
        },
    }

    async def execute(
        self,
        params: Dict[str, Any],
        gcp_slice: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        action = params.get("action", "parking_search")

        # 获取用户偏好
        default_pref = "convenience"
        try:
            profile_pref = gcp_slice["user_profile"]["travel_preferences"]["parking_preference"]
            if profile_pref in ("convenience", "cheap", "balance"):
                default_pref = profile_pref
        except (KeyError, TypeError):
            pass

        user_pref = params.get("user_preference", default_pref)

        try:
            if action == "parking_search":
                return await self._parking_search(params, user_pref)
            elif action == "parking_transit_hub":
                return await self._parking_transit_hub(params, user_pref)
            else:
                return SkillResult.error(f"unknown action: {action}")
        except AmapError as e:
            logger.error("parking_find amap error: %s", e)
            return SkillResult.error(str(e), "停车场搜索服务暂时不可用")
        except Exception as e:
            logger.exception("parking_find error: %s", e)
            return SkillResult.error(str(e), "停车场搜索出错")

    # ==================== parking_search ====================

    async def _parking_search(
        self, params: Dict[str, Any], user_pref: str
    ) -> SkillResult:
        """普通停车场搜索（目的地周边）。"""
        dest = params["destination_position"]
        dest_name = params.get("destination_name", "目的地")
        radius_m = params.get("radius_m", 500)
        limit = params.get("limit", 5)

        amap = get_amap()

        # 使用周边搜索 + 停车场类型
        try:
            pois = await amap.place_around(
                location=(dest["lon"], dest["lat"]),
                types=self.PARKING_TYPE,
                radius=radius_m,
                offset=max(limit * 2, 10),
                extensions="all",
                sort="distance",
            )
        except AmapError:
            # 尝试用关键词搜索
            pois = await amap.place_around(
                location=(dest["lon"], dest["lat"]),
                keyword="停车场",
                radius=radius_m,
                offset=max(limit * 2, 10),
                extensions="all",
                sort="distance",
            )

        if not pois:
            return SkillResult.no_result(
                f"{dest_name}周边{radius_m}米内未找到停车场，建议扩大搜索范围。"
            )

        parking_lots = []
        for poi in pois:
            lot = self._parse_parking_poi(poi, dest)
            if lot:
                parking_lots.append(lot)

        if not parking_lots:
            return SkillResult.no_result(
                f"{dest_name}周边{radius_m}米内未找到合适的停车场。"
            )

        # 按偏好排序
        sorted_lots = self._sort_by_preference(parking_lots, user_pref)
        result_lots = sorted_lots[:limit]

        # 计算步行时间（如果没有）
        for lot in result_lots:
            if lot.get("walk_min", 0) == 0:
                lot["walk_min"] = max(1, round(lot.get("distance_m", 0) / 80))

        result = {
            "parking_lots": result_lots,
            "total_found": len(parking_lots),
            "sorted_by": user_pref,
            "destination_name": dest_name,
            "recommended_index": 0,
        }

        if result_lots:
            nearest = result_lots[0]
            dist_m = nearest.get('distance_m', 0)
            if dist_m >= 1000:
                dist_str = f"{dist_m/1000:.1f}公里"
            else:
                dist_str = f"{int(dist_m)}米"
            message = (
                f"为您找到{dest_name}周边{len(result_lots)}个停车场，"
                f"推荐{nearest['name']}，距离{dist_str}，"
                f"步行约{nearest.get('walk_min', 0)}分钟。"
            )
            if nearest.get("price"):
                message += f" 收费标准：{nearest['price']}。"
        else:
            message = "附近未找到合适的停车场。"

        return SkillResult.success(result, message)

    # ==================== parking_transit_hub ====================

    async def _parking_transit_hub(
        self, params: Dict[str, Any], user_pref: str
    ) -> SkillResult:
        """交通枢纽模式：使用预设数据 + 周边搜索补充。"""
        hub_name = params.get("hub_name", "")
        dest_name = params.get("destination_name", "")
        dest = params["destination_position"]
        pref = params.get("preference", "parking")

        # 自动识别枢纽
        if not hub_name:
            hub_name = self._detect_hub(dest_name or "")

        # 如果是已知枢纽，使用预设数据
        if hub_name in self.TRANSIT_HUBS:
            hub = self.TRANSIT_HUBS[hub_name]
            parking_lots = []

            for lot in hub["parking_lots"]:
                lot_copy = {
                    **lot,
                    "distance_m": 0,  # 预设停车场距离在 walk_min 中体现
                    "walk_min": lot.get("walk_min", 5),
                    "is_transit_hub": True,
                }
                # 统一坐标字段：position → location（与 amap 搜索结果对齐）
                pos = lot_copy.pop("position", None)
                if pos and not lot_copy.get("location"):
                    lot_copy["location"] = pos
                parking_lots.append(lot_copy)

            # 送客模式：推荐最短步行的
            recommended_idx = 0
            if pref == "dropoff":
                # 找出发层停车场
                for i, lot in enumerate(parking_lots):
                    if "出发" in lot.get("recommended_for", "") or "dropoff" in lot.get("entry_hint", ""):
                        recommended_idx = i
                        break
            else:
                # 停车模式：找到达层停车场
                for i, lot in enumerate(parking_lots):
                    if "到达" in lot.get("recommended_for", "") or "接人" in lot.get("recommended_for", ""):
                        recommended_idx = i
                        break

            result = {
                "parking_lots": parking_lots,
                "hub_name": hub_name,
                "hub_full_name": hub["name"],
                "terminal_guide": hub["terminal_guide"],
                "entry_hint": parking_lots[recommended_idx].get("entry_hint", ""),
                "recommended_index": recommended_idx,
                "is_transit_hub": True,
                "sorted_by": "transit_hub",
            }

            rec = parking_lots[recommended_idx]
            message = (
                f"{hub_name}推荐{rec['name']}，"
                f"步行至航站楼约{rec.get('walk_min', 5)}分钟，"
                f"{rec.get('entry_hint', '')}。"
                f"{hub.get('terminal_guide', '')}。"
            )
            return SkillResult.success(result, message)

        # 未知枢纽，降级为普通搜索
        return await self._parking_search(params, user_pref)

    # ==================== 辅助方法 ====================

    @staticmethod
    def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """计算两点间的大圆距离（米）。"""
        import math
        R = 6371000.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    @staticmethod
    def _parse_parking_poi(poi: Dict[str, Any], center: Dict[str, float]) -> Dict[str, Any]:
        """从高德 POI 数据解析停车场信息。"""
        location_str = poi.get("location", "")
        lat, lon = 0.0, 0.0
        if location_str:
            try:
                parts = location_str.split(",")
                lon = float(parts[0])
                lat = float(parts[1])
            except (ValueError, IndexError):
                pass

        # 获取距离：优先使用 API 返回的 distance，没有则用 haversine 估算
        distance_str = poi.get("distance", "")
        distance_m = 0.0
        if distance_str and str(distance_str).strip():
            try:
                distance_m = float(distance_str)
            except (ValueError, TypeError):
                distance_m = 0.0

        # 如果 distance 为 0 且有坐标，计算直线距离
        if distance_m <= 0 and lat > 0 and lon > 0 and center.get("lat", 0) > 0:
            distance_m = ParkingFindSkill._haversine_m(
                center["lat"], center["lon"], lat, lon
            )

        # 尝试从 biz_ext 或 deep_type 提取价格信息
        price = ""
        biz_ext = poi.get("biz_ext", {})
        if isinstance(biz_ext, dict):
            cost = biz_ext.get("cost", "")
            if cost:
                price = f"{cost}元/小时" if cost.isdigit() else cost

        # 判断停车场类型
        name = poi.get("name", "")
        type_str = poi.get("type", "")
        if "地下" in name or "车库" in name:
            p_type = "underground"
        elif "地面" in name:
            p_type = "ground"
        elif "立体" in name:
            p_type = "stereo"
        else:
            p_type = "general"

        return {
            "id": poi.get("id", ""),
            "name": name,
            "address": poi.get("address", ""),
            "location": {"lat": lat, "lon": lon},
            "distance_m": distance_m,
            "walk_min": max(1, round(distance_m / 80)),  # 约 80m/分钟
            "price": price,
            "type": p_type,
            "tel": poi.get("tel", ""),
            "rating": poi.get("rating", ""),
            "total_spaces": None,  # 高德不提供实时车位数
            "is_transit_hub": False,
        }

    @staticmethod
    def _sort_by_preference(
        lots: List[Dict[str, Any]], preference: str
    ) -> List[Dict[str, Any]]:
        """根据用户偏好排序停车场。"""
        if preference == "convenience":
            # 按距离升序
            return sorted(lots, key=lambda x: x.get("distance_m", 99999))
        elif preference == "cheap":
            # 按价格排序（无价格的排在后面）
            def price_key(lot):
                p = lot.get("price", "")
                if not p:
                    return (1, 999, lot.get("distance_m", 99999))
                try:
                    price_num = float("".join(c for c in p if c.isdigit() or c == "."))
                    return (0, price_num, lot.get("distance_m", 99999))
                except ValueError:
                    return (1, 999, lot.get("distance_m", 99999))
            return sorted(lots, key=price_key)
        else:  # balance
            # 距离 * 0.7 + 价格 * 0.3（综合评分）
            def balance_key(lot):
                dist_score = lot.get("distance_m", 99999) / 1000  # 转为公里
                price_score = 0
                p = lot.get("price", "")
                try:
                    price_num = float("".join(c for c in p if c.isdigit() or c == "."))
                    price_score = price_num
                except (ValueError, TypeError):
                    price_score = 10  # 默认中等价格
                return dist_score * 0.7 + price_score * 0.03  # 距离权重更大
            return sorted(lots, key=balance_key)

    @staticmethod
    def _detect_hub(name: str) -> str:
        """根据名称自动识别交通枢纽。"""
        if not name:
            return ""
        name_lower = name.lower()
        if "虹桥" in name and "t2" in name_lower:
            return "虹桥T2"
        if "虹桥" in name and "t1" in name_lower:
            return "虹桥T1"
        if "浦东" in name and "t2" in name_lower:
            return "浦东T2"
        if "浦东" in name and "t1" in name_lower:
            return "浦东T1"
        if "虹桥机场" in name or ("虹桥" in name and "机场" in name):
            return "虹桥T2"
        if "浦东机场" in name or ("浦东" in name and "机场" in name):
            return "浦东T2"
        return ""


# 全局实例
_parking_find: Optional[ParkingFindSkill] = None


def get_parking_find() -> ParkingFindSkill:
    global _parking_find
    if _parking_find is None:
        _parking_find = ParkingFindSkill()
    return _parking_find
