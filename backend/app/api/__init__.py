"""API 包。"""

from fastapi import APIRouter
from ..core.config import settings
from .health import router as health_router
from .gcp import router as gcp_router
from .ws import router as ws_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(gcp_router)
api_router.include_router(ws_router)

# 测试路由：生产环境默认关闭，通过 ENABLE_TEST_ROUTERS=1 启用
if settings.enable_test_routers:
    from .amap_test import router as amap_test_router
    from .voice_test import router as voice_test_router
    from .claude_test import router as claude_test_router
    from .tavily_test import router as tavily_test_router
    from .skill_test import router as skill_test_router

    api_router.include_router(amap_test_router)
    api_router.include_router(voice_test_router)
    api_router.include_router(claude_test_router)
    api_router.include_router(tavily_test_router)
    api_router.include_router(skill_test_router)

__all__ = ["api_router"]
