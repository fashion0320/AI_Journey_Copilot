import './Cards.css'
import './ArrivingCard.css'
import type { ArrivingData } from '@/types'

interface ArrivingCardProps {
  data: ArrivingData
}

export default function ArrivingCard({ data }: ArrivingCardProps) {
  const { destination, parking, parking_lots, arrival_message } = data

  return (
    <div className="card-base arriving-card">
      <div className="arriving-ribbon">
        <span>🎊 即将到达</span>
      </div>

      <div className="card-header">
        <span className="card-icon">📍</span>
        <div>
          <div className="card-title">{destination || '目的地'}</div>
          <div className="card-subtitle">请减速慢行，注意安全</div>
        </div>
      </div>

      {arrival_message && (
        <div className="arrival-message">
          {arrival_message}
        </div>
      )}

      {parking && (
        <div className="parking-guide">
          <div className="section-label">🅿️ 停车引导</div>
          <div className="parking-guide-text">{parking}</div>
        </div>
      )}

      {parking_lots && parking_lots.length > 0 && (
        <div className="arrival-parking-list">
          {parking_lots.slice(0, 3).map((lot, i) => (
            <div key={i} className="arrival-parking-item">
              <span className="arrival-parking-name">{lot.name}</span>
              {lot.walk_min !== undefined && (
                <span className="arrival-parking-walk">步行 {lot.walk_min}min</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
