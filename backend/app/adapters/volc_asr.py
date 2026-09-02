"""豆包语音流式 ASR 适配器（火山引擎语音识别大模型 2.0，双向流式 WebSocket）。

文档：双向流式语音识别 WebSocket
URL: wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
鉴权: X-Api-Key (API Key)

协议格式：自定义二进制帧（参考 SDK protocol.py）
  - header: 4 bytes
      byte0: version(4bit) | header_size(4bit)
      byte1: msg_type(4bit) | flags(4bit)
      byte2: serialization(4bit) | compression(4bit)
      byte3: reserved
  - sequence: int32 big-endian（flags 有 POS_SEQUENCE/NEG_SEQUENCE 时）
  - payload_size: uint32 big-endian
  - payload: gzip 压缩的 JSON

使用流程:
    asr = VolcAsrClient()
    async for event in asr.start():
        if event.type == 'partial':
            print('实时:', event.text)
        elif event.type == 'final':
            print('最终:', event.text)
    # 边说边发音频: await asr.send_audio(bytes_data)
    # 说完了: await asr.finish()
"""

from __future__ import annotations

import asyncio
import gzip
import json
import struct
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, Optional

import websockets

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger(__name__)


class AsrEventType(str, Enum):
    PARTIAL = "partial"       # 实时/中间结果
    FINAL = "final"           # 最终结果（一句话结束，definite=true）
    UTTERANCE = "utterance"   # 完整分句
    START = "start"           # 识别开始
    END = "end"               # 识别结束
    ERROR = "error"           # 错误


@dataclass
class AsrEvent:
    type: AsrEventType
    text: str = ""
    is_final: bool = False   # 是否最终确定（definite=true）
    utterance_id: str = ""
    additions: dict = None   # 附加信息（语速、情绪等）


class VolcAsrError(Exception):
    pass


# ===================== 协议常量 =====================

class _MsgType:
    CLIENT_FULL_REQUEST = 0b0001
    CLIENT_AUDIO_ONLY_REQUEST = 0b0010
    SERVER_FULL_RESPONSE = 0b1001
    SERVER_ERROR_RESPONSE = 0b1111


class _Flags:
    NO_SEQUENCE = 0b0000
    POS_SEQUENCE = 0b0001
    NEG_SEQUENCE = 0b0010
    NEG_WITH_SEQUENCE = 0b0011
    # bit2: last package (server->client)
    # bit3: event number (server->client)


class _Serialization:
    NONE = 0b0000
    JSON = 0b0001


class _Compression:
    NONE = 0b0000
    GZIP = 0b0001


# ===================== 协议编解码 =====================

def _build_frame(
    msg_type: int,
    flags: int,
    payload: bytes,
    *,
    sequence: Optional[int] = None,
    serialization: int = _Serialization.JSON,
    compression: int = _Compression.GZIP,
    version: int = 0b0001,
    header_size: int = 1,  # header_size 值表示 4*n 字节，1=4字节
) -> bytes:
    """构造 ASR 二进制帧。"""
    # 压缩 payload
    if compression == _Compression.GZIP:
        compressed = gzip.compress(payload)
    else:
        compressed = payload

    buf = bytearray()
    # byte 0: version(4bit) | header_size(4bit)
    buf.append((version << 4) | (header_size & 0x0f))
    # byte 1: msg_type(4bit) | flags(4bit)
    buf.append((msg_type << 4) | (flags & 0x0f))
    # byte 2: serialization(4bit) | compression(4bit)
    buf.append((serialization << 4) | (compression & 0x0f))
    # byte 3: reserved
    buf.append(0x00)
    # (header_size=1 时 header 总共 4 字节)

    # sequence（如果有）
    if sequence is not None:
        buf.extend(struct.pack(">i", sequence))

    # payload_size
    buf.extend(struct.pack(">I", len(compressed)))
    # payload
    buf.extend(compressed)

    return bytes(buf)


