# Sprint 2 收尾：Claude Adapter 实现计划

## Context

Sprint 1（项目骨架 + GCP 数据层）和 Sprint 2 中的高德地图 Adapter、豆包语音 ASR/TTS 已完成。Sprint 2 还剩 **Claude + Web Search Adapter** 未实现。该 Adapter 是后续 Sprint 3（Skills）、Sprint 4（LangGraph Agent/Orchestrator）的核心依赖——Skills 通过 tool-use 被 Claude 调用，Agent 通过 Claude 做意图识别/方案推荐/任务拆解，所有 LLM 交互都经由此 Adapter。

## 当前状态

- `anthropic==0.34.2`、`langgraph==0.2.39` 已在 `requirements.txt` 中，`ANTHROPIC_API_KEY`/`CLAUDE_MODEL` 已在 config 中。
- `app/adapters/` 下只有 `amap.py`、`volc_asr.py`、`volc_tts.py`，**无 claude adapter**。
- `/ws/chat`（`app/api/ws.py`）仍是 echo 占位，TEXT_INPUT 仅回显占位文本。
- WebSocket 消息类型在 `app/core/websocket.py` 已定义好（TOKEN_STREAM、MESSAGE、SKILL_START、SKILL_RESULT、ERROR 等）。
- 现有 adapter 模式：每个 adapter 一个 `*Error(AppError)` + async client 类 + `get_logger` + settings 读取。

## 实施方案

### 1. 更新配置 `app/core/config.py`

新增 Claude Adapter 相关配置项：
- `claude_max_tokens: int = 4096` — 单次响应最大 token 数
- `claude_temperature: float = 0.7` — 默认温度
- `web_search_allowed_domains: str = ""` — 逗号分隔的允许域名（空=不限制）
- `web_search_blocked_domains: str = ""` — 逗号分隔的屏蔽域名
- `web_search_max_uses: int = 5` — 单次对话最大搜索次数

同时修正 `.env.example` 中 `VOLCENGINE_TTS_ENDPOINT` 与 `config.py` 中 `volcengine_tts_uri` 的不一致（保持 adapter 当前 HTTP v1 实现不变，在 `.env.example` 中添加注释说明）。

### 2. 创建 `app/adapters/claude.py` — Claude LLM + Web Search 适配器

#### 2.1 事件模型

定义 `ClaudeEventType` 枚举和 `ClaudeEvent` Pydantic model：

```
ClaudeEventType:
  TEXT_DELTA          # 流式文本片段
  TEXT_START          # 文本块开始
  TEXT_STOP           # 文本块结束
  TOOL_USE_START      # 工具调用开始
  TOOL_USE_INPUT      # 工具调用参数（流式 JSON 片段）
  TOOL_USE_STOP       # 工具调用参数收集完毕
  MESSAGE_START
  MESSAGE_STOP        # 整轮消息结束（含 stop_reason）
  ERROR
```

#### 2.2 ClaudeClient 类

**初始化与配置**：
- 接收 `api_key`、`model`、`max_tokens`、`temperature` 参数（默认从 settings 取）
- 内部持有 `anthropic.AsyncAnthropic` 实例
- 懒加载单例 `get_claude()` 模式（与 amap 一致）

**核心方法**：

`async def chat_stream(messages, system="", tools=None, tool_handlers=None) -> AsyncGenerator[ClaudeEvent, None]`
- 核心流式对话方法
- `messages`: 多轮消息列表 `[{"role": "user"/"assistant", "content": ...}]`
- `system`: system prompt 字符串
- `tools`: 工具定义列表（遵循 Anthropic tool-use schema），用于 Sprint 3+ 注册 Skill tools
- `tool_handlers`: Dict[str, Callable] — 工具名→处理函数的映射。当 Claude 返回 tool_use 时，client 自动调用对应 handler 获取结果，然后将 tool_result 发回 Claude 继续生成（自动 tool-use loop）
- 生成器依次 yield 事件
- 使用 `async with client.messages.stream(...)` 处理流式响应

**web_search 工具封装**：
- 提供便捷方法，构造 Anthropic 内置 web_search 工具定义
- 将 allowed_domains / blocked_domains / max_uses 配置传入
- 内置工具由 Claude 自己处理搜索并返回结果，不需要本地 handler

**ConversationManager 辅助类**：
- 管理一个对话 session 的 messages 列表
- `add_user(text)` / `add_assistant(blocks)` / `add_tool_result(tool_use_id, content)`
- `get_messages()` 返回可直接传给 Claude 的消息列表
- `reset()` 清空
- MVP 阶段内存存储，session_id 由 WebSocket 连接标识

**错误处理**：
- `ClaudeError(AppError)` 统一异常
- 捕获 `anthropic.APIError`、`RateLimitError`、`APIConnectionError` 等，转换为 ClaudeError

#### 2.3 实现细节要点

