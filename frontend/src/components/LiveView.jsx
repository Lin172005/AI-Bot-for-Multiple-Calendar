import React from 'react'
import { FileTextIcon } from './Icon.jsx'
import LiveTranscript from './LiveTranscript.jsx'

export default function LiveView({ title, startedLabel, participantsCount, botId, backendUrl, authToken }) {
  return (
    <div>
      <div className="live-header">
        <span className="live-dot" />
        <span className="live-title">Live Meeting</span>
      </div>
      <div className="card" style={{ marginTop: 8 }}>
        <div className="event-title">{title}</div>
        <div className="muted">{startedLabel} · {participantsCount} participants</div>
      </div>
      <div className="card" style={{ marginTop: 12 }}>
        <div className="card-title" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <FileTextIcon /> Live Transcript
        </div>
        <div style={{ marginTop: 8 }}>
          {botId ? (
            <LiveTranscript botId={botId} apiBase={backendUrl} authToken={authToken} />
          ) : (
            <div className="empty">
              <div className="icon">🗣️</div>
              Bot not scheduled for this meeting yet.
            </div>
          )}
        </div>
        {/* Summary is generated automatically by backend/webhooks; no manual button needed. */}
      </div>
    </div>
  )
}
