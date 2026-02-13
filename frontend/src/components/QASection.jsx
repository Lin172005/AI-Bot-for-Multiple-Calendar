import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function QASection({
  qaQuestion,
  setQaQuestion,
  qaSources,
  setQaSources,
  qaLoading,
  askQuestion,
  gmailLoading,
  ingestGmail,
  qaAnswer,
  qaContexts,
}) {
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="card-title">Ask about your past meetings</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <textarea className="textarea" rows={3} placeholder="e.g., What decisions were made last week?" value={qaQuestion} onChange={(e) => setQaQuestion(e.target.value)} />
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <label className="toggle">
            <input type="checkbox" checked={qaSources.transcript} onChange={(e) => setQaSources((s) => ({ ...s, transcript: e.target.checked }))} /> Include transcripts
          </label>
          <label className="toggle">
            <input type="checkbox" checked={qaSources.email} onChange={(e) => setQaSources((s) => ({ ...s, email: e.target.checked }))} /> Include emails
          </label>
          <button className="btn btn-primary" disabled={qaLoading} onClick={askQuestion}>{qaLoading ? 'Thinking…' : 'Ask'}</button>
          <div className="spacer" />
          <button className="btn" disabled={gmailLoading} onClick={ingestGmail}>{gmailLoading ? 'Ingesting Gmail…' : 'Ingest last 5 Gmail'}</button>
        </div>
        {qaAnswer && (
          <div className="card-meta markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{qaAnswer}</ReactMarkdown>
          </div>
        )}
        {!!qaContexts.length && (
          <details>
            <summary>Show supporting passages ({qaContexts.length})</summary>
            <div style={{ marginTop: 6, display: 'grid', gap: 8 }}>
              {qaContexts.map((c, i) => (
                <div key={c.id || i} className="card-meta small">
                  <div style={{ fontWeight: 600 }}>[#{i + 1}] {c?.metadata?.title || c?.metadata?.source || 'context'}</div>
                  {c?.metadata?.meeting_link && (
                    <div>Meeting: <a className="link" href={c.metadata.meeting_link} target="_blank" rel="noreferrer">{c.metadata.meeting_link}</a></div>
                  )}
                  <div style={{ whiteSpace: 'pre-wrap' }}>{c.text}</div>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  )
}
