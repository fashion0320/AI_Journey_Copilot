import { useEffect } from 'react'
import { gcpWs } from '@/services/websocket'
import { useGcpStore } from '@/store/gcpStore'
import type { WsMessageType } from '@/types'

/**
 * GCP WebSocket Hook —— 订阅 GCP 上下文变化，更新 store。
 */
export function useGcpWebSocket() {
  const { setContext } = useGcpStore()

  useEffect(() => {
    const applySnapshot = (msg: any) => {
      const snapshot = msg?.payload?.snapshot ?? msg?.snapshot
      if (snapshot) {
        setContext(snapshot)
      }
    }

    const unsubState = gcpWs.on('state_change' as WsMessageType, applySnapshot)

    const unsubGlobal = gcpWs.onGlobal((msg: any) => {
      if (msg.type === 'gcp_update') {
        applySnapshot(msg)
      }
    })

    gcpWs.connect()

    return () => {
      unsubState()
      unsubGlobal()
    }
  }, [setContext])
}
