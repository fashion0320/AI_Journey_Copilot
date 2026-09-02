import { useJourneyStore } from '@/store/journeyStore'
import type { JourneyReadyData } from '@/types'
import './Cards.css'
import './JourneyReadyCard.css'

interface JourneyReadyCardProps {
  data: JourneyReadyData
}

const trafficLabels: Record<string, { label: string; cls: string }> = {
  smooth: { label: '畅通', cls: 'tag-green' },
  slow: { label: '缓行', cls: 'tag-amber' },
  congested: { label: '拥堵', cls: 'tag-red' },
  severe: { label: '严重拥堵', cls: 'tag-red' },
  unknown: { label: '未知', cls: 'tag-indigo' },
}

export default function JourneyReadyCard({ data }: JourneyReadyCardProps) {
  const confirmDeparture = useJourneyStore((s) => s.confirmDeparture)
  const journeyStatus = useJourneyStore((s) => s.journeyStatus)

  const isDeparted = journeyStatus === 'in_progress'
    || journeyStatus === 'replanning'
    || journeyStatus === 'arriving'
    || journeyStatus === 'completed'

  const handleDeparture = () => {
    if (isDeparted) return
    confirmDeparture()
  }

  const { destination, route, eta, parking, reminders } = data
  const traffic = trafficLabels[eta?.traffic_level || 'unknown'] || trafficLabels.unknown

  return (
    <div className="card-base journey-ready-card">
      <div className="card-header">
        <span className="card-icon">🚗</span>
        <div>
          <div className="card-title">行程已就绪</div>
          <div className="card-subtitle">{destination?.name || '目的地'}</div>
        </div>
        <span className="tag tag-green">准备出发</span>
      </div>

      {/* ETA 大数字 */}
      <div className="eta-hero">
        <div className="eta-big">
          <span className="info-value-large">
            {eta?.remaining_min || route?.duration_min || '--'}
            <span className="unit">分钟</span>
          </span>
          {eta?.eta_arrival_time && (
            <div className="eta-arrival">预计 {eta.eta_arrival_time} 到达</div>
          )}
        </div>
        <span className={`tag ${traffic.cls}`}>{traffic.label}</span>
      </div>

      {/* 路线信息 */}
      <div className="info-grid">
        <div className="info-item">
          <span className="info-label">距离</span>
          <span className="info-value">{route?.distance_km || '--'} km</span>
        </div>
        <div className="info-item">
          <span className="info-label">过路费</span>
          <span className="info-value">{route?.toll_cny ? `¥${route.toll_cny}` : '免费'}</span>
        </div>
      </div>

      {/* 停车 */}
      {parking?.lots && parking.lots.length > 0 && (
        <div className="parking-info">
          <div className="section-label">🅿️ 推荐停车</div>
          <div className="parking-item">
            <span className="parking-name">
              {parking.recommended_index >= 0 && parking.lots[parking.recommended_index]?.name || parking.lots[0]?.name}
            </span>
            {parking.lots[0]?.walk_min !== undefined && (
              <span className="parking-walk">步行 {parking.lots[0].walk_min} 分钟</span>
            )}
          </div>
        </div>
      )}

      {/* 提醒 */}
      {reminders && reminders.length > 0 && reminders.some((r: any) => r?.tts_text) && (
        <div className="reminders-info">
          <div className="section-label">📋 出行提醒</div>
          {reminders.slice(0, 2).map((r: any, i: number) =>
            r?.tts_text ? (
              <div key={i} className="reminder-item">{r.tts_text}</div>
            ) : null,
          )}
        </div>
      )}

      <div className="card-footer">
        <button
          className="card-btn"
          onClick={handleDeparture}
          disabled={isDeparted}
        >
          {isDeparted ? '🚗 行程进行中...' : '🚀 确认出发'}
        </button>
      </div>
    </div>
  )
}
