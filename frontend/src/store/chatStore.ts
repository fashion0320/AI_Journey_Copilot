import { create } from 'zustand'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
}

interface ChatState {
  messages: ChatMessage[]
  isStreaming: boolean
  streamingText: string
  isRecording: boolean
  isPlayingTts: boolean
  isConnected: boolean
  isThinking: boolean
  thinkingText: string
  _lastFinalizedKey: string // 防御性去重：记录最近 finalize 的内容哈希
  addMessage: (msg: Omit<ChatMessage, 'id' | 'timestamp'>) => void
  appendStreamingText: (text: string) => void
  finishStreaming: () => void
  interruptStreaming: () => void
  finalizeMessage: (content: string, role?: 'user' | 'assistant' | 'system') => void
  appendThinkingText: (text: string) => void
  finishThinking: () => void
  setRecording: (rec: boolean) => void
  setPlayingTts: (p: boolean) => void
  setConnected: (c: boolean) => void
  clear: () => void
}

let msgId = 0
const nextId = () => `msg_${++msgId}_${Date.now()}`

export const useChatStore = create<ChatState>((set) => ({
  messages: [
    {
      id: 'welcome',
      role: 'assistant',
      content: '你好！我是 AI Journey Copilot，你的智能座舱出行伙伴。你可以试试说："准备出发去虹桥机场T2接周总，航班是MU5301" 或 "晚上下班后想和闺蜜聚一下"。',
      timestamp: Date.now(),
    },
  ],
  isStreaming: false,
  streamingText: '',
  isRecording: false,
  isPlayingTts: false,
  isConnected: false,
  isThinking: false,
  thinkingText: '',
  _lastFinalizedKey: '',

  addMessage: (msg) =>
    set((state) => ({
      messages: [...state.messages, { ...msg, id: nextId(), timestamp: Date.now() }],
    })),
  appendStreamingText: (text) =>
    set((state) => ({
      isStreaming: true,
      streamingText: state.streamingText + text,
    })),
  finishStreaming: () =>
    set((state) => {
      const updates: Record<string, any> = {
        streamingText: '',
        isStreaming: false,
        thinkingText: '',
        isThinking: false,
      }
      if (state.streamingText) {
        // Convert streaming bubble to a permanent message
        const finalMsg: ChatMessage = {
          id: nextId(),
          role: 'assistant',
          content: state.streamingText,
          timestamp: Date.now(),
        }
        updates.messages = [...state.messages, finalMsg]
      }
      return updates
    }),
  interruptStreaming: () =>
    set((state) => {
      if (!state.isStreaming) return {}
      // 将当前流式文本转为消息，标注已中断
      const finalMsg: ChatMessage | null = state.streamingText
        ? {
            id: nextId(),
            role: 'assistant',
            content: state.streamingText + '…（已中断）',
            timestamp: Date.now(),
          }
        : null
      return {
        messages: finalMsg ? [...state.messages, finalMsg] : state.messages,
        streamingText: '',
        isStreaming: false,
        thinkingText: '',
        isThinking: false,
      }
    }),
  appendThinkingText: (text) =>
    set((state) => ({
      isThinking: true,
      thinkingText: state.thinkingText + text,
    })),
  finishThinking: () =>
    set(() => ({
      isThinking: false,
    })),
  finalizeMessage: (content: string, role: 'user' | 'assistant' | 'system' = 'assistant') =>
    set((state) => {
      // 防御性去重：如果内容和角色与上一条消息完全相同（2秒内），跳过
      const dedupKey = `${role}:${content}`
      const lastMsg = state.messages[state.messages.length - 1]
      const isDuplicate = lastMsg
        && lastMsg.role === role
        && lastMsg.content === content
        && (Date.now() - lastMsg.timestamp) < 3000

      if (isDuplicate) {
        return { isStreaming: false, streamingText: '', thinkingText: '', isThinking: false }
      }

      // If we were streaming, convert streaming bubble to final message
      if (state.isStreaming && state.streamingText) {
        const finalMsg: ChatMessage = {
          id: nextId(),
          role,
          content,
          timestamp: Date.now(),
        }
        return {
          messages: [...state.messages, finalMsg],
          streamingText: '',
          isStreaming: false,
          thinkingText: '',
          isThinking: false,
          _lastFinalizedKey: dedupKey,
        }
      }
      // Not streaming - just add the message normally
      return {
        messages: [...state.messages, {
          id: nextId(),
          role,
          content,
          timestamp: Date.now(),
        }],
        isStreaming: false,
        streamingText: '',
        thinkingText: '',
        isThinking: false,
        _lastFinalizedKey: dedupKey,
      }
    }),
  setRecording: (rec) => set({ isRecording: rec }),
  setPlayingTts: (p) => set({ isPlayingTts: p }),
  setConnected: (c) => set({ isConnected: c }),
  clear: () => set({ messages: [], streamingText: '', isStreaming: false }),
}))
