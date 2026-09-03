"""AI Journey Copilot —— 后端服务入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core import settings, setup_logging, register_exception_handlers
from .api import api_router
from .gcp import get_store, PRESETS, USER_PROFILES

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- 启动时初始化 GCP ----
    store = get_store()

    # 默认加载 Amy 接机场景
    default_preset = PRESETS["amy_airport_pickup"]
    from .gcp.models import (
        VehicleSignals, InCabinPerception, TimeContext, WeatherContext,
        TrafficContext, TransitContext, JourneyState,
    )
    ctx = store._ctx
    ctx.user_profile = USER_PROFILES[default_preset["user_profile"]]
    ctx.vehicle = default_preset["vehicle"]
    ctx.in_cabin = default_preset["in_cabin"]
    ctx.time = default_preset["time"]
    ctx.weather = default_preset["weather"]
    ctx.traffic = TrafficContext()
    ctx.transit = default_preset["transit"]
    ctx.journey = JourneyState()

    # 初始化事件队列（让 /ws/gcp 可以连接）
    store.get_event_queue()

    from .core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("AI Journey Copilot backend started — default preset: amy_airport_pickup")

    yield

    # ---- 关闭时清理 ----
    logger.info("AI Journey Copilot backend shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Journey Copilot",
        description="座舱 AI Journey Copilot MVP —— Agent + Skills + GCP",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # 路由
    app.include_router(api_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    # 开发环境开启 reload，生产环境（gunicorn 等）直接用 app 变量
    uvicorn.run(
        "app.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=settings.debug,
        ws_ping_interval=None,  # WebSocket 不发 ping，避免干扰
    )
