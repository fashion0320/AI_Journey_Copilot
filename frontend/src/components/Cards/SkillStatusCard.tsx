import { useState } from 'react'
import type { SkillStatusData } from '@/types'
import './Cards.css'
import './SkillStatusCard.css'

interface SkillStatusCardProps {
  data: SkillStatusData
}

const skillLabels: Record<string, string> = {
  route_master: '路线规划',
  dynamic_eta: 'ETA 计算',
  smart_remind: '智能提醒',
  local_poi: 'POI 搜索',
  parking_find: '停车搜索',
}

const statusLabels: Record<string, { label: string; cls: string; icon: string }> = {
  pending: { label: '执行中', cls: 'status-pending', icon: '' },
  success: { label: '已完成', cls: 'status-success', icon: '✓' },
  error: { label: '失败', cls: 'status-error', icon: '✕' },
  no_result: { label: '无结果', cls: 'status-noresult', icon: '○' },
  partial: { label: '部分完成', cls: 'status-partial', icon: '◐' },
}

export default function SkillStatusCard({ data }: SkillStatusCardProps) {
  const [expanded, setExpanded] = useState(false)
  const { skill, action, task_id, status, result, error } = data

  const statusInfo = statusLabels[status] || statusLabels.pending
  const label = skillLabels[skill] || skill

  return (
    <div
      className={`card-base skill-status-card ${statusInfo.cls}`}
      onClick={() => setExpanded(!expanded)}
    >
      <div className="skill-status-row">
        <div className="skill-icon">
          {status === 'pending' ? (
            <span className="spinner" />
          ) : (
            <span className="skill-status-icon">{statusInfo.icon}</span>
          )}
        </div>
        <div className="skill-info">
          <span className="skill-name">{label}</span>
          <span className="skill-action">{action}</span>
        </div>
        <span className={`skill-status-tag ${statusInfo.cls}`}>
          {statusInfo.label}
        </span>
        <span className="skill-expand-icon">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="skill-detail">
          <div className="skill-detail-row">
            <span className="detail-label">任务 ID</span>
            <span className="detail-value">{task_id}</span>
          </div>
          {result && (
            <div className="skill-detail-row">
              <span className="detail-label">结果</span>
              <span className="detail-value mono">
                {typeof result === 'object'
                  ? JSON.stringify(result, null, 2).slice(0, 500)
                  : String(result)}
              </span>
            </div>
          )}
          {error && (
            <div className="skill-detail-row error">
              <span className="detail-label">错误</span>
              <span className="detail-value">{error}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
