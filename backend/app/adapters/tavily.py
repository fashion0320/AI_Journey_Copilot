"""Tavily Web Search API 适配器。

封装 Tavily Search REST API，提供：
- 通用 web search（返回搜索结果列表）
- POI 搜索（针对本地 POI 推荐场景的搜索 + 信息提取）
- 支持 basic / advanced 两种搜索深度

文档：https://docs.tavily.com/docs/tavily-api/rest_api
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import settings
from ..core.errors import AppError
from ..core.logging import get_logger

logger = get_logger(__name__)


class TavilyError(AppError):
    """Tavily API 错误。"""

    def __init__(self, message: str, code: int = 52001):
        super().__init__(code=code, message=f"[TAVILY] {message}")


class TavilyClient:
    """Tavily Web Search API 客户端。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        max_results: int = 0,
        search_depth: str = "",
    ) -> None:
        self.api_key = api_key or settings.tavily_api_key
        self.base_url = base_url or settings.tavily_base_url
        self.max_results = max_results or settings.tavily_max_results
        self.search_depth = search_depth or settings.tavily_search_depth

        if not self.api_key:
            logger.warning("TAVILY_API_KEY not set, tavily client will fail on calls")

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=15.0,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- 核心搜索方法 ----------

    async def search(
        self,
        query: str,
        search_depth: str = "",
        max_results: int = 0,
        include_images: bool = False,
        include_answer: bool = False,
        include_raw_content: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        topic: str = "general",  # general | news
        days: int = 0,  # news 主题时的天数范围
        include_images_descriptions: bool = False,
    ) -> Dict[str, Any]:
        """执行 Tavily 搜索。

        Args:
            query: 搜索关键词
            search_depth: basic | advanced，默认使用配置值
            max_results: 返回结果数，默认使用配置值
            include_images: 是否包含图片
            include_answer: 是否包含 AI 生成的答案
            include_raw_content: 是否包含原始页面内容
            include_domains: 只搜索指定域名
            exclude_domains: 排除指定域名
            topic: general | news
            days: news 主题时的天数范围

        Returns:
            {
                "answer": str | None,           # include_answer=true 时返回
                "query": str,                   # 原始搜索词
                "results": [                    # 搜索结果列表
                    {
                        "title": str,
                        "url": str,
                        "content": str,
                        "score": float,          # 相关度分数 0-1
                        "images": [str] | None,  # include_images=true 时
                        "published_date": str | None,
                        "raw_content": str | None,
                    },
                    ...
                ],
                "images": [str] | None,
            }
        """
        if not self.api_key:
            raise TavilyError("TAVILY_API_KEY not configured")

        depth = search_depth or self.search_depth
        num_results = max_results or self.max_results

        payload: Dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": depth,
            "max_results": num_results,
            "include_images": include_images,
            "include_answer": include_answer,
            "include_raw_content": include_raw_content,
            "include_images_descriptions": include_images_descriptions,
        }

        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains

        if topic:
            payload["topic"] = topic
        if topic == "news" and days > 0:
            payload["days"] = days

        logger.debug("tavily search: query=%s depth=%s max=%s", query, depth, num_results)

        try:
            resp = await self._client.post(
                "/search",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as e:
            raise TavilyError(f"timeout: {e}", code=52401) from e
        except httpx.HTTPStatusError as e:
            # 尝试读取错误信息
            err_msg = f"HTTP {e.response.status_code}"
            try:
                err_data = e.response.json()
                if isinstance(err_data, dict):
                    detail = err_data.get("error", err_data.get("message", ""))
                    if detail:
                        err_msg += f": {detail}"
            except (json.JSONDecodeError, ValueError):
                pass
            raise TavilyError(err_msg, code=52000 + e.response.status_code) from e
        except httpx.HTTPError as e:
            raise TavilyError(f"http error: {e}", code=52002) from e
        except json.JSONDecodeError as e:
            raise TavilyError(f"invalid json: {e}", code=52003) from e

        # 校验返回
        if not isinstance(data, dict):
            raise TavilyError("unexpected response format")

        # Tavily 错误返回
        if data.get("error"):
            raise TavilyError(str(data["error"]), code=52100)

        results = data.get("results", [])
        if not isinstance(results, list):
            results = []

        logger.debug("tavily search returned %d results", len(results))
        return data

    # ---------- 便捷方法 ----------

    async def search_poi_web(
        self,
        keyword: str,
        city: str = "",
        max_results: int = 0,
    ) -> List[Dict[str, Any]]:
        """针对本地 POI 场景的 web 搜索。

        将搜索关键词加上城市限定，返回适合 POI 召回的结果列表。
        结果格式已标准化，可直接与高德 POI 结果合并。

        Args:
            keyword: 搜索关键词（如 "静安寺附近好吃的餐厅"）
            city: 城市名（用于限定搜索范围）
            max_results: 返回数量

        Returns:
            标准化的 POI-like 结果列表，每项包含：
            - name: 名称（从搜索结果标题/内容提取）
            - url: 来源 URL
            - content: 摘要内容
            - score: 相关度分数
            - _source: "tavily"
            - published_date: 发布时间（如有）
        """
        # 构造更精准的搜索查询
        query = keyword
        if city:
            query = f"{city} {keyword}"

        data = await self.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            topic="general",
        )

        raw_results = data.get("results", [])
        normalized = []

        for item in raw_results:
            title = item.get("title", "")
            content = item.get("content", "")
            url = item.get("url", "")
            score = item.get("score", 0.0)

            # 标准化为 POI-like 格式
            normalized.append({
                "id": f"tavily_{abs(hash(url))}",
                "name": title,
                "address": "",  # web search 不直接返回地址
                "url": url,
                "content": content,
                "score": score,
                "rating": "",
                "cost": "",
                "category": "",
                "published_date": item.get("published_date", ""),
                "_source": "tavily",
                "type": "",  # 高德的 type 字段
                "typecode": "",
                "location": "",  # 无坐标信息
                "distance": "",  # 无距离信息
            })

        return normalized

    async def extract_poi_info(
        self,
        poi_name: str,
        city: str = "",
    ) -> Dict[str, Any]:
        """针对单个 POI 名称，搜索其详细信息（评分、价格、评价等）。

        用于补充高德 POI 缺失的评分/口碑信息。

        Args:
            poi_name: POI 名称
            city: 城市名

        Returns:
            提取的 POI 信息 dict
        """
        query = f"{city} {poi_name} 评分 推荐" if city else f"{poi_name} 评分 推荐"

        data = await self.search(
            query=query,
            search_depth="basic",
            max_results=5,
        )

        results = data.get("results", [])
        answer = data.get("answer", "")

        return {
            "poi_name": poi_name,
            "answer": answer,
            "sources": results[:3],
            "total_results": len(results),
        }


# 全局实例
_tavily_client: Optional[TavilyClient] = None


def get_tavily() -> TavilyClient:
    """获取 TavilyClient 单例。"""
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient()
    return _tavily_client
