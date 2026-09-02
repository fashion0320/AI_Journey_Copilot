"""Claude LLM + Web Search 适配器。

封装 Anthropic SDK，提供：
- 流式对话（chat_stream）
- 自动 tool-use loop（自定义工具 + 内置 web_search）
- 多轮对话管理（ConversationManager）
"""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import anthropic
from anthropic.types import (
    Message,
    TextBlock,
    ToolUseBlock,
)
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.errors import AppError
from ..core.logging import get_logger

logger = get_logger(__name__)


# ==================== 事件类型 ====================

class ClaudeEventType(str, Enum):
    MESSAGE_START = "message_start"
    TEXT_START = "text_start"
    TEXT_DELTA = "text_delta"
    TEXT_STOP = "text_stop"
    TOOL_USE_START = "tool_use_start"
    TOOL_USE_INPUT = "tool_use_input"
    TOOL_USE_STOP = "tool_use_stop"
    MESSAGE_STOP = "message_stop"
    ERROR = "error"


class ClaudeEvent(BaseModel):
    type: ClaudeEventType
    index: int = 0
    text: str = ""
    tool_id: str = ""
    tool_name: str = ""
    tool_input: Dict[str, Any] = Field(default_factory=dict)
    stop_reason: str = ""
    error_message: str = ""
    usage: Dict[str, int] = Field(default_factory=dict)


class ClaudeError(AppError):
    """Claude API 错误。"""

    def __init__(self, message: str, code: int = 60001):
        super().__init__(code=code, message=f"[CLAUDE] {message}")


# ==================== 工具定义 ====================

def build_web_search_tool(
    allowed_domains: Optional[List[str]] = None,
    blocked_domains: Optional[List[str]] = None,
    max_uses: Optional[int] = None,
) -> Dict[str, Any]:
    """构造 Claude 内置 web_search 工具定义（dict 格式）。"""
    tool: Dict[str, Any] = {
        "type": "web_search_20250305",
        "name": "web_search",
    }
    if max_uses is not None:
        tool["max_uses"] = max_uses
    if allowed_domains:
        tool["allowed_domains"] = allowed_domains
    if blocked_domains:
        tool["blocked_domains"] = blocked_domains
    return tool


# ==================== ClaudeClient ====================

