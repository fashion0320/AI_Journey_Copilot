import './Cards.css'
import './RouteCard.css'

interface RouteCardProps {
  data: {
    distance_km?: number
    duration_min?: number
    toll_cny?: number
    strategy?: string
  }
}

const strategyLabels: Record<string, string> = {
  time_first: '最快路线',
  no_toll: '不走高速',
  shortest: '最短路线',
}

export default function RouteCard({ data }: RouteCardProps) {
  const { distance_km, duration_min, toll_cny, strategy } = data

  return (
    <div className="card-base route-card">
      <div className="card-header">
        <span className="card-icon">🛣️</span>
        <div className="card-title">路线已规划</div>
        {strategy && (
          <span className="tag tag-indigo">
            {strategyLabels[strategy] || strategy}
          </span>
        )}
      </div>

      <div className="route-info-row">
        <div className="route-info-block">
          <span className="info-label">距离</span>
          <span className="info-value">{distance_km || '--'} km</span>
        </div>
        <div className="route-divider" />
        <div className="route-info-block">
          <span className="info-label">预计时间</span>
          <span className="info-value">{duration_min || '--'} 分钟</span>
        </div>
        <div className="route-divider" />
        <div className="route-info-block">
          <span className="info-label">过路费</span>
          <span className="info-value">{toll_cny ? `¥${toll_cny}` : '免费'}</span>
        </div>
      </div>
    </div>
  )
}
