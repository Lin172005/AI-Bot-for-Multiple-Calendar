import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import LiveTranscript from './LiveTranscript.jsx'

export default function EventCard({
  e,
  botMap,
  backendUrl,
  authToken,
  summaryLoading,
  summaries,
  removeBot,
  getSummary,
  ingestTranscriptForEvent,
  scheduleBot,
}) {
  const hasBot = !!botMap[e.event_id]
  const botId = botMap[e.event_id]

  const dt = e.start_time ? new Date(e.start_time) : null
  const dateStr = dt ? dt.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) : 'N/A'
  const timeStr = dt ? dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : ''

  return (
    <div className="card event-card">
      <div className="event-head">
        <div className="event-when">
          <div className="event-date">{dateStr}</div>
          {timeStr && <div className="event-time">{timeStr}</div>}
        </div>
        <div className="event-title">{e.title || '(No title)'}</div>
      </div>
      <div className="card-meta">Meet: {e.meet_link ? <a className="link" href={e.meet_link} target="_blank" rel="noreferrer">{e.meet_link}</a> : 'N/A'}</div>
      {e.meet_link ? (
        hasBot ? (
          <div className="inline-controls">
            <button className="btn" onClick={() => removeBot(e)}>Remove Bot</button>
            <span className="muted">Summary and Q&A ingest will run after the meeting ends.</span>
          </div>
        ) : (
          <button className="btn btn-primary" onClick={() => scheduleBot(e)}>Add Bot</button>
        )
      ) : (
        <div className="muted">No meeting link detected</div>
      )}
      {summaries[e.event_id] && (
        <details style={{ marginTop: 10 }}>
          <summary>Summary</summary>
          <div className="markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{summaries[e.event_id]}</ReactMarkdown>
          </div>
        </details>
      )}

      {hasBot && (
        <details style={{ marginTop: 10 }} open>
          <summary>Live Transcript</summary>
          <LiveTranscript botId={botId} apiBase={backendUrl} authToken={authToken} />
        </details>
      )}
    </div>
  )
}
