"""高德地图 Web 服务 API 适配器。

覆盖 MVP 需要的 7 类接口：
1. 地理编码（地址 → 坐标）     /v3/geocode/geo
2. 逆地理编码（坐标 → 地址）    /v3/geocode/regeo
3. 关键字搜索 POI              /v5/place/text
4. 周边搜索 POI                /v5/place/around
5. POI 详情（ID 查询）          /v5/place/detail
6. 驾车路径规划                /v5/direction/driving
7. 距离测量                    /v3/distance
8. 天气查询                    /v3/weather/weatherInfo
9. 交通态势（矩形/圆形/道路）   /v3/traffic/status/{rectangle|circle|road}

统一错误处理、超时重试、响应解析。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..core.config import settings
from ..core.errors import AppError
from ..core.logging import get_logger

logger = get_logger(__name__)


class AmapError(AppError):
    """高德 API 错误。"""

    def __init__(self, message: str, code: int = 50001):
        super().__init__(code=code, message=f"[AMAP] {message}")


class AmapClient:
    """高德地图 Web 服务 API 客户端。"""

    BASE_URL = "https://restapi.amap.com"

    def __init__(self, key: str = "") -> None:
        self.key = key or settings.amap_key
        if not self.key:
            logger.warning("AMAP_KEY not set, amap client will fail on calls")
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=10.0,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- 内部请求 ----------

    async def _get(self, path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.key:
            raise AmapError("AMAP_KEY not configured")

        params = params or {}
        params.setdefault("key", self.key)
        params.setdefault("output", "json")

        logger.debug("amap request: %s, params=%s", path, {k: v for k, v in params.items() if k != "key"})

        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as e:
            raise AmapError(f"timeout: {path}", code=50401) from e
        except httpx.HTTPError as e:
            raise AmapError(f"http error: {e}", code=50002) from e
        except json.JSONDecodeError as e:
            raise AmapError(f"invalid json: {e}", code=50003) from e

        status = data.get("status")
        info = data.get("info", "unknown")
        infocode = data.get("infocode", "0")

        if status != "1" and infocode not in ("10000", "10001"):
            # infocode 10000 = 成功；10001 = 正常结果但有 warning
            raise AmapError(f"{info} (infocode={infocode})", code=50010)

        return data

    # ==================================================================
    # 1. 地理编码 —— 地址 → 坐标
    # ==================================================================

    async def geocode(self, address: str, city: str = "") -> Optional[Dict[str, Any]]:
        """地址转坐标。返回第一个 geocode 结果，失败返回 None。

        返回示例: {
            "formatted_address": "...",
            "location": "lon,lat",
            "level": "...",
            ...
        }
        """
        params: Dict[str, Any] = {"address": address}
        if city:
            params["city"] = city
        data = await self._get("/v3/geocode/geo", params)
        geocodes = data.get("geocodes", [])
        if not geocodes:
            return None
        return geocodes[0]

    async def geocode_to_lnglat(self, address: str, city: str = "") -> Optional[Tuple[float, float]]:
        """便捷方法：地址 → (lng, lat)。"""
        result = await self.geocode(address, city)
        if not result or "location" not in result:
            return None
        try:
            lng, lat = result["location"].split(",")
            return float(lng), float(lat)
        except (ValueError, AttributeError):
            return None

    # ==================================================================
    # 2. 逆地理编码 —— 坐标 → 地址
    # ==================================================================

    async def regeocode(
        self,
        location: Tuple[float, float],  # (lng, lat)
        extensions: str = "base",  # base | all
    ) -> Dict[str, Any]:
        """坐标转地址。"""
        params = {
            "location": f"{location[0]},{location[1]}",
            "extensions": extensions,
        }
        data = await self._get("/v3/geocode/regeo", params)
        return data.get("regeocode", {})

    # ==================================================================
    # 3. 关键字搜索 POI —— /v5/place/text
    # ==================================================================

    async def place_text(
        self,
        keyword: str,
        city: str = "",
        citylimit: bool = False,
        offset: int = 20,
        page: int = 1,
        extensions: str = "base",  # base | all
        types: str = "",  # POI 类型，如 "050000"（餐饮）
        location: str = "",  # 限制中心点，格式 "lon,lat"
        radius: int = 0,  # 搜索半径（米），需配合 location
    ) -> List[Dict[str, Any]]:
        """关键字搜索 POI。返回 POI 列表。

        每个 POI 包含: id, name, type, typecode, address, location, tel,
        distance, business_area 等。
        """
        params: Dict[str, Any] = {
            "keywords": keyword,
            "offset": offset,
            "page": page,
            "extensions": extensions,
        }
        if city:
            params["city"] = city
        if citylimit:
            params["citylimit"] = "true"
        if types:
            params["types"] = types
        if location:
            params["location"] = location
        if radius > 0 and location:
            params["radius"] = radius

        data = await self._get("/v5/place/text", params)
        pois = data.get("pois", [])
        return pois if isinstance(pois, list) else []

    # ==================================================================
    # 4. 周边搜索 POI —— /v5/place/around
    # ==================================================================

    async def place_around(
        self,
        location: Tuple[float, float],  # (lng, lat)
        keyword: str = "",
        types: str = "",
        radius: int = 3000,  # 米，默认 3000，最大 50000
        offset: int = 20,
        page: int = 1,
        extensions: str = "base",
        sort: str = "distance",  # distance | weight
    ) -> List[Dict[str, Any]]:
        """周边搜索 POI。"""
        params: Dict[str, Any] = {
            "location": f"{location[0]},{location[1]}",
            "radius": radius,
            "offset": offset,
            "page": page,
            "extensions": extensions,
            "sortrule": sort,
        }
        if keyword:
            params["keywords"] = keyword
        if types:
            params["types"] = types

        data = await self._get("/v5/place/around", params)
        pois = data.get("pois", [])
        return pois if isinstance(pois, list) else []

    # ==================================================================
    # 5. POI 详情 —— /v5/place/detail
    # ==================================================================

    async def place_detail(self, poi_id: str) -> Optional[Dict[str, Any]]:
        """根据 POI ID 查询详情。"""
        params = {"id": poi_id, "extensions": "all"}
        data = await self._get("/v5/place/detail", params)
        pois = data.get("pois", [])
        if not pois or not isinstance(pois, list):
            return None
        return pois[0]

    # ==================================================================
    # 6. 驾车路径规划 —— /v5/direction/driving
    # ==================================================================

    async def direction_driving(
        self,
        origin: Tuple[float, float],       # (lng, lat)
        destination: Tuple[float, float],  # (lng, lat)
        strategy: int = 0,  # 0=速度优先 1=费用优先 2=距离优先 3=不走高速...
        waypoints: List[Tuple[float, float]] | None = None,  # 途经点（最多16个）
        show_fields: str = "polyline,tmcs,cost,navi",
        extensions: str = "all",  # base | all  （all 才返回 duration/tolls/tmcs）
    ) -> Dict[str, Any]:
        """驾车路径规划。返回 route 数据。

        包含 paths: [{ distance, duration, tolls, polyline, tmcs[], steps[] }]
        """
        params: Dict[str, Any] = {
            "origin": f"{origin[0]},{origin[1]}",
            "destination": f"{destination[0]},{destination[1]}",
            "strategy": strategy,
            "extensions": extensions,
        }
        if waypoints:
            params["waypoints"] = ";".join(f"{w[0]},{w[1]}" for w in waypoints)
        if show_fields:
            params["show_fields"] = show_fields

        data = await self._get("/v5/direction/driving", params)
        route = data.get("route", {})
        return route if isinstance(route, dict) else {}

    # ==================================================================
    # 7. 距离测量 —— /v3/distance
    # ==================================================================

    async def distance(
        self,
        origins: List[Tuple[float, float]],  # 起点列表
        destination: Tuple[float, float],     # 终点
        type_: int = 1,  # 0=直线距离 1=驾车距离 3=步行距离
    ) -> List[Dict[str, Any]]:
        """批量计算起终点距离。

        返回: [{"distance": int(米), "duration": int(秒)}, ...]
        """
        if not origins:
            return []
        origin_str = "|".join(f"{o[0]},{o[1]}" for o in origins)
        params = {
            "origins": origin_str,
            "destination": f"{destination[0]},{destination[1]}",
            "type": type_,
        }
        data = await self._get("/v3/distance", params)
        results = data.get("results", [])
        return results if isinstance(results, list) else []

    # ==================================================================
    # 8. 天气查询 —— /v3/weather/weatherInfo
    # ==================================================================

    async def weather(
        self,
        city_adcode: str,
        extensions: str = "base",  # base=实况  all=预报
    ) -> Dict[str, Any]:
        """查询天气。

        base: 返回 lives[]（当前实况）
        all:  返回 lives[] + forecasts[]（未来 3 天预报）
        """
        params = {"city": city_adcode, "extensions": extensions}
        data = await self._get("/v3/weather/weatherInfo", params)
        return data

    async def weather_live(self, city_adcode: str) -> Optional[Dict[str, Any]]:
        """获取当前天气实况（第一条）。"""
        data = await self.weather(city_adcode, extensions="base")
        lives = data.get("lives", [])
        if lives and isinstance(lives, list):
            return lives[0]
        return None

    async def weather_forecast(self, city_adcode: str) -> List[Dict[str, Any]]:
        """获取天气预报。"""
        data = await self.weather(city_adcode, extensions="all")
        forecasts = data.get("forecasts", [])
        if forecasts and isinstance(forecasts, list):
            # 第一个元素是目标城市，里面有 casts 数组
            return forecasts[0].get("casts", [])
        return []

    # ==================================================================
    # 9. 交通态势 —— /v3/traffic/status/{rectangle|circle|road}
    # ==================================================================

    async def traffic_status_rectangle(
        self,
        rectangle: str,  # "x1,y1;x2,y2" 左下右上坐标
        level: int = 5,  # 等级 1-5
        extensions: str = "base",  # base | all
    ) -> Dict[str, Any]:
        """矩形区域交通态势。"""
        params = {"rectangle": rectangle, "level": level, "extensions": extensions}
        data = await self._get("/v3/traffic/status/rectangle", params)
        return data.get("trafficinfo", {})

    async def traffic_status_circle(
        self,
        location: Tuple[float, float],
        radius: int = 1000,  # 米
        level: int = 5,
        extensions: str = "base",
    ) -> Dict[str, Any]:
        """圆形区域交通态势。"""
        params = {
            "location": f"{location[0]},{location[1]}",
            "radius": radius,
            "level": level,
            "extensions": extensions,
        }
        data = await self._get("/v3/traffic/status/circle", params)
        return data.get("trafficinfo", {})

    async def traffic_status_road(
        self,
        name: str,        # 道路名称
        adcode: str = "",  # 城市编码
        level: int = 5,
        extensions: str = "base",
    ) -> Dict[str, Any]:
        """指定道路交通态势。"""
        params = {"name": name, "level": level, "extensions": extensions}
        if adcode:
            params["adcode"] = adcode
        data = await self._get("/v3/traffic/status/road", params)
        return data.get("trafficinfo", {})


# 全局单例（延迟初始化，避免模块导入时就建连接）
_amap_client: Optional[AmapClient] = None


def get_amap() -> AmapClient:
    global _amap_client
    if _amap_client is None:
        _amap_client = AmapClient()
    return _amap_client
