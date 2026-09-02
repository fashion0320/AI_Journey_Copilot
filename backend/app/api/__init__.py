"""API 包。"""

from fastapi import APIRouter
from .health import router as health_router
from .gcp import router as gcp_router
from .ws import router as ws_router
from .amap_test import router as amap_test_router
from .voice_test import router as voice_test_router
from .claude_test import router as claude_test_router
from .tavily_test import router as tavily_test_router
from .skill_test import router as skill_test_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(gcp_router)
api_router.include_router(ws_router)
api_router.include_router(amap_test_router)  # 开发测试用，正式环境可移除
api_router.include_router(voice_test_router)  # 开发测试用，正式环境可移除
api_router.include_router(claude_test_router)  # 开发测试用，正式环境可移除
api_router.include_router(tavily_test_router)  # 开发测试用，正式环境可移除
api_router.include_router(skill_test_router)  # 开发测试用，正式环境可移除

__all__ = ["api_router"]
