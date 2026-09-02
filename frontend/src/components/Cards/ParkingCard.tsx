import { useJourneyStore } from '@/store/journeyStore'
import './Cards.css'
import './ParkingCard.css'
import type { ParkingData } from '@/types'

interface ParkingCardProps {
  data: ParkingData
}

export default function ParkingCard({ data }: ParkingCardProps) {
  const { parking_lots, entry_hint } = data
  const confirmDeparture = useJourneyStore((s) => s.confirmDeparture)
  const journeyStatus = useJourneyStore((s) => s.journeyStatus)

  const isDeparted = journeyStatus === 'planning'
    || journeyStatus === 'in_progress'
    || journeyStatus === 'replanning'
    || journeyStatus === 'arriving'
    || journeyStatus === 'completed'

  const handleDeparture = () => {
    if (isDeparted) return
    confirmDeparture()
  }

  if (!parking_lots || parking_lots.length === 0) return null

  return (
    <div className="card-base parking-card">
      <div className="card-header">
        <span className="card-icon">🅿️</span>
        <div>
          <div className="card-title">附近停车场推荐</div>
          <div className="card-subtitle">共 {parking_lots.length} 个，推荐停车场已选好</div>
        </div>
      </div>

      <div className="parking-list">
        {parking_lots.slice(0, 3).map((lot, index) => (
          <div key={index} className={`parking-item ${index === 0 ? 'recommended' : ''}`}>
            <div className="parking-rank">{index + 1}</div>
            <div className="parking-info-main">
              <div className="parking-name">
                {lot.name || '停车场'}
                {index === 0 && <span className="tag tag-green">推荐</span>}
              </div>
              {lot.address && (
                <div className="parking-address">{lot.address}</div>
              )}
              {entry_hint && index === 0 && (
                <div className="parking-entry-hint">🚪 {entry_hint}</div>
              )}
            </div>
            <div className="parking-meta">
              {lot.walk_min !== undefined && (
                <span className="walk-time">步行 {lot.walk_min}min</span>
              )}
              {lot.price && (
                <span className="parking-price">{lot.price}</span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="card-footer">
        <button
          className="card-btn"
          onClick={handleDeparture}
          disabled={isDeparted}
        >
          {isDeparted ? '🚗 行程规划中...' : '🚀 确认出发'}
        </button>
      </div>
    </div>
  )
}
