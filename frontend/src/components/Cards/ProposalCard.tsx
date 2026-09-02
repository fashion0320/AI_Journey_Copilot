import { useJourneyStore } from '@/store/journeyStore'
import type { ProposalData } from '@/types'
import './Cards.css'
import './ProposalCard.css'

interface ProposalCardProps {
  proposals: ProposalData[]
}

const strategyLabels: Record<string, string> = {
  time_first: '最快',
  no_toll: '不走高速',
  shortest: '最短',
}

export default function ProposalCard({ proposals }: ProposalCardProps) {
  const selectPlan = useJourneyStore((s) => s.selectPlan)
  const selectedPlanId = useJourneyStore((s) => s.selectedPlanId)
  const journeyStatus = useJourneyStore((s) => s.journeyStatus)

  const isPlanning = journeyStatus === 'planning' || journeyStatus === 'ready'
  const hasSelected = selectedPlanId !== null

  const handleSelect = (planId: string) => {
    if (hasSelected) return  // 已选择，禁用重复点击
    selectPlan(planId)
  }

  if (!proposals || proposals.length === 0) return null

  return (
    <div className="card-base proposal-card">
      <div className="card-header">
        <span className="card-icon">🎯</span>
        <div>
          <div className="card-title">为您推荐了 {proposals.length} 套方案</div>
          <div className="card-subtitle">点击选择您偏好的出行方案</div>
        </div>
      </div>

      {hasSelected && (
        <div className="proposal-selected-hint">
          {isPlanning ? '⏳ 正在规划路线...' : '✓ 已选择方案'}
        </div>
      )}

      <div className="proposal-list">
        {proposals.map((proposal) => {
          const isSelected = selectedPlanId === proposal.id
          return (
            <div
              key={proposal.id}
              className={`proposal-item ${isSelected ? 'selected' : ''} ${hasSelected && !isSelected ? 'dimmed' : ''}`}
              onClick={() => handleSelect(proposal.id)}
            >
              <div className="proposal-header">
                <span className="proposal-title">{proposal.title}</span>
                {proposal.strategy && (
                  <span className="tag tag-indigo">
                    {strategyLabels[proposal.strategy] || proposal.strategy}
                  </span>
                )}
              </div>

              {proposal.summary && (
                <div className="proposal-summary">{proposal.summary}</div>
              )}

              <div className="proposal-meta">
                {proposal.eta_min > 0 && (
                  <span className="meta-item">
                    <span className="meta-icon">⏱</span>
                    {proposal.eta_min}分钟
                  </span>
                )}
                {proposal.distance_km > 0 && (
                  <span className="meta-item">
                    <span className="meta-icon">📍</span>
                    {proposal.distance_km}km
                  </span>
                )}
                {proposal.parking_hint && (
                  <span className="meta-item">
                    <span className="meta-icon">🅿️</span>
                    {proposal.parking_hint}
                  </span>
                )}
              </div>

              {proposal.reason && (
                <div className="proposal-reason">
                  💡 {proposal.reason}
                </div>
              )}

              {isSelected && (
                <div className="proposal-selected-badge">
                  ✓ 已选择
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