class ClaudeClient:
    """Claude 流式对话客户端。

    支持：
    - 纯文本流式对话
    - 自定义 tool-use（自动 loop 执行 handler）
    - 内置 web_search 工具（通过 beta header）
    """

    # web_search beta header
    WEB_SEARCH_BETA = "web-search-2025-03-05"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        max_tokens: int = 0,
        temperature: float = -1.0,
    ) -> None:
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.claude_model
        self.max_tokens = max_tokens or settings.claude_max_tokens
        self.temperature = temperature if temperature >= 0 else settings.claude_temperature

        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY not set, claude client will fail on calls")

        self._client = anthropic.AsyncAnthropic(
            api_key=self.api_key or "sk-placeholder",
        )

    async def close(self) -> None:
        await self._client.close()

    # ---------- 核心流式方法 ----------

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        system: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_handlers: Optional[Dict[str, Callable[..., Any]]] = None,
        max_tool_loops: int = 5,
    ) -> Any:
        """流式对话生成器，自动处理 tool-use loop。

        Yields ClaudeEvent 事件序列。

        Args:
            messages: 多轮消息列表，格式如 [{"role": "user", "content": "..."}, ...]
            system: system prompt
            tools: 工具定义列表（支持自定义 tool 和 web_search）
            tool_handlers: 自定义工具名 → 处理函数的映射（参数为 tool_input dict）
            max_tool_loops: 最大 tool-use 循环次数，防止死循环
        """
        if not self.api_key:
            yield ClaudeEvent(
                type=ClaudeEventType.ERROR,
                error_message="ANTHROPIC_API_KEY not configured",
            )
            return

        tool_handlers = tool_handlers or {}
        current_messages = list(messages)  # 本地拷贝，tool-use loop 会追加消息
        loop_count = 0

        while loop_count < max_tool_loops:
            loop_count += 1
            try:
                final_message, events = await self._stream_once(
                    current_messages, system, tools
                )
                for evt in events:
                    yield evt

                if final_message is None:
                    return

                # 将 assistant 消息追加到历史
                assistant_blocks: List[Dict[str, Any]] = []
                for block in final_message.content:
                    if isinstance(block, TextBlock):
                        assistant_blocks.append({
                            "type": "text",
                            "text": block.text,
                        })
                    elif isinstance(block, ToolUseBlock):
                        assistant_blocks.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                    else:
                        # 其他类型（web_search_result 等），跳过
                        pass

                current_messages.append({
                    "role": "assistant",
                    "content": assistant_blocks,
                })

                # 判断是否需要 tool-use loop
                if final_message.stop_reason != "tool_use":
                    # end_turn / max_tokens 等，结束
                    return

                # 收集 tool_use blocks 并执行 handler
                tool_use_blocks = [
                    b for b in final_message.content
                    if isinstance(b, ToolUseBlock)
                ]
                if not tool_use_blocks:
                    return

                tool_result_blocks: List[Dict[str, Any]] = []

                for block in tool_use_blocks:
                    handler = tool_handlers.get(block.name)
                    if handler is None:
                        result_content = f"Error: tool '{block.name}' not found"
                    else:
                        try:
                            result = await self._call_handler(handler, block.input)
                            if isinstance(result, str):
                                result_content = result
                            else:
                                result_content = json.dumps(result, ensure_ascii=False)
                        except Exception as e:
                            logger.error("tool handler %s error: %s", block.name, e)
                            result_content = f"Error: {str(e)}"

                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_content,
                    })

                # 追加 tool_result 消息，进入下一轮循环
                current_messages.append({
                    "role": "user",
                    "content": tool_result_blocks,
                })

            except anthropic.RateLimitError as e:
                yield ClaudeEvent(
                    type=ClaudeEventType.ERROR,
                    error_message=f"rate limit exceeded: {e}",
                )
                return
            except anthropic.APIConnectionError as e:
                yield ClaudeEvent(
                    type=ClaudeEventType.ERROR,
                    error_message=f"connection error: {e}",
                )
                return
            except anthropic.APIError as e:
                yield ClaudeEvent(
                    type=ClaudeEventType.ERROR,
                    error_message=f"api error: {e}",
                )
                return
            except Exception as e:
                logger.exception("chat_stream error: %s", e)
                yield ClaudeEvent(
                    type=ClaudeEventType.ERROR,
                    error_message=str(e),
                )
                return

    async def _stream_once(
        self,
        messages: List[Dict[str, Any]],
        system: str,
        tools: Optional[List[Dict[str, Any]]],
    ) -> tuple[Optional[Message], List[ClaudeEvent]]:
        """执行单次流式调用，返回最终消息和事件列表。

        使用 beta.messages.stream 以支持 web_search 工具。
        """
        events: List[ClaudeEvent] = []
        final_message: Optional[Message] = None

        # 分离 web_search 和自定义 tools
        custom_tools: List[Dict[str, Any]] = []
        has_web_search = False

        if tools:
            for t in tools:
                if t.get("type", "").startswith("web_search"):
                    has_web_search = True
                else:
                    custom_tools.append(t)

        # 构造调用参数
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if custom_tools:
            kwargs["tools"] = custom_tools

        # 使用 beta API（web_search 需要 beta header）
        # 如果有 web_search，通过 betas 参数传入
        if has_web_search:
            kwargs["betas"] = [self.WEB_SEARCH_BETA]
            # web_search 工具也要放到 tools 里
            all_tools = list(custom_tools)
            for t in tools or []:
                if t.get("type", "").startswith("web_search"):
                    all_tools.append(t)
            if all_tools:
                kwargs["tools"] = all_tools

        try:
            content_block_index = 0
            current_tool_name = ""
            current_tool_id = ""
            accumulated_input_json: Dict[int, str] = {}  # index -> partial json string

            async with self._client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", "")

                    if event_type == "message_start":
                        msg = getattr(event, "message", None)
                        usage = {}
                        if msg and hasattr(msg, "usage"):
                            usage = self._usage_to_dict(msg.usage)
                        events.append(ClaudeEvent(
                            type=ClaudeEventType.MESSAGE_START,
                            usage=usage,
                        ))

                    elif event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        idx = getattr(event, "index", 0)
                        content_block_index = idx

                        block_type = getattr(block, "type", "") if block else ""

                        if block_type == "text":
                            events.append(ClaudeEvent(
                                type=ClaudeEventType.TEXT_START,
                                index=idx,
                            ))
                        elif block_type == "tool_use":
                            current_tool_name = getattr(block, "name", "") or ""
                            current_tool_id = getattr(block, "id", "") or ""
                            accumulated_input_json[idx] = ""
                            events.append(ClaudeEvent(
                                type=ClaudeEventType.TOOL_USE_START,
                                index=idx,
                                tool_id=current_tool_id,
                                tool_name=current_tool_name,
                            ))
                        elif block_type in ("web_search_result", "web_search"):
                            # web_search 结果块，当作 text 开始（搜索过程中产生）
                            events.append(ClaudeEvent(
                                type=ClaudeEventType.TEXT_START,
                                index=idx,
                            ))
                        # 其他类型忽略

                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue

                        delta_type = getattr(delta, "type", "")
                        idx = getattr(event, "index", content_block_index)

                        if delta_type == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            events.append(ClaudeEvent(
                                type=ClaudeEventType.TEXT_DELTA,
                                index=idx,
                                text=text,
                            ))
                        elif delta_type == "input_json_delta":
                            partial = getattr(delta, "partial_json", "") or ""
                            if idx in accumulated_input_json:
                                accumulated_input_json[idx] += partial
                            events.append(ClaudeEvent(
                                type=ClaudeEventType.TOOL_USE_INPUT,
                                index=idx,
                                tool_id=current_tool_id,
                                tool_name=current_tool_name,
                                tool_input={"_delta": partial},
                            ))

                    elif event_type == "content_block_stop":
                        idx = getattr(event, "index", content_block_index)
                        block = getattr(event, "content_block", None)
                        block_type = getattr(block, "type", "") if block else ""

                        if block_type == "text":
                            events.append(ClaudeEvent(
                                type=ClaudeEventType.TEXT_STOP,
                                index=idx,
                            ))
                        elif block_type == "tool_use":
                            tool_id = getattr(block, "id", "") or current_tool_id
                            tool_name = getattr(block, "name", "") or current_tool_name
                            tool_input = getattr(block, "input", {}) or {}
                            events.append(ClaudeEvent(
                                type=ClaudeEventType.TOOL_USE_STOP,
                                index=idx,
                                tool_id=tool_id,
                                tool_name=tool_name,
                                tool_input=tool_input,
                            ))
                            # 清理累积
                            accumulated_input_json.pop(idx, None)

                    elif event_type == "message_delta":
                        # 增量 usage 等，暂不处理
                        pass

                    elif event_type == "message_stop":
                        pass  # 下面从 final_message 读取

                # 获取最终完整消息
                final_message = await stream.get_final_message()

        except Exception as e:
            logger.error("stream error: %s", e)
            events.append(ClaudeEvent(
                type=ClaudeEventType.ERROR,
                error_message=str(e),
            ))
            return None, events

        # 追加 message_stop 事件
        if final_message is not None:
            usage = self._usage_to_dict(final_message.usage) if final_message.usage else {}
            events.append(ClaudeEvent(
                type=ClaudeEventType.MESSAGE_STOP,
                stop_reason=final_message.stop_reason or "",
                usage=usage,
            ))

        return final_message, events

    @staticmethod
    def _usage_to_dict(usage: Any) -> Dict[str, int]:
        """将 usage 对象转为 dict。"""
        result: Dict[str, int] = {}
        for attr in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                      "cache_creation_input_tokens", "search_tokens"):
            val = getattr(usage, attr, None)
            if val is not None:
                result[attr] = int(val)
        return result

    async def _call_handler(self, handler: Callable[..., Any], tool_input: Any) -> Any:
        """调用工具 handler，支持 sync 和 async。"""
        if asyncio.iscoroutinefunction(handler):
            return await handler(tool_input)
        else:
            return handler(tool_input)

    # ---------- 便捷方法 ----------

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        system: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_handlers: Optional[Dict[str, Callable[..., Any]]] = None,
    ) -> Optional[Message]:
        """非流式对话，返回完整 Message。

        复用流式逻辑，收集完整最终 message。
        """
        if not self.api_key:
            raise ClaudeError("ANTHROPIC_API_KEY not configured")

        final_message: Optional[Message] = None
        async for evt in self.chat_stream(messages, system, tools, tool_handlers):
            if evt.type == ClaudeEventType.ERROR:
                raise ClaudeError(evt.error_message)
            # 不在这里收集，chat_stream generator 会走完整个 loop

        # chat_stream 是生成器，需要完整迭代才能获取最终结果
        # 但我们没法直接从 generator 拿到 final_message
        # 所以这里直接调用非流式 API

        # 分离 web_search 和自定义 tools
        custom_tools: List[Dict[str, Any]] = []
        has_web_search = False

        if tools:
            for t in tools:
                if t.get("type", "").startswith("web_search"):
                    has_web_search = True
                else:
                    custom_tools.append(t)

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        if has_web_search:
            kwargs["betas"] = [self.WEB_SEARCH_BETA]
            all_tools = list(custom_tools)
            for t in tools or []:
                if t.get("type", "").startswith("web_search"):
                    all_tools.append(t)
            if all_tools:
                kwargs["tools"] = all_tools
        elif custom_tools:
            kwargs["tools"] = custom_tools

        resp = await self._client.beta.messages.create(**kwargs)
        return resp


