"""WebSocket 消息类型定义与帮助函数。"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel, Field


class WsMessageType(str, Enum):
    # 上行（客户端 → 服务端）
    TEXT_INPUT = "text_input"          # 文本输入
    AUDIO_CHUNK = "audio_chunk"        # ASR 音频分片
    AUDIO_START = "audio_start"        # 开始语音输入
    AUDIO_STOP = "audio_stop"          # 结束语音输入
    CLARIFY_REPLY = "clarify_reply"    # 澄清追问回复
    JOURNEY_ACTION = "journey_action"  # 旅程操作（确认出发/选方案/取消等）
    GCP_UPDATE = "gcp_update"          # GCP 参数更新

    # 下行（服务端 → 客户端）
    TOKEN_STREAM = "token_stream"      # LLM 流式 token
    THINKING_STREAM = "thinking_stream" # AI 思考过程流式文本
    THINKING_END = "thinking_end"       # 思考过程结束
    ASR_TEXT = "asr_text"              # ASR 实时转写
    ASR_FINAL = "asr_final"            # ASR 最终结果
    ASR_READY = "asr_ready"            # ASR 会话已就绪，可以开始发送音频
    TTS_AUDIO = "tts_audio"            # TTS 音频分片
    MESSAGE = "message"                # 完整 AI 消息
    SKILL_START = "skill_start"        # Skill 开始执行
    SKILL_RESULT = "skill_result"      # Skill 执行结果
    CARD_UPDATE = "card_update"        # 卡片更新
    STATE_CHANGE = "state_change"      # 旅程状态变化
    CLARIFY_QUESTION = "clarify_question"  # 澄清追问
    ERROR = "error"                    # 错误
    PING = "ping"                      # 心跳
    PONG = "pong"                      # 心跳响应


class WsMessage(BaseModel):
    type: WsMessageType
    payload: Dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)


def ws_msg(type_: WsMessageType, payload: Any = None, request_id: str = "") -> str:
    return WsMessage(
        type=type_,
        payload=payload if payload is not None else {},
        request_id=request_id,
    ).to_json()


def parse_ws_message(text: str) -> WsMessage:
    data = json.loads(text)
    return WsMessage(**data)
