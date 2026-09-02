"""Skills 测试路由（开发用，正式环境可移除）。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.errors import ApiResponse
from ..skills import (
    get_local_poi,
    get_parking_find,
    get_dynamic_eta,
    get_route_master,
    get_smart_remind,
)

router = APIRouter(prefix="/api/test/skills", tags=["test-skills"])


# ==================== Route Master ====================

class RouteSingleReq(BaseModel):
    origin_lat: float
    origin_lon: float
    origin_name: str = "起点"
    dest_lat: float
    dest_lon: float
    dest_name: str = "目的地"
    strategy: str = "time_first"


@router.post("/route/single")
async def test_route_single(req: RouteSingleReq):
    """测试单目的地路线规划。"""
    skill = get_route_master()
    params = {
        "action": "route_single",
        "origin": {"lat": req.origin_lat, "lon": req.origin_lon, "name": req.origin_name},
        "destination": {"lat": req.dest_lat, "lon": req.dest_lon, "name": req.dest_name},
        "strategy": req.strategy,
    }
    result = await skill.execute(params, {})
    return ApiResponse.success(result.to_dict())


# ==================== Dynamic ETA ====================

class EtaQueryReq(BaseModel):
    current_lat: float
    current_lon: float
    dest_lat: float
    dest_lon: float
    dest_name: str = "目的地"


@router.post("/eta/query")
async def test_eta_query(req: EtaQueryReq):
    """测试 ETA 查询。"""
    skill = get_dynamic_eta()
    params = {
        "action": "eta_query",
        "current_position": {"lat": req.current_lat, "lon": req.current_lon},
        "destination": {"lat": req.dest_lat, "lon": req.dest_lon, "name": req.dest_name},
    }
    result = await skill.execute(params, {})
    return ApiResponse.success(result.to_dict())


# ==================== Smart Remind ====================

class RemindWeatherReq(BaseModel):
    weather: str = "小雨"
    temperature: float = 20.0
    windpower: str = "3"
    is_driving: bool = True


@router.post("/remind/weather")
async def test_remind_weather(req: RemindWeatherReq):
    """测试天气提醒。"""
    skill = get_smart_remind()
    params = {
        "remind_type": "weather",
        "weather": {
            "weather": req.weather,
            "temperature": req.temperature,
            "windpower": req.windpower,
        },
        "is_driving": req.is_driving,
    }
    result = await skill.execute(params, {})
    return ApiResponse.success(result.to_dict())


class RemindDepartureReq(BaseModel):
    destination: str = "虹桥机场"
    departure_time: str = "14:30"
    items: list[str] = []


@router.post("/remind/pre-departure")
async def test_remind_pre_departure(req: RemindDepartureReq):
    """测试出发前提醒。"""
    skill = get_smart_remind()
    params = {
        "remind_type": "pre_departure",
        "destination": req.destination,
        "departure_time": req.departure_time,
        "items_to_bring": req.items,
    }
    result = await skill.execute(params, {})
    return ApiResponse.success(result.to_dict())


# ==================== Local POI ====================

class POIRecommendReq(BaseModel):
    intent: str = "dining"
    keyword: str = ""
    center_lat: float
    center_lon: float
    radius_km: float = 3.0
    limit: int = 3
    city: str = "上海"


@router.post("/poi/recommend")
async def test_poi_recommend(req: POIRecommendReq):
    """测试 POI 推荐。"""
    skill = get_local_poi()
    params = {
        "action": "poi_recommend",
        "intent": req.intent,
        "keyword": req.keyword,
        "center_position": {"lat": req.center_lat, "lon": req.center_lon},
        "radius_km": req.radius_km,
        "limit": req.limit,
        "city": req.city,
    }
    result = await skill.execute(params, {})
    return ApiResponse.success(result.to_dict())


# ==================== Parking Find ====================

class ParkingSearchReq(BaseModel):
    dest_lat: float
    dest_lon: float
    dest_name: str = "目的地"
    radius_m: int = 500
    limit: int = 5
    preference: str = "convenience"


@router.post("/parking/search")
async def test_parking_search(req: ParkingSearchReq):
    """测试停车场搜索。"""
    skill = get_parking_find()
    params = {
        "action": "parking_search",
        "destination_position": {"lat": req.dest_lat, "lon": req.dest_lon},
        "destination_name": req.dest_name,
        "radius_m": req.radius_m,
        "user_preference": req.preference,
        "limit": req.limit,
    }
    result = await skill.execute(params, {})
    return ApiResponse.success(result.to_dict())


class ParkingHubReq(BaseModel):
    hub_name: str = "虹桥T2"
    dest_lat: float = 31.1964
    dest_lon: float = 121.3363


@router.post("/parking/transit-hub")
async def test_parking_transit_hub(req: ParkingHubReq):
    """测试交通枢纽停车场。"""
    skill = get_parking_find()
    params = {
        "action": "parking_transit_hub",
        "destination_position": {"lat": req.dest_lat, "lon": req.dest_lon},
        "hub_name": req.hub_name,
    }
    result = await skill.execute(params, {})
    return ApiResponse.success(result.to_dict())