# ==================== ConversationManager ====================

class ConversationManager:
    """多轮对话消息管理器（内存存储，MVP 阶段）。"""

    def __init__(self, max_history: int = 20) -> None:
        self._messages: List[Dict[str, Any]] = []
        self._max_history = max_history

    def add_user(self, text: str) -> None:
        """添加用户消息。"""
        self._messages.append({"role": "user", "content": text})
        self._trim()

    def add_user_blocks(self, blocks: List[Dict[str, Any]]) -> None:
        """添加用户多 content-block 消息（如 tool_results）。"""
        self._messages.append({"role": "user", "content": blocks})
        self._trim()

    def add_assistant(self, content: Any) -> None:
        """添加助手消息。content 可以是字符串或 block 列表。"""
        if isinstance(content, str):
            self._messages.append({"role": "assistant", "content": content})
        else:
            self._messages.append({"role": "assistant", "content": content})
        self._trim()

    def add_tool_result(self, tool_use_id: str, content: str) -> None:
        """添加 tool_result（作为用户消息）。"""
        self._messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
            }],
        })
        self._trim()

    def get_messages(self) -> List[Dict[str, Any]]:
        """返回消息列表（浅拷贝）。"""
        return list(self._messages)

    def reset(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

    def _trim(self) -> None:
        """消息过长时，保留最早一条 + 最近几条。

        MVP 阶段简单按条数截断，后续可替换为 token 计数。
        """
        max_msgs = self._max_history * 2  # user + assistant 配对
        if len(self._messages) <= max_msgs:
            return

        # 保留第一条消息作为锚点，然后截断前面的完整轮次
        keep_first = 1
        excess = len(self._messages) - max_msgs
        # 确保移除偶数条（完整对话轮次）
        if excess % 2 != 0:
            excess += 1
        if excess > 0 and len(self._messages) > keep_first + excess:
            self._messages = (
                self._messages[:keep_first]
                + self._messages[keep_first + excess:]
            )


# ==================== 全局单例 ====================

_claude_client: Optional[ClaudeClient] = None


def get_claude() -> ClaudeClient:
    global _claude_client
    if _claude_client is None:
        _claude_client = ClaudeClient()
    return _claude_client
