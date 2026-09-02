"""高德地图 API 测试路由（开发用，正式环境可移除）。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..core.errors import ApiResponse
from ..adapters.amap import get_amap

router = APIRouter(prefix="/api/test/amap", tags=["test-amap"])


class GeocodeReq(BaseModel):
    address: str
    city: str = "上海"


@router.get("/geocode", response_model=ApiResponse)
async def test_geocode(address: str, city: str = "上海"):
    """地理编码测试：地址 → 坐标。"""
    amap = get_amap()
    result = await amap.geocode(address, city)
    return ApiResponse.success(result)


@router.get("/regeocode", response_model=ApiResponse)
async def test_regeocode(lng: float, lat: float):
    """逆地理编码测试：坐标 → 地址。"""
    amap = get_amap()
    result = await amap.regeocode((lng, lat))
    return ApiResponse.success(result)


@router.get("/place/text", response_model=ApiResponse)
async def test_place_text(
    keyword: str,
    city: str = "上海",
    offset: int = 10,
    page: int = 1,
    extensions: str = "base",
):
    """关键字 POI 搜索测试。"""
    amap = get_amap()
    result = await amap.place_text(keyword, city=city, offset=offset, page=page, extensions=extensions)
    # 简化输出
    simplified = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "address": p.get("address"),
            "location": p.get("location"),
            "tel": p.get("tel"),
            "type": p.get("type"),
            "distance": p.get("distance"),
            "business_area": p.get("business_area"),
        }
        for p in result
    ]
    return ApiResponse.success(simplified, message=f"found {len(simplified)} POIs")


@router.get("/place/around", response_model=ApiResponse)
async def test_place_around(
    lng: float,
    lat: float,
    keyword: str = "",
    radius: int = 3000,
    offset: int = 10,
):
    """周边 POI 搜索测试。"""
    amap = get_amap()
    result = await amap.place_around((lng, lat), keyword=keyword, radius=radius, offset=offset)
    simplified = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "address": p.get("address"),
            "location": p.get("location"),
            "distance": p.get("distance"),
        }
        for p in result
    ]
    return ApiResponse.success(simplified, message=f"found {len(simplified)} POIs")


@router.get("/place/detail", response_model=ApiResponse)
async def test_place_detail(poi_id: str):
    """POI 详情测试。"""
    amap = get_amap()
    result = await amap.place_detail(poi_id)
    return ApiResponse.success(result)


@router.get("/direction/driving", response_model=ApiResponse)
async def test_direction_driving(
    origin_lng: float,
    origin_lat: float,
    dest_lng: float,
    dest_lat: float,
    strategy: int = 0,
    show_fields: str = "polyline,tmcs",
):
    """驾车路径规划测试。"""
    amap = get_amap()
    route = await amap.direction_driving(
        (origin_lng, origin_lat),
        (dest_lng, dest_lat),
        strategy=strategy,
        show_fields=show_fields,
    )
    paths = route.get("paths", [])
    simplified = []
    for p in paths:
        cost = p.get("cost", {})
        # tmcs 是嵌套在 steps 里的，聚合计算段数
        steps = p.get("steps", [])
        all_tmcs = []
        for s in steps:
            all_tmcs.extend(s.get("tmcs", []))
        # 聚合 polyline（从 steps 中拼接，v5 里 path 级 polyline 需要 show_fields）
        polyline_points = sum(
            len(s.get("polyline", "").split(";")) for s in steps if s.get("polyline")
        )
        simplified.append({
            "distance_km": int(p.get("distance", 0)) / 1000,
            "duration_min": int(cost.get("duration", 0)) / 60,
            "tolls": int(cost.get("tolls", 0)) / 100 if cost.get("tolls") else 0,
            "traffic_lights": int(cost.get("traffic_lights", 0)),
            "steps_count": len(steps),
            "polyline_points": polyline_points,
            "tmcs_segments": len(all_tmcs),
        })
    return ApiResponse.success(
        {"total_paths": len(paths), "paths": simplified, "taxi_cost": route.get("taxi_cost")},
    )


@router.get("/distance", response_model=ApiResponse)
async def test_distance(
    origin_lng: float,
    origin_lat: float,
    dest_lng: float,
    dest_lat: float,
    type_: int = 1,
):
    """距离测量测试。"""
    amap = get_amap()
    result = await amap.distance([(origin_lng, origin_lat)], (dest_lng, dest_lat), type_)
    if result:
        r = result[0]
        distance_km = int(r.get("distance", 0)) / 1000
        duration_min = int(r.get("duration", 0)) / 60
        return ApiResponse.success({
            "distance_km": distance_km,
            "duration_min": duration_min,
        })
    return ApiResponse.success(None)


@router.get("/weather", response_model=ApiResponse)
async def test_weather(city_adcode: str = "310000", extensions: str = "all"):
    """天气查询测试。city_adcode 默认上海 310000。"""
    amap = get_amap()
    data = await amap.weather(city_adcode, extensions)
    return ApiResponse.success(data)


@router.get("/traffic/circle", response_model=ApiResponse)
async def test_traffic_circle(lng: float, lat: float, radius: int = 1000):
    """圆形区域交通态势测试。"""
    amap = get_amap()
    data = await amap.traffic_status_circle((lng, lat), radius)
    return ApiResponse.success(data)
