import type { JourneyStatus } from '@/types'
import './Cards.css'
import './StateBanner.css'

interface StateBannerProps {
  status: JourneyStatus
}

const statusConfig: Record<string, { label: string; icon: string; cls: string }> = {
  idle: { label: '待命', icon: '💤', cls: 'banner-idle' },
  understanding: { label: '理解中', icon: '🧠', cls: 'banner-thinking' },
  clarifying: { label: '需要澄清', icon: '❓', cls: 'banner-clarify' },
  destination_confirm: { label: '确认目的地', icon: '📍', cls: 'banner-clarify' },
  recommending: { label: '推荐方案中', icon: '✨', cls: 'banner-thinking' },
  parking_confirm: { label: '待出发', icon: '🅿️', cls: 'banner-ready' },
  planning: { label: '规划中', icon: '📋', cls: 'banner-thinking' },
  ready: { label: '待出发', icon: '⏱️', cls: 'banner-ready' },
  in_progress: { label: '行程中', icon: '🚗', cls: 'banner-progress' },
  replanning: { label: '重规划中', icon: '🔄', cls: 'banner-thinking' },
  arriving: { label: '即将到达', icon: '🎯', cls: 'banner-arriving' },
  completed: { label: '已到达', icon: '✅', cls: 'banner-completed' },
  ended: { label: '已结束', icon: '🏁', cls: 'banner-idle' },
}

export default function StateBanner({ status }: StateBannerProps) {
  const config = statusConfig[status] || statusConfig.idle

  if (status === 'idle') return null

  return (
    <div className={`state-banner ${config.cls}`}>
      <span className="banner-icon">{config.icon}</span>
      <span className="banner-label">{config.label}</span>
      {['understanding', 'recommending', 'planning', 'replanning'].includes(status) && (
        <span className="banner-dots">
          <span />
          <span />
          <span />
        </span>
      )}
    </div>
  )
}
