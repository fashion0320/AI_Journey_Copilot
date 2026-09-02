import { useState, useRef, useEffect, useMemo } from 'react'
import { useChatStore } from '@/store/chatStore'
import { useJourneyStore } from '@/store/journeyStore'
import { CardItem, StateBanner } from '@/components/Cards'
import type { CardItem as CardItemType } from '@/types'
import './ChatPanel.css'

interface ChatItem {
  id: string
  kind: 'message' | 'card' | 'streaming'
  timestamp: number
  data: any
}

interface ChatPanelProps {
  sendText: (text: string) => void
  asrInterimText: string
  recorderSupported: boolean
  startVoiceInput: () => Promise<void>
  stopVoiceInput: () => Promise<void>
}

export default function ChatPanel({ sendText, asrInterimText, recorderSupported, startVoiceInput, stopVoiceInput }: ChatPanelProps) {
  const { messages, streamingText, isStreaming, isConnected, isRecording, isPlayingTts, isThinking, thinkingText } = useChatStore()
  const { journeyStatus, cards } = useJourneyStore()
  const [input, setInput] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  // 录音中，input 显示实时转写
  useEffect(() => {
    if (isRecording && asrInterimText) {
      setInput(asrInterimText)
    }
  }, [isRecording, asrInterimText])

  // 合并消息与卡片，按时间排序
  const mergedItems = useMemo<ChatItem[]>(() => {
    const items: ChatItem[] = []
    for (const msg of messages) {
      items.push({
        id: `msg_${msg.id}`,
        kind: 'message',
        timestamp: typeof msg.timestamp === 'number' ? msg.timestamp : 0,
        data: msg,
      })
    }
    for (const card of cards) {
      items.push({
        id: `card_${card.id}`,
        kind: 'card',
        timestamp: card.timestamp,
        data: card,
      })
    }
    items.sort((a, b) => a.timestamp - b.timestamp)
    return items
  }, [messages, cards])

  // 自动滚到底部
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [messages, streamingText, cards.length, mergedItems.length])

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    if (isRecording) return
    sendText(text)
    setInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleMicClick = async () => {
    if (isRecording) {
      await stopVoiceInput()
    } else {
      await startVoiceInput()
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <span className="chat-title">AI Journey Copilot</span>
        <span className={`chat-status ${isConnected ? 'online' : 'offline'}`}>
          {isConnected ? '● 已连接' : '● 连接中...'}
        </span>
      </div>

      <StateBanner status={journeyStatus} />

      <div className="chat-messages" ref={listRef}>
        {mergedItems.length === 0 && (
          <div className="chat-welcome">
            <div className="welcome-icon">🚗</div>
            <div className="welcome-title">AI Journey Copilot</div>
            <div className="welcome-desc">
              告诉我你想去哪里，我来帮你规划行程、推荐路线、查询实时路况。
            </div>
            <div className="welcome-suggestions">
              <button
                className="suggestion-chip"
                onClick={() => { sendText('我要去浦东机场接人'); }}
              >
                去机场接人
              </button>
              <button
                className="suggestion-chip"
                onClick={() => { sendText('帮我找个附近的日料店'); }}
              >
                找附近的日料店
              </button>
              <button
                className="suggestion-chip"
                onClick={() => { sendText('导航到陆家嘴'); }}
              >
                导航到陆家嘴
              </button>
            </div>
          </div>
        )}

        {mergedItems.map((item) => {
          if (item.kind === 'message') {
            const msg = item.data
            return (
              <div key={item.id} className={`msg msg-${msg.role} msg-enter`}>
                <div className="msg-avatar">{msg.role === 'user' ? '我' : 'AI'}</div>
                <div className="msg-bubble">{msg.content}</div>
              </div>
            )
          }
          // card
          return (
            <div key={item.id} className="card-enter">
              <CardItem card={item.data as CardItemType} />
            </div>
          )
        })}

        {isThinking && (
          <div className="msg msg-assistant msg-enter thinking-bubble-container">
            <div className="msg-avatar">AI</div>
            <div className="msg-bubble thinking-bubble">
              <span className="thinking-icon">💭</span>
              <span className="thinking-text">{thinkingText || '思考中...'}</span>
              {isThinking && <span className="thinking-dots"><span>.</span><span>.</span><span>.</span></span>}
            </div>
          </div>
        )}

        {isStreaming && streamingText && (
          <div className="msg msg-assistant msg-enter">
            <div className="msg-avatar">AI</div>
            <div className="msg-bubble streaming">{streamingText}</div>
          </div>
        )}
      </div>

      <div className="chat-input-area">
        {recorderSupported && (
          <button
            className={`mic-btn ${isRecording ? 'recording' : ''}`}
            onClick={handleMicClick}
            title={isRecording ? '停止录音' : '语音输入'}
          >
            {isRecording ? (
              <span className="mic-icon mic-recording">⏺</span>
            ) : (
              <span className="mic-icon">🎤</span>
            )}
          </button>
        )}
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isRecording ? '正在聆听...' : '输入你的出行需求...'}
          rows={2}
          disabled={isRecording}
          className={isRecording ? 'recording-input' : ''}
        />
        <button className="send-btn" onClick={handleSend} disabled={!input.trim() || isRecording}>
          发送
        </button>
      </div>
    </div>
  )
}
