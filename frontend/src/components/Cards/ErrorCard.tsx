import type { ErrorData } from '@/types'
import './Cards.css'
import './ErrorCard.css'

interface ErrorCardProps {
  data: ErrorData
}

export default function ErrorCard({ data }: ErrorCardProps) {
  const { code, message } = data

  return (
    <div className="card-base error-card">
      <div className="card-header">
        <span className="card-icon">⚠️</span>
        <div>
          <div className="card-title">出错了</div>
          {code && <div className="card-subtitle">错误代码: {code}</div>}
        </div>
      </div>

      <div className="error-message">
        {message || '发生了未知错误'}
      </div>

      <div className="error-hint">
        请稍后重试，或检查网络连接。
      </div>
    </div>
  )
}
