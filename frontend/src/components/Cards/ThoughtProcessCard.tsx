import { useState } from 'react'
import type { ThoughtProcessData } from '@/types'
import './Cards.css'
import './ThoughtProcessCard.css'

interface ThoughtProcessCardProps {
  data: ThoughtProcessData
}

/**
 * Parse structured thinking text into renderable segments.
 * Supports:
 * - Bold: **text**
 * - Numbered headings: "1. **Heading:** content"
 * - Bullet points: "- item"
 * - Multi-line content within sections
 */
function renderThinkingContent(content: string) {
  const lines = content.split('\n')
  const elements: JSX.Element[] = []
  let keyIdx = 0
  let currentPoint: { num: string; heading: string; bodyLines: string[] } | null = null

  const flushPoint = () => {
    if (!currentPoint) return
    elements.push(
      <div key={keyIdx++} className="thought-point">
        <div className="thought-point-row">
          <span className="thought-point-num">{currentPoint.num}.</span>
          <span className="thought-point-heading">{currentPoint.heading}：</span>
        </div>
        {currentPoint.bodyLines.length > 0 && (
          <div className="thought-point-body">
            {currentPoint.bodyLines.map((bodyLine, bi) => {
              // Check for bullet points within body
              const bulletMatch = bodyLine.trim().match(/^[-•]\s+(.+)$/)
              if (bulletMatch) {
                return (
                  <div key={bi} className="thought-bullet">
                    <span className="thought-bullet-dot">•</span>
                    <span>{renderInlineBold(bulletMatch[1])}</span>
                  </div>
                )
              }
              return (
                <div key={bi} className="thought-body-line">
                  {renderInlineBold(bodyLine)}
                </div>
              )
            })}
          </div>
        )}
      </div>,
    )
    currentPoint = null
  }

  for (const line of lines) {
    const trimmed = line.trim()

    // Skip empty lines but flush current point
    if (!trimmed) {
      flushPoint()
      continue
    }

    // Check for numbered heading pattern: "1. **Heading:** content" or "1. **Heading：** content"
    const numMatch = trimmed.match(/^(\d+)\.\s*\*\*(.+?)[：:]\*\*\s*(.*)$/)
    if (numMatch) {
      flushPoint() // flush any previous point
      const [, num, heading, firstBody] = numMatch
      currentPoint = {
        num,
        heading,
        bodyLines: firstBody ? [firstBody] : [],
      }
      continue
    }

    // Check for continuation lines after a numbered heading (no number, not a new section)
    if (currentPoint) {
      // Continuation: indented lines, bullet points, or text that doesn't start a new pattern
      const isNewNumber = /^\d+\.\s*\*\*/.test(trimmed)
      const isTitle = /^\*\*.+[：:]\*\*$/.test(trimmed)
      if (!isNewNumber && !isTitle) {
        currentPoint.bodyLines.push(trimmed)
        continue
      }
    }

    flushPoint()

    // Check for title pattern: "**Thought Process：**" or "**思考过程：**"
    const titleMatch = trimmed.match(/^\*\*(.+?)[：:]\*\*$/)
    if (titleMatch) {
      elements.push(
        <div key={keyIdx++} className="thought-title">
          {titleMatch[1]}
        </div>,
      )
      continue
    }

    // Check for "**Heading：**" or similar title + content on same line
    const titleMatch2 = trimmed.match(/^\*\*(.+?)[：:]\*\*(.*)$/)
    if (titleMatch2) {
      elements.push(
        <div key={keyIdx++} className="thought-sub-heading">
          <span className="thought-point-heading">{titleMatch2[1]}：</span>
          {titleMatch2[2] && (
            <span className="thought-point-body">{renderInlineBold(titleMatch2[2])}</span>
          )}
        </div>,
      )
      continue
    }

    // Check for standalone bullet points (outside numbered sections)
    const bulletMatch = trimmed.match(/^[-•]\s+(.+)$/)
    if (bulletMatch) {
      elements.push(
        <div key={keyIdx++} className="thought-bullet">
          <span className="thought-bullet-dot">•</span>
          <span>{renderInlineBold(bulletMatch[1])}</span>
        </div>,
      )
      continue
    }

    // Regular line
    elements.push(
      <div key={keyIdx++} className="thought-line">
        {renderInlineBold(trimmed)}
      </div>,
    )
  }

  flushPoint() // flush any remaining point
  return elements
}

function renderInlineBold(text: string): JSX.Element | string {
  if (!text.includes('**')) return text
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i}>{part.slice(2, -2)}</strong>
        }
        return <span key={i}>{part}</span>
      })}
    </>
  )
}

export default function ThoughtProcessCard({ data }: ThoughtProcessCardProps) {
  const [expanded, setExpanded] = useState(true)
  const { content } = data

  if (!content) return null

  return (
    <div className={`card-base thought-process-card ${expanded ? 'expanded' : 'collapsed'}`}>
      <button
        className="thought-header"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span className="thought-icon">💭</span>
        <span className="thought-header-text">思考过程</span>
        <span className="thought-chevron">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <div className="thought-body">
          {renderThinkingContent(content)}
        </div>
      )}
    </div>
  )
}
