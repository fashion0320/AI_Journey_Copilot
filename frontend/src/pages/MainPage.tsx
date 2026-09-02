import { useEffect } from 'react'
import ChatPanel from '@/components/ChatPanel/ChatPanel'
import MapView from '@/components/MapView/MapView'
import GcpPanel from '@/components/GcpPanel/GcpPanel'
import { useGcpWebSocket } from '@/hooks/useGcpWebSocket'
import { useChatWebSocket } from '@/hooks/useChatWebSocket'
import { api } from '@/services/api'
import { useGcpStore } from '@/store/gcpStore'
import './MainPage.css'

export default function MainPage() {
  const setContext = useGcpStore((s) => s.setContext)
  const showPanel = useGcpStore((s) => s.showPanel)
  const togglePanel = useGcpStore((s) => s.togglePanel)

  // 初始化 WebSocket 连接（唯一挂载点，避免重复注册监听器）
  useGcpWebSocket()
  const { sendText, asrInterimText, recorderSupported, startVoiceInput, stopVoiceInput } = useChatWebSocket()

  // 首次加载时拉取一次 GCP 快照
  useEffect(() => {
    api.getGcpContext().then(setContext).catch(console.error)
  }, [setContext])

  return (
    <div className="main-page">
      <div className="left-panel">
        <ChatPanel
          sendText={sendText}
          asrInterimText={asrInterimText}
          recorderSupported={recorderSupported}
          startVoiceInput={startVoiceInput}
          stopVoiceInput={stopVoiceInput}
        />
      </div>

      <div className="center-panel">
        <MapView />
      </div>

      <div className={`right-panel ${showPanel ? '' : 'hidden'}`}>
        {showPanel && <GcpPanel />}
      </div>

      <button
        className={`gcp-toggle ${showPanel ? 'gcp-visible' : 'gcp-hidden'}`}
        onClick={togglePanel}
        title={showPanel ? '隐藏控制面板' : '显示控制面板'}
      >
        {showPanel ? '◀ GCP' : 'GCP ▶'}
      </button>
    </div>
  )
}