- **自动 tool-use loop**：当 stop_reason 为 `tool_use` 时，收集所有 tool_use blocks，执行对应 handler（如果有），将结果 append 到 messages，重新调用 API 继续生成，直到 stop_reason 为 `end_turn`。
- **流式事件转换**：使用 `anthropic` SDK 的 `messages.stream(...)` async context manager，监听 `text`、`input_json`、`content_block_start`、`content_block_stop`、`message_stop` 等事件，映射为 ClaudeEvent。
- **SDK 版本**：如果 anthropic 0.34.2 不支持 web_search，则升级到支持的版本（更新 requirements.txt）。

### 3. 系统提示词模板 `app/adapters/prompts.py`

- `SYSTEM_PROMPT_COPILOT`：座舱助手基础角色 prompt（中文，AI Journey Copilot 定位，简洁专业、有温度，适合驾驶场景）
- 模板预留 `{context}` 占位符供 Sprint 4 注入 GCP 上下文
- 作为 Claude 对话的默认 system prompt

### 4. 创建测试端点 `app/api/claude_test.py`

仿照 `amap_test.py`、`voice_test.py` 模式：
- 路由前缀 `/api/test/claude`
- `POST /chat`：简单文本对话（非流式，快速验证）
- `POST /chat/stream`：SSE 流式对话测试
- `POST /web-search`：测试 web_search 工具
- 在 `app/api/__init__.py` 中注册

### 5. 更新 `/ws/chat` 接入 Claude（最小集成）

更新 `app/api/ws.py` 的 `/ws/chat` WebSocket handler：
- 连接建立时创建 `ConversationManager` 实例（按 WebSocket 连接维护）
- `TEXT_INPUT` → 调用 `claude.chat_stream(...)` 流式获取响应：
  - TEXT_DELTA → `WsMessageType.TOKEN_STREAM`
  - TOOL_USE_START/STOP → `WsMessageType.SKILL_START`/`SKILL_RESULT`（为 Sprint 3 预留）
  - MESSAGE_STOP → `WsMessageType.MESSAGE`，同时更新 ConversationManager
  - ERROR → `WsMessageType.ERROR`
- `AUDIO_START`/`AUDIO_CHUNK`/`AUDIO_STOP`：保持 NOT_IMPLEMENTED（ASR/TTS 完整 WebSocket 集成在 Sprint 6）
- `CLARIFY_REPLY`/`JOURNEY_ACTION`：保持 NOT_IMPLEMENTED（Sprint 4）

**注意**：此版本 /ws/chat 只做纯对话 + web_search，不涉及 Skills/状态机。Sprint 4 接入 LangGraph Agent 时会替换此处逻辑。代码中清晰标注 TODO，将对话逻辑封装为可替换函数。

### 6. 更新 README 进度标记

将 Sprint 2 标记为完成。

## 关键文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/core/config.py` | 修改 | 新增 web_search 配置项 |
| `app/adapters/claude.py` | **新建** | ClaudeClient + ConversationManager + ClaudeEvent |
| `app/adapters/prompts.py` | **新建** | System prompt 模板 |
| `app/api/claude_test.py` | **新建** | 测试 REST 端点 |
| `app/api/__init__.py` | 修改 | 注册 claude_test router |
| `app/api/ws.py` | 修改 | /ws/chat 接入 Claude 流式对话 |
| `.env.example` | 修改 | 新增 web_search 配置项 |
| `requirements.txt` | 修改 | 如有必要升级 anthropic SDK 版本 |
| `README.md` | 修改 | 标记 Sprint 2 完成 |

## 可复用的现有代码/模式

- **Adapter 模式**（`app/adapters/amap.py`）：`*Error(AppError)` + async client class + lazy singleton `get_*()` + `get_logger(__name__)`，Claude adapter 遵循相同模式。
- **配置**（`app/core/config.py`）：`Settings(BaseSettings)` singleton，追加 Claude 配置项。
- **WebSocket 消息**（`app/core/websocket.py`）：`WsMessageType` 已定义 TOKEN_STREAM/MESSAGE/SKILL_START/SKILL_RESULT/ERROR，直接使用。
- **错误处理**（`app/core/errors.py`）：`AppError` 基类，`ClaudeError` 继承 AppError。

## 验证方式

1. **启动后端**：`uvicorn app.main:app --reload`，确认无导入错误。
2. **REST 测试**：
   - `POST /api/test/claude/chat` 发送简单文本，验证收到 Claude 回复。
   - `POST /api/test/claude/web-search` 发送需要搜索的问题，验证 web_search 生效。
3. **WebSocket 测试**：
   - 连接 `ws://localhost:8000/ws/chat`，发送 TEXT_INPUT，应收到 TOKEN_STREAM 流式 token + 最终 MESSAGE。
   - 发送需要搜索的问题，验证 web_search 正常工作。
4. **多轮对话**：连续发多轮消息，验证上下文保持。
5. **错误场景**：空消息、超长消息、断开重连等，验证错误处理。
