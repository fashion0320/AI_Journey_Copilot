import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 录音 Hook —— 使用 Web Audio API 采集 16kHz 单声道 PCM 音频。
 *
 * 通过 AudioContext + ScriptProcessorNode 从麦克风实时获取 Float32 采样，
 * 转换为 16-bit PCM Int16 小端格式，适合直接喂给火山引擎 ASR。
 *
 * 使用方式：
 *   const { isRecording, isSupported, startRecording, stopRecording, error } = useVoiceRecorder(
 *     (pcmBase64) => sendAudioChunk(pcmBase64)  // 每次攒够一片时回调
 *   )
 */

const TARGET_SAMPLE_RATE = 16000
const CHUNK_SIZE_SAMPLES = 1600  // ~100ms @ 16kHz
const CHUNK_SIZE_BYTES = CHUNK_SIZE_SAMPLES * 2  // Int16 = 2 bytes

export interface UseVoiceRecorderReturn {
  isRecording: boolean
  isSupported: boolean
  startRecording: () => Promise<void>
  stopRecording: () => Promise<void>
  error: string | null
}

export function useVoiceRecorder(
  onChunk: (base64Pcm: string) => void,
): UseVoiceRecorderReturn {
  const [isRecording, setIsRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isSupported, setIsSupported] = useState(true)

  const audioCtxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const pendingPcmRef = useRef<Int16Array>(new Int16Array(0))
  const onChunkRef = useRef(onChunk)
  onChunkRef.current = onChunk

  useEffect(() => {
    const supported = !!(
      typeof window !== 'undefined'
      && navigator.mediaDevices
      && navigator.mediaDevices.getUserMedia
      && (window.AudioContext || (window as any).webkitAudioContext)
    )
    setIsSupported(supported)
  }, [])

  const floatTo16BitPCM = (samples: Float32Array): Int16Array => {
    const out = new Int16Array(samples.length)
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]))
      out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF
    }
    return out
  }

  // 重采样到 16kHz（线性插值）
  const resample = (input: Float32Array, fromRate: number, toRate: number): Float32Array => {
    if (fromRate === toRate) return input
    const ratio = fromRate / toRate
    const newLen = Math.round(input.length / ratio)
    const out = new Float32Array(newLen)
    for (let i = 0; i < newLen; i++) {
      const srcIdx = i * ratio
      const idx0 = Math.floor(srcIdx)
      const idx1 = Math.min(idx0 + 1, input.length - 1)
      const frac = srcIdx - idx0
      out[i] = input[idx0] * (1 - frac) + input[idx1] * frac
    }
    return out
  }

  const int16ToBase64 = (samples: Int16Array): string => {
    const bytes = new Uint8Array(samples.buffer)
    // 简单 base64（浏览器原生 btoa，处理二进制）
    let binary = ''
    const chunk = 0x8000
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(
        null,
        bytes.subarray(i, i + chunk) as unknown as number[],
      )
    }
    return btoa(binary)
  }

  const flushChunks = () => {
    const pending = pendingPcmRef.current
    if (pending.length < CHUNK_SIZE_SAMPLES) return
    let offset = 0
    while (offset + CHUNK_SIZE_SAMPLES <= pending.length) {
      const chunk = pending.slice(offset, offset + CHUNK_SIZE_SAMPLES)
      try {
        onChunkRef.current(int16ToBase64(chunk))
      } catch (e) {
        console.error('[recorder] chunk send error:', e)
      }
      offset += CHUNK_SIZE_SAMPLES
    }
    pendingPcmRef.current = pending.slice(offset)
  }

  const handleAudioProcess = useCallback((e: AudioProcessingEvent) => {
    const input = e.inputBuffer.getChannelData(0)
    const ctx = audioCtxRef.current
    if (!ctx) return

    const resampled = resample(input, ctx.sampleRate, TARGET_SAMPLE_RATE)
    const pcm = floatTo16BitPCM(resampled)

    // 追加到 pending buffer
    const merged = new Int16Array(pendingPcmRef.current.length + pcm.length)
    merged.set(pendingPcmRef.current)
    merged.set(pcm, pendingPcmRef.current.length)
    pendingPcmRef.current = merged

    flushChunks()
  }, [])

  const startRecording = useCallback(async () => {
    if (isRecording) return
    setError(null)

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })
      streamRef.current = stream

      const Ctx = window.AudioContext || (window as any).webkitAudioContext
      const ctx = new Ctx()
      audioCtxRef.current = ctx

      const source = ctx.createMediaStreamSource(stream)
      sourceRef.current = source

      const processor = ctx.createScriptProcessor(4096, 1, 1)
      processor.onaudioprocess = handleAudioProcess
      processorRef.current = processor

      source.connect(processor)
      processor.connect(ctx.destination)
      pendingPcmRef.current = new Int16Array(0)

      setIsRecording(true)
      console.debug('[recorder] started, input sampleRate:', ctx.sampleRate)
    } catch (e: any) {
      const msg = e?.message || '无法访问麦克风'
      setError(msg)
      console.error('[recorder] start error:', e)
      // 清理
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop())
        streamRef.current = null
      }
    }
  }, [isRecording, handleAudioProcess])

  const stopRecording = useCallback(async () => {
    if (!isRecording) return

    // 发送最后残留的音频
    const remaining = pendingPcmRef.current
    if (remaining.length > 0) {
      try {
        onChunkRef.current(int16ToBase64(remaining))
      } catch (e) {
        console.error('[recorder] final chunk send error:', e)
      }
      pendingPcmRef.current = new Int16Array(0)
    }

    // 断开并清理
    if (processorRef.current) {
      processorRef.current.disconnect()
      processorRef.current.onaudioprocess = null
      processorRef.current = null
    }
    if (sourceRef.current) {
      sourceRef.current.disconnect()
      sourceRef.current = null
    }
    if (audioCtxRef.current) {
      try { await audioCtxRef.current.close() } catch (_) {}
      audioCtxRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }

    setIsRecording(false)
    console.debug('[recorder] stopped')
  }, [isRecording])

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      if (isRecording) {
        processorRef.current?.disconnect()
        sourceRef.current?.disconnect()
        audioCtxRef.current?.close().catch(() => {})
        streamRef.current?.getTracks().forEach((t) => t.stop())
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { isRecording, isSupported, startRecording, stopRecording, error }
}
