import React from 'react'
import { SearchIcon, SparklesIcon, MessageSquareIcon, MailIcon } from './Icon.jsx'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function KnowledgeView({
  qaQuestion,
  setQaQuestion,
  qaSources,
  setQaSources,
  qaLoading,
  askQuestion,
  qaAnswer,
  qaContexts,
}) {
  const toggleSource = (key) => {
    setQaSources((s) => ({ ...s, [key]: !s[key] }))
  }
  return (
    <div>
      <div className="detail-title">Knowledge Base</div>
      <div className="searchbar">
        <SearchIcon />
        <input
          placeholder="Ask about your meetings..."
          value={qaQuestion}
          onChange={(e) => setQaQuestion(e.target.value)}
        />
        <button className="btn btn-accent inline" onClick={askQuestion} disabled={qaLoading}>
          <SparklesIcon /> Ask
        </button>
      </div>
      <div className="chips">
        <button className={qaSources.transcript ? 'chip chip-active' : 'chip'} onClick={() => toggleSource('transcript')}>
          <MessageSquareIcon /> Transcripts
        </button>
        <button className={qaSources.email ? 'chip chip-active' : 'chip'} onClick={() => toggleSource('email')}>
          <MailIcon /> Emails
        </button>
      </div>
      {!qaLoading && !qaAnswer && (!qaContexts || qaContexts.length === 0) && (
        <div className="empty large">
          <div className="icon">🔎</div>
          Ask questions about your meetings and emails
        </div>
      )}
      {qaLoading && (
        <div className="muted" style={{ marginTop: 8 }}>Thinking…</div>
      )}
      {qaAnswer && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="card-title">AI Answer</div>
          <div className="markdown" style={{ marginTop: 6 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{qaAnswer}</ReactMarkdown>
          </div>
        </div>
      )}
      {qaContexts && qaContexts.length > 0 && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="card-title">Supporting Sources</div>
          <div className="context-list">
            {qaContexts.map((c, i) => (
              <div key={i} className="context-item">
                <div className="muted" style={{ marginBottom: 4 }}>{c.source || 'Source'}</div>
                <div>{c.text || c.excerpt || ''}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
