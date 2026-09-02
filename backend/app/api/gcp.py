"""GCP 控制面板 REST API。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, Body

from ..core.errors import ApiResponse, AppError
from ..core.logging import get_logger
from ..gcp import ContextStore, PRESETS, USER_PROFILES, GlobalContext, JourneyState
from ..gcp.models import (
    VehicleSignals,
    InCabinPerception,
    TimeContext,
    WeatherContext,
    TrafficContext,
    TransitContext,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/gcp", tags=["gcp"])


def _get_store() -> ContextStore:
    return ContextStore.get_instance()


@router.get("/context", response_model=ApiResponse[Dict[str, Any]])
async def get_context():
    """获取当前完整 GCP 快照。"""
    store = _get_store()
    return ApiResponse.success(store.to_dict())


@router.put("/context", response_model=ApiResponse[List[str]])
async def update_context(updates: Dict[str, Any] = Body(...)):
    """扁平字段路径更新 GCP。

    示例 body: {"vehicle.position.lat": 31.23, "weather.live.weather": "小雨"}
    """
    store = _get_store()
    changed = await store.update(updates)
    return ApiResponse.success(changed, message=f"{len(changed)} fields changed")


@router.post("/module/{module_name}", response_model=ApiResponse[List[str]])
async def update_module(module_name: str, data: Dict[str, Any] = Body(...)):
    """更新整个模块（vehicle/time/weather/traffic/transit/in_cabin）。"""
    allowed = {"vehicle", "time", "weather", "traffic", "journey", "transit", "in_cabin", "user_profile"}
    if module_name not in allowed:
        raise AppError(40001, f"unknown module: {module_name}, allowed: {allowed}")
    store = _get_store()
    changed = await store.update_module(module_name, data)
    return ApiResponse.success(changed, message=f"{len(changed)} fields changed")


@router.get("/presets", response_model=ApiResponse[Dict[str, str]])
async def list_presets():
    """列出可用预设场景。"""
    return ApiResponse.success({
        name: p.get("description", name) for name, p in PRESETS.items()
    })


@router.post("/presets/{preset_name}/load", response_model=ApiResponse[str])
async def load_preset(preset_name: str):
    """加载预设场景（Amy 接机 / Claire 聚会）。"""
    if preset_name not in PRESETS:
        raise AppError(40401, f"preset not found: {preset_name}")

    preset = PRESETS[preset_name]
    store = _get_store()

    async with store._lock:
        from ..gcp.models import (
            VehicleSignals, InCabinPerception, TimeContext, WeatherContext,
            TrafficContext, TransitContext, JourneyState, JourneyStatus,
        )

        ctx = store._ctx

        # 设置用户画像
        profile_key = preset.get("user_profile", "")
        if profile_key and profile_key in USER_PROFILES:
            ctx.user_profile = USER_PROFILES[profile_key]

        # 加载各个模块（如果预设中有）
        if "vehicle" in preset:
            ctx.vehicle = preset["vehicle"]
        if "in_cabin" in preset:
            ctx.in_cabin = preset["in_cabin"]
        if "time" in preset:
            ctx.time = preset["time"]
        if "weather" in preset:
            ctx.weather = preset["weather"]
        if "transit" in preset:
            ctx.transit = preset["transit"]
        ctx.traffic = TrafficContext()  # 重置交通
        ctx.journey = JourneyState()  # 重置旅程

    # 通知变更（直接广播快照，update({}) 空字典会跳过推送）
    event = {"type": "gcp_update", "fields": ["preset"], "snapshot": store.to_dict()}
    dead_queues = set()
    for q in store._event_queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
        except Exception:
            dead_queues.add(q)
    for q in dead_queues:
        store._event_queues.discard(q)
    logger.info("loaded preset: %s", preset_name)
    return ApiResponse.success(preset_name, message=f"preset {preset_name} loaded")


@router.get("/profiles", response_model=ApiResponse[Dict[str, Any]])
async def list_profiles():
    """列出可用用户画像。"""
    return ApiResponse.success({
        k: {"id": v.id, "name": v.name, "age": v.age, "occupation": v.occupation}
        for k, v in USER_PROFILES.items()
    })


@router.post("/profiles/{profile_key}/load", response_model=ApiResponse[str])
async def load_profile(profile_key: str):
    """切换用户画像。"""
    if profile_key not in USER_PROFILES:
        raise AppError(40401, f"profile not found: {profile_key}")
    store = _get_store()
    async with store._lock:
        store._ctx.user_profile = USER_PROFILES[profile_key]

    # 广播变更
    event = {"type": "gcp_update", "fields": ["user_profile"], "snapshot": store.to_dict()}
    dead_queues = set()
    for q in store._event_queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
        except Exception:
            dead_queues.add(q)
    for q in dead_queues:
        store._event_queues.discard(q)
    logger.info("loaded profile: %s", profile_key)
    return ApiResponse.success(profile_key, message=f"profile {profile_key} loaded")
