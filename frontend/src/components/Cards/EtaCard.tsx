import './Cards.css'
import './EtaCard.css'

interface EtaCardProps {
  data: {
    remaining_min?: number
    eta_arrival_time?: string
    traffic_level?: string
    confidence_band_min?: number
  }
}

const trafficLabels: Record<string, { label: string; cls: string }> = {
  smooth: { label: '畅通', cls: 'tag-green' },
  slow: { label: '缓行', cls: 'tag-amber' },
  congested: { label: '拥堵', cls: 'tag-red' },
  severe: { label: '严重拥堵', cls: 'tag-red' },
  unknown: { label: '未知', cls: 'tag-indigo' },
}

export default function EtaCard({ data }: EtaCardProps) {
  const { remaining_min, eta_arrival_time, traffic_level, confidence_band_min } = data
  const traffic = trafficLabels[traffic_level || 'unknown'] || trafficLabels.unknown

  return (
    <div className="card-base eta-card">
      <div className="eta-content">
        <div className="eta-left">
          <div className="info-label">预计剩余时间</div>
          <div className="eta-number">
            {remaining_min !== undefined ? remaining_min : '--'}
            <span className="eta-unit"> 分钟</span>
          </div>
          {eta_arrival_time && (
            <div className="eta-arrival-text">预计 {eta_arrival_time} 到达</div>
          )}
        </div>
        <div className="eta-right">
          <span className={`tag ${traffic.cls}`}>
            {traffic.label}
          </span>
          {confidence_band_min !== undefined && (
            <div className="eta-confidence">
              ±{confidence_band_min} 分钟
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