def _parse_frame(data: bytes) -> dict:
    """解析 ASR 二进制帧，返回 dict。"""
    if len(data) < 4:
        raise VolcAsrError(f"frame too short: {len(data)} bytes")

    version = (data[0] >> 4) & 0x0f
    header_size_nibble = data[0] & 0x0f
    header_bytes = header_size_nibble * 4

    msg_type = (data[1] >> 4) & 0x0f
    flags = data[1] & 0x0f
    serialization = (data[2] >> 4) & 0x0f
    compression = data[2] & 0x0f

    payload_pos = header_bytes
    payload = data[payload_pos:]

    result = {
        "version": version,
        "msg_type": msg_type,
        "flags": flags,
        "serialization": serialization,
        "compression": compression,
        "sequence": 0,
        "is_last": False,
        "event_code": 0,
        "payload_msg": None,
        "error_code": 0,
    }

    # 解析 flags
    pos = 0
    if flags & 0x01:  # POS_SEQUENCE 或 NEG_WITH_SEQUENCE 的 sequence
        if len(payload) - pos < 4:
            raise VolcAsrError("frame too short for sequence")
        result["sequence"] = struct.unpack(">i", payload[pos:pos+4])[0]
        pos += 4
    if flags & 0x02:  # last package (NEG_SEQUENCE / 最后一包标志)
        result["is_last"] = True
    if flags & 0x04:  # event number
        if len(payload) - pos < 4:
            raise VolcAsrError("frame too short for event")
        result["event_code"] = struct.unpack(">i", payload[pos:pos+4])[0]
        pos += 4

    # 解析 payload size + payload
    if msg_type == _MsgType.SERVER_FULL_RESPONSE:
        if len(payload) - pos < 4:
            raise VolcAsrError("frame too short for payload_size")
        payload_size = struct.unpack(">I", payload[pos:pos+4])[0]
        pos += 4
        payload_body = payload[pos:pos+payload_size]
    elif msg_type == _MsgType.SERVER_ERROR_RESPONSE:
        if len(payload) - pos < 8:
            raise VolcAsrError("frame too short for error")
        result["error_code"] = struct.unpack(">i", payload[pos:pos+4])[0]
        pos += 4
        payload_size = struct.unpack(">I", payload[pos:pos+4])[0]
        pos += 4
        payload_body = payload[pos:pos+payload_size]
    else:
        payload_body = b""

    # 解压
    if payload_body:
        if compression == _Compression.GZIP:
            try:
                payload_body = gzip.decompress(payload_body)
            except Exception as e:
                logger.warning("asr gzip decompress failed: %s", e)
        # JSON 解析
        if serialization == _Serialization.JSON and payload_body:
            try:
                result["payload_msg"] = json.loads(payload_body.decode("utf-8"))
            except Exception as e:
                logger.warning("asr json parse failed: %s", e)

    return result


# ===================== 客户端 =====================

