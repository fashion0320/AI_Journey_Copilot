import { useState, useEffect } from 'react'
import { useJourneyStore } from '@/store/journeyStore'
import type { ClarifyData } from '@/types'
import './Cards.css'
import './ClarifyCard.css'

interface ClarifyCardProps {
  data: ClarifyData
}

export default function ClarifyCard({ data }: ClarifyCardProps) {
  const { question, round, candidates } = data
  const sendClarifyReply = useJourneyStore((s) => s.sendClarifyReply)
  const selectCandidate = useJourneyStore((s) => s.selectCandidate)
  const [input, setInput] = useState('')
  const [replied, setReplied] = useState(false)

  // 当问题或候选列表变化时（新的澄清卡片到达），重置 replied 状态
  useEffect(() => {
    setReplied(false)
    setInput('')
  }, [question, candidates?.length])

  const handleSend = () => {
    if (!input.trim()) return
    setReplied(true)
    sendClarifyReply(input.trim())
    setInput('')
  }

  const handleCandidateClick = (candidate: any) => {
    setReplied(true)
    selectCandidate(candidate)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="card-base clarify-card">
      <div className="card-header">
        <span className="card-icon">❓</span>
        <div>
          <div className="card-title">需要确认一下</div>
          {round !== undefined && (
            <div className="card-subtitle">第 {round} 轮澄清</div>
          )}
        </div>
      </div>

      <div className="clarify-question">
        {question}
      </div>

      {candidates && candidates.length > 0 ? (
        <div className="clarify-candidates">
          {candidates.map((c, i) => (
            <div
              key={c.id || i}
              className="clarify-candidate-item"
              onClick={() => handleCandidateClick(c)}
            >
              <div className="candidate-name">
                <span className="candidate-index">{i + 1}</span>
                {c.name}
              </div>
              {c.address && (
                <div className="candidate-address">{c.address}</div>
              )}
              {c.distance && (
                <div className="candidate-distance">距离 {c.distance}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="clarify-input-row">
          <input
            type="text"
            className="clarify-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="请输入你的回复..."
            disabled={replied}
          />
          <button
            className="card-btn"
            onClick={handleSend}
            disabled={!input.trim() || replied}
          >
            发送
          </button>
        </div>
      )}

      {replied && (
        <div className="clarify-replied-hint">
          已回复，正在处理...
        </div>
      )}
    </div>
  )
}
