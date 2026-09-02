"""豆包语音测试接口（开发用）。"""

from __future__ import annotations

import base64
import io
import wave
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..core.errors import ApiResponse
from ..adapters.volc_tts import VolcTtsClient, TtsEventType
from ..adapters.volc_asr import VolcAsrClient, AsrEventType

router = APIRouter(prefix="/api/test/voice", tags=["test-voice"])


class TtsTestReq(BaseModel):
    text: str = "你好，我是AI Journey Copilot，你的智能出行伙伴。"
    speaker: str = ""
    format: str = "pcm"  # pcm / mp3


@router.post("/tts")
async def test_tts(req: TtsTestReq):
    """TTS 测试：输入文本，返回 base64 编码的音频。"""
    try:
        tts = VolcTtsClient(audio_format=req.format)
        if req.speaker:
            tts.speaker = req.speaker

        audio_chunks: List[bytes] = []
        async for event in tts.synthesize(req.text):
            if event.type == TtsEventType.AUDIO:
                audio_chunks.append(event.audio)
            elif event.type == TtsEventType.ERROR:
                return ApiResponse.error(code=50003, message=f"TTS error: {event.text}")

        audio_data = b"".join(audio_chunks)
        return ApiResponse.success({
            "audio_b64": base64.b64encode(audio_data).decode(),
            "audio_bytes": len(audio_data),
            "format": req.format,
            "sample_rate": tts.sample_rate,
        })
    except Exception as e:
        return ApiResponse.error(code=50002, message=str(e))


@router.post("/asr")
async def test_asr(file: UploadFile = File(...)):
    """ASR 测试：上传音频文件，返回识别文本。

    支持 wav/raw pcm 格式（16kHz 16bit mono 推荐）。
    """
    try:
        audio_data = await file.read()
        if not audio_data:
            return ApiResponse.error(code=50001, message="empty audio file")

        # 判断是否是 wav 文件
        is_wav = audio_data[:4] == b"RIFF" and audio_data[8:12] == b"WAVE"
        sample_rate = 16000
        bits = 16
        channels = 1
        pcm_data = audio_data

        if is_wav:
            # 解析 wav header
            if len(audio_data) < 44:
                return ApiResponse.error(code=50004, message="invalid wav file (too short)")
            import struct
            channels = struct.unpack('<H', audio_data[22:24])[0]
            sample_rate = struct.unpack('<I', audio_data[24:28])[0]
            bits = struct.unpack('<H', audio_data[34:36])[0]
            # 找 data chunk
            pos = 36
            while pos < len(audio_data) - 8:
                chunk_id = audio_data[pos:pos+4]
                chunk_size = struct.unpack('<I', audio_data[pos+4:pos+8])[0]
                if chunk_id == b'data':
                    pcm_data = audio_data[pos+8:pos+8+chunk_size]
                    break
                pos += 8 + chunk_size

        asr = VolcAsrClient(
            sample_rate=sample_rate,
            bits=bits,
            channel=channels,
            audio_format="pcm",
            enable_nonstream=False,
        )

        partial_texts: List[str] = []
        final_text = ""
        bytes_per_sec = sample_rate * channels * (bits // 8)
        chunk_size = max(bytes_per_sec * 200 // 1000, 1024)  # 200ms 一片

        async for event in asr.start():
            if event.type == AsrEventType.START:
                # 开始发送音频
                for i in range(0, len(pcm_data), chunk_size):
                    chunk = pcm_data[i:i+chunk_size]
                    await asr.send_audio(chunk)
                    # 不 sleep，快速发送（非实时场景）
                await asr.finish()
            elif event.type == AsrEventType.PARTIAL:
                partial_texts.append(event.text)
            elif event.type == AsrEventType.FINAL:
                final_text = event.text
            elif event.type == AsrEventType.END:
                break
            elif event.type == AsrEventType.ERROR:
                return ApiResponse.error(code=50005, message=f"ASR error: {event.text}")

        result_text = final_text or (partial_texts[-1] if partial_texts else "")
        return ApiResponse.success({
            "text": result_text,
            "is_final": bool(final_text),
            "partial_count": len(partial_texts),
            "sample_rate": sample_rate,
            "audio_bytes": len(audio_data),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return ApiResponse.error(code=50002, message=str(e))