class VolcAsrClient:
    """豆包语音流式 ASR 客户端。

    每次识别是一次性会话：
    1. 调用 start() 建立连接并启动识别，返回异步生成器
    2. 调用 send_audio() 发送 PCM 音频分片
    3. 调用 finish() 结束本次识别
    """

    URI = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"

    def __init__(
        self,
        api_key: str = "",
        resource_id: str = "volc.seedasr.sauc.duration",  # 豆包流式2.0 小时版
        sample_rate: int = 16000,
        bits: int = 16,
        channel: int = 1,
        enable_itn: bool = True,
        enable_punc: bool = True,
        enable_ddc: bool = False,
        show_utterances: bool = True,
        enable_nonstream: bool = True,  # 二遍识别，提升最终准确率
        model_name: str = "bigmodel",
        audio_format: str = "pcm",
        codec: str = "raw",
    ):
        self.api_key = api_key or settings.volcengine_asr_api_key or settings.volcengine_api_key
        self.resource_id = resource_id
        self.sample_rate = sample_rate
        self.bits = bits
        self.channel = channel
        self.enable_itn = enable_itn
        self.enable_punc = enable_punc
        self.enable_ddc = enable_ddc
        self.show_utterances = show_utterances
        self.enable_nonstream = enable_nonstream
        self.model_name = model_name
        self.audio_format = audio_format
        self.codec = codec

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._recv_task: Optional[asyncio.Task] = None
        self._send_task: Optional[asyncio.Task] = None
        self._request_id: str = ""
        self._seq = 1
        self._connected = False
        self._finished = False
        self._audio_queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    # ---------- 公共 API ----------

    async def start(self) -> AsyncGenerator[AsrEvent, None]:
        """建立 WebSocket 连接并启动识别。返回异步事件生成器。"""
        if not self.api_key:
            raise VolcAsrError("VOLCENGINE_API_KEY not configured")

        self._request_id = str(uuid.uuid4())
        self._seq = 1

        headers = [
            ("X-Api-Key", self.api_key),
            ("X-Api-Resource-Id", self.resource_id),
            ("X-Api-Request-Id", self._request_id),
        ]

        logger.info("asr connecting: %s, request_id=%s", self.URI, self._request_id[:8])

        try:
            self._ws = await websockets.connect(
                self.URI,
                extra_headers=headers,
                ping_interval=None,
                max_size=20 * 1024 * 1024,  # 20MB
            )
        except Exception as e:
            raise VolcAsrError(f"connect failed: {e}") from e

        self._connected = True

        # 启动接收循环
        self._recv_task = asyncio.create_task(self._recv_loop())
        # 启动音频发送循环
        self._send_task = asyncio.create_task(self._send_loop())

        # 发送配置帧（Full Client Request）
        config_payload = self._build_config_payload()
        config_frame = _build_frame(
            _MsgType.CLIENT_FULL_REQUEST,
            _Flags.POS_SEQUENCE,
            json.dumps(config_payload, ensure_ascii=False).encode("utf-8"),
            sequence=self._seq,
        )
        self._seq += 1
        await self._ws.send(config_frame)
        logger.debug("asr config sent (seq=%d)", self._seq - 1)

        await self._push_event(AsrEvent(type=AsrEventType.START))

        # 返回事件生成器
        try:
            while True:
                event = await self._event_queue.get()
                if event.type == AsrEventType.END or event.type == AsrEventType.ERROR:
                    yield event
                    break
                yield event
        finally:
            await self._cleanup()

    async def send_audio(self, audio_bytes: bytes) -> None:
        """发送 PCM 音频分片。"""
        if not self._connected or self._finished:
            return
        try:
            self._audio_queue.put_nowait(audio_bytes)
        except asyncio.QueueFull:
            logger.warning("asr audio queue full, dropping frame")

    async def finish(self) -> None:
        """告诉服务端音频发送完毕。"""
        if self._finished:
            return
        self._finished = True
        # 放一个 None 作为结束标记
        try:
            self._audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    # ---------- 内部 ----------

    def _build_config_payload(self) -> dict:
        """构造初始配置 payload（JSON）。

        注意：audio.format 必须是 "pcm"（不是 "raw"），
        否则服务端报 "unsupported format raw"。
        音频数据发送时为纯 PCM 字节（gzip 压缩）。
        """
        fmt = self.audio_format
        if fmt == "raw":
            fmt = "pcm"  # raw 不被服务端接受，统一用 pcm
        return {
            "user": {
                "uid": "ai-journey-copilot",
            },
            "audio": {
                "format": fmt,
                "codec": self.codec,
                "rate": self.sample_rate,
                "bits": self.bits,
                "channel": self.channel,
            },
            "request": {
                "model_name": self.model_name,
                "enable_itn": self.enable_itn,
                "enable_punc": self.enable_punc,
                "enable_ddc": self.enable_ddc,
                "show_utterances": self.show_utterances,
                "enable_nonstream": self.enable_nonstream,
                "result_type": "full",
            },
        }

    async def _send_loop(self) -> None:
        """从音频队列读取分片，按协议封装并发送。

        注意：音频帧使用 serialization=RAW(0), compression=GZIP(1)，
        且配置帧里 audio.format 需要是 "pcm"（不是 "raw"）。
        """
        try:
            while True:
                chunk = await self._audio_queue.get()
                if chunk is None:
                    # 结束：发送最后一包（NEG_WITH_SEQUENCE，seq 取负）
                    last_frame = _build_frame(
                        _MsgType.CLIENT_AUDIO_ONLY_REQUEST,
                        _Flags.NEG_WITH_SEQUENCE,
                        b"",
                        sequence=-self._seq,
                        serialization=_Serialization.NONE,
                        compression=_Compression.GZIP,
                    )
                    if self._ws and self._connected:
                        await self._ws.send(last_frame)
                    logger.debug("asr last audio frame sent (seq=%d)", self._seq)
                    break

                # 普通音频帧：serialization=NONE(原始数据), compression=GZIP
                frame = _build_frame(
                    _MsgType.CLIENT_AUDIO_ONLY_REQUEST,
                    _Flags.POS_SEQUENCE,
                    chunk,
                    sequence=self._seq,
                    serialization=_Serialization.NONE,
                    compression=_Compression.GZIP,
                )
                self._seq += 1
                if self._ws and self._connected:
                    await self._ws.send(frame)

        except Exception as e:
            logger.error("asr send loop error: %s", e)
            await self._push_event(AsrEvent(type=AsrEventType.ERROR, text=str(e)))

    async def _recv_loop(self) -> None:
        """接收 WebSocket 消息并解析为事件。"""
        try:
            async for message in self._ws:
                if isinstance(message, str):
                    logger.warning("asr received unexpected text: %s", message[:100])
                    continue

                try:
                    frame = _parse_frame(message)
                except Exception as e:
                    logger.warning("asr parse frame failed: %s", e)
                    continue

                await self._handle_frame(frame)

        except websockets.ConnectionClosed:
            logger.info("asr connection closed")
            await self._push_event(AsrEvent(type=AsrEventType.END))
        except Exception as e:
            logger.error("asr recv loop error: %s", e)
            await self._push_event(AsrEvent(type=AsrEventType.ERROR, text=str(e)))

    async def _handle_frame(self, frame: dict) -> None:
        """处理解析后的帧。

        响应 payload 结构（实测）：
        {
          "result": {
            "text": "识别结果全文",
            "utterances": [...],  // 如果 show_utterances=true
            "additions": { "log_id": "..." },
            ...
          },
          // 可能还有 code、is_last_package 等外层字段？
          // 实测 flags 的 bit1(is_last) 来自 frame flags，不是 payload
        }

        注意：is_last_package 通过 frame flags 的 bit1 (NEG_SEQUENCE flag) 判断，
        不是 payload 里的字段。
        """
        msg_type = frame["msg_type"]
        is_last = frame["is_last"]

        if msg_type == _MsgType.SERVER_ERROR_RESPONSE:
            err_msg = "unknown error"
            if isinstance(frame["payload_msg"], dict):
                err_msg = frame["payload_msg"].get("error", str(frame["payload_msg"]))
            elif isinstance(frame["payload_msg"], str):
                err_msg = frame["payload_msg"]
            logger.error("asr server error: code=%s, msg=%s", frame["error_code"], err_msg)
            await self._push_event(AsrEvent(type=AsrEventType.ERROR, text=str(err_msg)))
            return

        if msg_type != _MsgType.SERVER_FULL_RESPONSE:
            logger.debug("asr unknown msg_type: %s", msg_type)
            if is_last:
                await self._push_event(AsrEvent(type=AsrEventType.END))
            return

        data = frame["payload_msg"]
        if not data or not isinstance(data, dict):
            if is_last:
                await self._push_event(AsrEvent(type=AsrEventType.END))
            return

        result = data.get("result", {})
        if not isinstance(result, dict):
            result = {}

        text = result.get("text", "")
        additions = result.get("additions", {})
        utterances = result.get("utterances", [])

        # 推送文本结果
        if text:
            if is_last:
                event = AsrEvent(
                    type=AsrEventType.FINAL,
                    text=text,
                    is_final=True,
                    additions=additions,
                )
            else:
                event = AsrEvent(
                    type=AsrEventType.PARTIAL,
                    text=text,
                    is_final=False,
                )
            await self._push_event(event)

        # 推送分句事件（如果有）
        if utterances and isinstance(utterances, list):
            for utt in utterances:
                if isinstance(utt, dict):
                    utt_text = utt.get("text", "")
                    if utt_text:
                        await self._push_event(AsrEvent(
                            type=AsrEventType.UTTERANCE,
                            text=utt_text,
                            is_final=utt.get("definite", False),
                            utterance_id=utt.get("utterance_id", utt.get("source", "")),
                            additions=utt.get("additions", {}),
                        ))

        # 最后一包 → 结束
        if is_last:
            await self._push_event(AsrEvent(type=AsrEventType.END))

    async def _push_event(self, event: AsrEvent) -> None:
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("asr event queue full, dropping event")

    async def _cleanup(self) -> None:
        for task in (self._send_task, self._recv_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._ws and self._connected:
            try:
                await self._ws.close()
            except Exception:
                pass

        self._connected = False
        logger.debug("asr client cleaned up")
