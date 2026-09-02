import { useEffect, useCallback, useRef, useState } from 'react'
import { chatWs } from '@/services/websocket'
import { useChatStore } from '@/store/chatStore'
import { useJourneyStore } from '@/store/journeyStore'
import { useVoiceRecorder } from './useVoiceRecorder'
import type { WsMessageType } from '@/types'

/**
 * 对话 WebSocket Hook。
 * 连接 /ws/chat，处理所有下行事件，映射到 chatStore 和 journeyStore。
 * 集成语音（ASR/TTS）交互。
 *
 * 注意：使用全局单例 WsClient，组件挂载时注册监听，卸载时取消监听。
 * WS 连接本身由单例管理（自动重连），不随组件卸载而断开。
 */
export function useChatWebSocket() {
  const { addMessage, appendStreamingText, finishStreaming, finalizeMessage, setConnected, setRecording, setPlayingTts, appendThinkingText, finishThinking } = useChatStore()
  const {
    addCard,
    updateCard,
    setJourneyStatus,
    reset: resetJourney,
    clearResultCards,
  } = useJourneyStore()

  const ttsAudioRef = useRef<HTMLAudioElement | null>(null)
  const [asrInterimText, setAsrInterimText] = useState('')
  const asrReadyResolverRef = useRef<(() => void) | null>(null)
  const asrReadyPromiseRef = useRef<Promise<void> | null>(null)

  // ---- 语音录音 ----
  const sendAudioChunk = useCallback((audioB64: string) => {
    chatWs.send('audio_chunk' as WsMessageType, { audio: audioB64 })
  }, [])

  const { isRecording, isSupported: recorderSupported, startRecording, stopRecording, error: recorderError } = useVoiceRecorder(sendAudioChunk)

  const startVoiceInput = useCallback(async () => {
    if (isRecording) return

    // 创建等待 ASR ready 的 Promise
    let resolveReady!: () => void
    const readyPromise = new Promise<void>((resolve) => {
      resolveReady = resolve
    })
    asrReadyResolverRef.current = resolveReady
    asrReadyPromiseRef.current = readyPromise

    const rid = chatWs.send('audio_start' as WsMessageType, {
      sample_rate: 16000,
      format: 'pcm',
    })
    if (rid) {
      setAsrInterimText('')
      setRecording(true)
      // 等待 ASR 就绪（最多等2秒，超时也开始录音以保证体验）
      try {
        await Promise.race([
          readyPromise,
          new Promise<void>((resolve) => setTimeout(resolve, 2000)),
        ])
      } catch (e) {
        console.warn('[voice] waiting asr_ready error:', e)
      }
      await startRecording()
    }
  }, [isRecording, startRecording, setRecording])

  const stopVoiceInput = useCallback(async () => {
    await stopRecording()
    chatWs.send('audio_stop' as WsMessageType, {})
    setRecording(false)
  }, [stopRecording, setRecording])

  const sendText = useCallback((text: string) => {
    if (!text.trim()) return
    // 如果当前正在流式输出，先中断旧回复
    if (useChatStore.getState().isStreaming) {
      useChatStore.getState().interruptStreaming()
    }
    // 如果当前旅程状态是 idle 或 completed，开始新对话前清空所有旧卡片
    const currentStatus = useJourneyStore.getState().journeyStatus
    if (currentStatus === 'idle' || currentStatus === 'completed') {
      resetJourney()
    } else {
      // 中途新query：清除旧的结果类卡片（ETA/停车/路线等），重置选中方案ID
      // proposals/clarify 等交互类卡片会被后端新推送覆盖
      clearResultCards()
    }
    addMessage({ role: 'user', content: text })
    chatWs.send('text_input' as WsMessageType, { text })
  }, [addMessage, resetJourney, clearResultCards])

  // ---- TTS 播放 ----
  const playTts = useCallback((audioB64: string, format: string = 'mp3') => {
    // 停止当前播放
    if (ttsAudioRef.current) {
      ttsAudioRef.current.pause()
      ttsAudioRef.current = null
    }
    setPlayingTts(true)
    const audio = new Audio(`data:audio/${format};base64,${audioB64}`)
    ttsAudioRef.current = audio
    audio.onended = () => {
      setPlayingTts(false)
      if (ttsAudioRef.current === audio) {
        ttsAudioRef.current = null
      }
    }
    audio.onerror = () => {
      setPlayingTts(false)
      console.warn('[tts] audio play error')
      if (ttsAudioRef.current === audio) {
        ttsAudioRef.current = null
      }
    }
    audio.play().catch((e) => {
      console.warn('[tts] play failed:', e)
      setPlayingTts(false)
    })
  }, [setPlayingTts])

  useEffect(() => {
    const unsubs: Array<() => void> = []

    // —— 文本消息 ——
    unsubs.push(chatWs.on('token_stream' as WsMessageType, (msg) => {
      appendStreamingText(msg.payload?.text || '')
    }))

    // —— 思考过程 ——
    unsubs.push(chatWs.on('thinking_stream' as WsMessageType, (msg) => {
      appendThinkingText(msg.payload?.text || '')
    }))

    unsubs.push(chatWs.on('thinking_end' as WsMessageType, () => {
      finishThinking()
    }))

    unsubs.push(chatWs.on('message' as WsMessageType, (msg) => {
      // 使用 finalizeMessage：如果正在流式输出，则替换为最终文本（避免重复消息）
      // 如果没在流式，正常添加消息
      if (msg.payload?.content) {
        finalizeMessage(msg.payload.content, msg.payload.role || 'assistant')
      } else {
        finishStreaming()
      }
    }))

    // —— 卡片更新 ——
    unsubs.push(chatWs.on('card_update' as WsMessageType, (msg) => {
      const cardType = msg.payload?.type
      const cardData = msg.payload?.data
      const rid = msg.request_id || ''
      if (cardType && cardData !== undefined) {
        addCard(cardType, cardData, rid)
      }
    }))

    // —— 澄清追问 ——
    unsubs.push(chatWs.on('clarify_question' as WsMessageType, (msg) => {
      finishStreaming()
      addCard('clarify', msg.payload || {}, msg.request_id || '')
    }))

    // —— 状态变化 ——
    unsubs.push(chatWs.on('state_change' as WsMessageType, (msg) => {
      const toState = msg.payload?.to_state
      if (toState) {
        setJourneyStatus(toState)
      }
    }))

    // —— Skill 开始执行 ——
    unsubs.push(chatWs.on('skill_start' as WsMessageType, (msg) => {
      const { skill, action, task_id } = msg.payload || {}
      if (skill) {
        addCard('skill', {
          skill,
          action,
          task_id: task_id || skill,
          status: 'pending',
        }, msg.request_id || '')
      }
    }))

    // —— Skill 结果 ——
    unsubs.push(chatWs.on('skill_result' as WsMessageType, (msg) => {
      const { skill, action, task_id, status, result, error } = msg.payload || {}
      if (skill) {
        const tid = task_id || skill
        // 查找已存在的 pending 卡片并更新
        const journeyState = useJourneyStore.getState()
        const existing = journeyState.cards.find(
          (c) => c.type === 'skill' && c.data.task_id === tid,
        )
        if (existing) {
          updateCard(existing.id, {
            data: {
              ...existing.data,
              status: status || 'success',
              result,
              error,
            },
          })
        } else {
          // 没有 start 直接收到 result，新建卡片
          addCard('skill', {
            skill, action, task_id: tid,
            status: status || 'success',
            result, error,
          }, msg.request_id || '')
        }
      }
    }))

    // —— ASR 就绪 ——
    unsubs.push(chatWs.on('asr_ready' as WsMessageType, () => {
      // ASR 连接就绪，通知 startVoiceInput 可以开始录音了
      if (asrReadyResolverRef.current) {
        asrReadyResolverRef.current()
        asrReadyResolverRef.current = null
      }
    }))

    // —— ASR 实时转写 ——
    unsubs.push(chatWs.on('asr_text' as WsMessageType, (msg) => {
      const text = msg.payload?.text || ''
      setAsrInterimText(text)
    }))

    unsubs.push(chatWs.on('asr_final' as WsMessageType, (msg) => {
      const text = msg.payload?.text || ''
      setAsrInterimText('')
      // ASR 最终结果会自动触发旅程启动，后端会推送 message 事件
      // 这里只需确保录制状态已结束
      setRecording(false)
      if (text) {
        // 同时在 UI 上显示用户说了什么
        addMessage({ role: 'user', content: text })
      }
    }))

    // —— TTS 音频 ——
    unsubs.push(chatWs.on('tts_audio' as WsMessageType, (msg) => {
      const audioB64 = msg.payload?.audio_b64
      const format = msg.payload?.format || 'mp3'
      if (audioB64) {
        playTts(audioB64, format)
      }
    }))

    // —— 错误 ——
    unsubs.push(chatWs.on('error' as WsMessageType, (msg) => {
      finishStreaming()
      addCard('error', msg.payload || { code: 'UNKNOWN', message: '未知错误' }, msg.request_id || '')
      // 语音相关错误停止录音
      const code = msg.payload?.code || ''
      if (code.startsWith('VOICE_') || code.startsWith('ASR_')) {
        setRecording(false)
        setAsrInterimText('')
      }
    }))

    // —— 连接状态 ——
    unsubs.push(chatWs.onConnection((connected) => setConnected(connected)))

    // 确保连接已建立（全局单例，重复调用安全）
    chatWs.connect()

    return () => {
      unsubs.forEach((fn) => fn())
      // 注意：不主动断开 WS 连接（全局单例可能被其他组件使用）
      // 停止 TTS
      if (ttsAudioRef.current) {
        ttsAudioRef.current.pause()
        ttsAudioRef.current = null
        setPlayingTts(false)
      }
    }
  }, [addMessage, appendStreamingText, finishStreaming, finalizeMessage, setConnected, addCard, updateCard, setJourneyStatus, resetJourney, setRecording, setPlayingTts, playTts, appendThinkingText, finishThinking, clearResultCards])

  return {
    sendText,
    connected: useChatStore.getState().isConnected,
    // 语音
    isRecording,
    asrInterimText,
    recorderSupported,
    recorderError,
    startVoiceInput,
    stopVoiceInput,
  }
}
