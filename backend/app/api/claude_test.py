"""Claude API 测试路由（开发用，正式环境可移除）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..adapters.claude import (
    ClaudeEventType,
    ConversationManager,
    build_web_search_tool,
    get_claude,
)
from ..adapters.prompts import build_copilot_system_prompt
from ..core.config import settings
from ..core.errors import ApiResponse

router = APIRouter(prefix="/api/test/claude", tags=["test-claude"])


class ChatReq(BaseModel):
    message: str
    history: Optional[List[Dict[str, Any]]] = None
    use_web_search: bool = False
    system_prompt: Optional[str] = None


@router.post("/chat")
async def test_chat(req: ChatReq):
    """简单文本对话测试（非流式）。"""
    claude = get_claude()
    messages = list(req.history or [])
    messages.append({"role": "user", "content": req.message})

    tools = None
    if req.use_web_search:
        tools = [build_web_search_tool(
            allowed_domains=settings.web_search_allowed_list or None,
            blocked_domains=settings.web_search_blocked_list or None,
            max_uses=settings.web_search_max_uses,
        )]

    system = req.system_prompt or build_copilot_system_prompt()

    try:
        resp = await claude.chat(messages, system=system, tools=tools)
        # 提取文本内容
        text_parts: List[str] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)

        # 安全地提取 usage
        usage = {}
        if resp.usage:
            for attr in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                         "cache_creation_input_tokens", "search_tokens"):
                val = getattr(resp.usage, attr, None)
                if val is not None:
                    usage[attr] = int(val)

        result = {
            "content": "\n".join(text_parts),
            "model": resp.model,
            "stop_reason": resp.stop_reason,
            "usage": usage,
        }
        return ApiResponse.success(result)
    except Exception as e:
        return ApiResponse.error(code=60001, message=str(e))


@router.post("/chat/stream")
async def test_chat_stream(req: ChatReq):
    """流式对话测试（SSE）。"""
    claude = get_claude()
    messages = list(req.history or [])
    messages.append({"role": "user", "content": req.message})

    tools = None
    if req.use_web_search:
        tools = [build_web_search_tool(
            allowed_domains=settings.web_search_allowed_list or None,
            blocked_domains=settings.web_search_blocked_list or None,
            max_uses=settings.web_search_max_uses,
        )]

    system = req.system_prompt or build_copilot_system_prompt()

    async def event_generator():
        async for evt in claude.chat_stream(messages, system=system, tools=tools):
            data = evt.model_dump(mode="json")
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


class WebSearchReq(BaseModel):
    query: str


@router.post("/web-search")
async def test_web_search(req: WebSearchReq):
    """测试 Claude 内置 web_search 工具。"""
    claude = get_claude()
    messages = [{"role": "user", "content": req.query}]
    tools = [build_web_search_tool(
        allowed_domains=settings.web_search_allowed_list or None,
        blocked_domains=settings.web_search_blocked_list or None,
        max_uses=settings.web_search_max_uses,
    )]
    system = "你是一个搜索助手，请使用 web_search 工具搜索相关信息，然后总结回答用户的问题。"

    try:
        resp = await claude.chat(messages, system=system, tools=tools)
        text_parts: List[str] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)

        usage = {}
        if resp.usage:
            for attr in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                         "cache_creation_input_tokens", "search_tokens"):
                val = getattr(resp.usage, attr, None)
                if val is not None:
                    usage[attr] = int(val)

        result = {
            "answer": "\n".join(text_parts),
            "model": resp.model,
            "stop_reason": resp.stop_reason,
            "usage": usage,
        }
        return ApiResponse.success(result)
    except Exception as e:
        return ApiResponse.error(code=60001, message=str(e))
