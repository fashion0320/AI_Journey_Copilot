"""Tavily Web Search 测试路由（开发用，正式环境可移除）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..adapters.tavily import get_tavily
from ..core.errors import ApiResponse

router = APIRouter(prefix="/api/test/tavily", tags=["test-tavily"])


class SearchReq(BaseModel):
    query: str
    search_depth: str = "basic"
    max_results: int = 5
    include_answer: bool = False
    include_images: bool = False


@router.post("/search")
async def test_tavily_search(req: SearchReq):
    """测试 Tavily 通用搜索。"""
    tavily = get_tavily()
    if not tavily.api_key:
        return ApiResponse.error(52001, "TAVILY_API_KEY not configured")

    result = await tavily.search(
        query=req.query,
        search_depth=req.search_depth,
        max_results=req.max_results,
        include_answer=req.include_answer,
        include_images=req.include_images,
    )

    # 精简返回结果
    results = result.get("results", [])
    simplified = []
    for r in results[:req.max_results]:
        simplified.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", "")[:200],
            "score": r.get("score", 0),
            "published_date": r.get("published_date", ""),
        })

    return ApiResponse.success({
        "query": result.get("query", req.query),
        "answer": result.get("answer", ""),
        "total_results": len(results),
        "results": simplified,
    })


class POISearchReq(BaseModel):
    keyword: str
    city: str = "上海"
    max_results: int = 5


@router.post("/poi-search")
async def test_tavily_poi_search(req: POISearchReq):
    """测试 Tavily POI web 搜索。"""
    tavily = get_tavily()
    if not tavily.api_key:
        return ApiResponse.error(52001, "TAVILY_API_KEY not configured")

    results = await tavily.search_poi_web(
        keyword=req.keyword,
        city=req.city,
        max_results=req.max_results,
    )

    return ApiResponse.success({
        "keyword": req.keyword,
        "city": req.city,
        "total": len(results),
        "results": results,
    })
