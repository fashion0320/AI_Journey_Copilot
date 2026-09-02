"""豆包语音 TTS 适配器（火山引擎语音合成大模型 2.0，HTTP 非流式）。

文档：语音合成
URL: https://openspeech.bytedance.com/api/v1/tts
鉴权: X-Api-Key (HTTP Header)

接口返回 MP3 格式的 base64 编码音频数据。
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, Optional

import httpx

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger(__name__)


class TtsEventType(str, Enum):
    AUDIO = "audio"
    SENTENCE_START = "sentence_start"
    SENTENCE_END = "sentence_end"
    FINISH = "finish"
    ERROR = "error"


@dataclass
class TtsEvent:
    type: TtsEventType
    audio: bytes = b""
    text: str = ""
    session_id: str = ""
    subtitle_words: list = None


class VolcTtsError(Exception):
    pass


class VolcTtsClient:
    """豆包语音 TTS 客户端（HTTP 非流式）。

    用法:
        tts = VolcTtsClient()
        async for event in tts.synthesize("你好，欢迎使用AI助手"):
            if event.type == TtsEventType.AUDIO:
                # 播放或保存音频
                pass
    """

    ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"

    def __init__(
        self,
        api_key: str = "",
        resource_id: str = "",
        speaker: str = "",
        audio_format: str = "mp3",
        sample_rate: int = 24000,
        bit_rate: int = 128000,
        speech_rate: int = 0,
        loudness_rate: int = 0,
        enable_subtitle: bool = False,
        pitch: int = 0,
        disable_markdown_filter: bool = True,
    ):
        self.api_key = api_key or settings.volcengine_tts_api_key or settings.volcengine_api_key
        self.resource_id = resource_id or settings.volcengine_tts_resource_id or "seed-tts-2.0"
        self.speaker = speaker or settings.volcengine_tts_speaker or "zh_female_shuangkuai_moon_bigtts"
        self.audio_format = audio_format
        self.sample_rate = sample_rate
        self.bit_rate = bit_rate
        self.speech_rate = speech_rate
        self.loudness_rate = loudness_rate
        self.enable_subtitle = enable_subtitle
        self.pitch = pitch
        self.disable_markdown_filter = disable_markdown_filter

        self._session_id: str = ""

    # ---------- 公共 API ----------

    async def synthesize(self, text: str) -> AsyncGenerator[TtsEvent, None]:
        """合成语音，以事件流的形式返回（虽然是非流式，但保持接口兼容）。

        Yields:
            SENTENCE_START → AUDIO → SENTENCE_END → FINISH
            出错时返回 ERROR。
        """
        if not text or not text.strip():
            return
        if not self.api_key:
            raise VolcTtsError("VOLCENGINE_API_KEY not configured")

        self._session_id = str(uuid.uuid4())
        logger.info("tts synthesize, session=%s, text_len=%d, speaker=%s",
                    self._session_id[:8], len(text), self.speaker)

        try:
            audio_data = await self._do_synthesize(text)
        except VolcTtsError as e:
            yield TtsEvent(type=TtsEventType.ERROR, text=str(e), session_id=self._session_id)
            return
        except Exception as e:
            logger.error("tts unexpected error: %s", e)
            yield TtsEvent(type=TtsEventType.ERROR, text=str(e), session_id=self._session_id)
            return

        # 模拟流式事件序列，保持与原接口兼容
        yield TtsEvent(type=TtsEventType.SENTENCE_START, text=text, session_id=self._session_id)
        yield TtsEvent(type=TtsEventType.AUDIO, audio=audio_data, session_id=self._session_id)
        yield TtsEvent(type=TtsEventType.SENTENCE_END, session_id=self._session_id)
        yield TtsEvent(type=TtsEventType.FINISH, session_id=self._session_id)

    # ---------- 内部方法 ----------

    async def _do_synthesize(self, text: str) -> bytes:
        """调用 HTTP TTS 接口，返回音频二进制数据。"""
        # HTTP 接口的 encoding 参数: 支持 mp3 / pcm / wav
        encoding = self.audio_format
        if encoding == "pcm":
            encoding = "pcm"

        # 语速/音量转换:
        #   HTTP 接口 speed_ratio 范围 0.5~2.0 (0.5倍速 ~ 2.0倍速)
        #   我们的 speech_rate 范围 -50~100 (-50=0.5x, 100=2.0x)
        speed_ratio = 1.0 + self.speech_rate / 100.0
        speed_ratio = max(0.5, min(2.0, speed_ratio))

        volume_ratio = 1.0 + self.loudness_rate / 100.0
        volume_ratio = max(0.5, min(2.0, volume_ratio))

        # 音调转换:
        #   HTTP 接口 pitch_ratio 范围 0.5~2.0
        #   我们的 pitch 范围 -12~12 (半音)
        #   粗略换算: 12 半音 ≈ 1 倍频 → pitch_ratio = 2^(pitch/12)
        pitch_ratio = 2 ** (self.pitch / 12.0)
        pitch_ratio = max(0.5, min(2.0, pitch_ratio))

        payload = {
            "app": {
                "appid": "ai_journey_copilot",
                "token": "",
                "cluster": "volcano_tts",
            },
            "user": {"uid": self._session_id},
            "audio": {
                "voice_type": self.speaker,
                "encoding": encoding,
                "speed_ratio": speed_ratio,
                "volume_ratio": volume_ratio,
                "pitch_ratio": pitch_ratio,
                "sample_rate": self.sample_rate,
            },
            "request": {
                "reqid": self._session_id,
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }

        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key,
        }

        logger.debug("tts http request: speaker=%s, encoding=%s, sample_rate=%d",
                     self.speaker, encoding, self.sample_rate)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.ENDPOINT,
                json=payload,
                headers=headers,
            )

            if resp.status_code != 200:
                raise VolcTtsError(f"HTTP {resp.status_code}: {resp.text[:200]}")

            try:
                data = resp.json()
            except json.JSONDecodeError as e:
                raise VolcTtsError(f"invalid JSON response: {e}")

            # 火山引擎 TTS 返回 code=3000 表示成功
            code = data.get("code", -1)
            message = data.get("message", "")

            if code != 3000:
                raise VolcTtsError(f"TTS error code={code}: {message}")

            audio_b64 = data.get("data", "")
            if not audio_b64:
                raise VolcTtsError("empty audio data in response")

            try:
                audio_bytes = base64.b64decode(audio_b64)
            except Exception as e:
                raise VolcTtsError(f"base64 decode failed: {e}")

            logger.debug("tts http response: audio_size=%d bytes", len(audio_bytes))
            return audio_bytes
