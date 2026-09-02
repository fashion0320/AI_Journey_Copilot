import type { WsMessage, WsMessageType } from '@/types'

const WS_BASE = import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:8000'

export type WsListener = (msg: WsMessage) => void
export type WsConnectionListener = (connected: boolean) => void

/**
 * 轻量级 WebSocket 客户端：
 * - 自动重连（指数退避）
 * - 支持多监听器
 * - 消息类型分发
 * - 连接状态监听
 */
export class WsClient {
  private url: string
  private ws: WebSocket | null = null
  private listeners: Map<WsMessageType, Set<WsListener>> = new Map()
  globalListeners: Set<WsListener> = new Set()
  private connectionListeners: Set<WsConnectionListener> = new Set()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectAttempts = 0
  private manualClose = false
  private pingInterval: ReturnType<typeof setInterval> | null = null
  private requestCounter = 0

  constructor(endpoint: string) {
    this.url = `${WS_BASE}${endpoint}`
  }

  connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return
    this.manualClose = false

    try {
      this.ws = new WebSocket(this.url)
    } catch (e) {
      this.scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      console.debug(`[ws] connected: ${this.url}`)
      this.reconnectAttempts = 0
      this.startPing()
      this.notifyConnection(true)
    }

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WsMessage
        this.dispatch(msg)
      } catch (e) {
        console.warn('[ws] parse error:', e)
      }
    }

    this.ws.onclose = () => {
      console.debug(`[ws] disconnected: ${this.url}`)
      this.stopPing()
      this.notifyConnection(false)
      if (!this.manualClose) {
        this.scheduleReconnect()
      }
    }

    this.ws.onerror = (e) => {
      console.error(`[ws] error: ${this.url}`, e)
    }
  }

  disconnect() {
    this.manualClose = true
    this.stopPing()
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }

  send(type: WsMessageType, payload: any = {}, requestId?: string) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('[ws] not connected, dropping message:', type)
      return
    }
    const rid = requestId || `req_${++this.requestCounter}`
    const msg = { type, payload, request_id: rid }
    this.ws.send(JSON.stringify(msg))
    return rid
  }

  on(type: WsMessageType, listener: WsListener): () => void {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set())
    }
    this.listeners.get(type)!.add(listener)
    return () => this.listeners.get(type)?.delete(listener)
  }

  onGlobal(listener: WsListener): () => void {
    this.globalListeners.add(listener)
    return () => this.globalListeners.delete(listener)
  }

  onConnection(listener: WsConnectionListener): () => void {
    this.connectionListeners.add(listener)
    // 立即通知当前状态
    listener(this.connected)
    return () => this.connectionListeners.delete(listener)
  }

  private notifyConnection(connected: boolean) {
    for (const l of this.connectionListeners) {
      try { l(connected) } catch (e) { console.error('[ws] connection listener error:', e) }
    }
  }

  private dispatch(msg: WsMessage) {
    // 全局监听
    for (const l of this.globalListeners) {
      try { l(msg) } catch (e) { console.error('[ws] listener error:', e) }
    }
    // 类型监听
    const set = this.listeners.get(msg.type)
    if (set) {
      for (const l of set) {
        try { l(msg) } catch (e) { console.error('[ws] listener error:', e) }
      }
    }
  }

  private scheduleReconnect() {
    if (this.manualClose || this.reconnectTimer) return
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000)
    this.reconnectAttempts++
    console.debug(`[ws] reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private startPing() {
    this.stopPing()
    this.pingInterval = setInterval(() => {
      this.send('ping' as WsMessageType)
    }, 30000)
  }

  private stopPing() {
    if (this.pingInterval) {
      clearInterval(this.pingInterval)
      this.pingInterval = null
    }
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }
}

// 单例：对话通道
export const chatWs = new WsClient('/ws/chat')
// 单例：GCP 事件通道
export const gcpWs = new WsClient('/ws/gcp')
