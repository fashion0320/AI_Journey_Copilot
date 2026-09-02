"""健康检查。"""

from fastapi import APIRouter
from ..core.errors import ApiResponse

router = APIRouter()


@router.get("/health", response_model=ApiResponse)
async def health():
    return ApiResponse.success({"status": "ok"})
