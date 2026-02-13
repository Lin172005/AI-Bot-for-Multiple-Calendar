import React, { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeftIcon, FileTextIcon } from './Icon.jsx'

function cleanSummary(s) {
  if (!s) return ''
  let t = s.replace(/^(\*{3,}|-{3,}|_{3,})\s*$/gm, '')
  t = t.replace(/^\s*[-•]\s*/gm, '• ')
  t = t.replace(/[\t ]+$/gm, '')
  t = t.replace(/\n{3,}/g, '\n\n')
  t = t.replace(/^\s+/gm, '')
  return t.trim()
}

export default function PastMeetingDetail({ item, onBack, backendUrl, authToken }) {
  if (!item) return null
  const start = item.start_time ? new Date(item.start_time) : null
  const dateStr = start ? start.toLocaleDateString() : '-'
  const meta = [dateStr, '30–60 min', `${(item.participants_count || 0)} participants`].join(' · ')
  const summaryText = cleanSummary(item.summary || '')
  const [transcript, setTranscript] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!backendUrl || !authToken || !item?.attendee_bot_id) return
    ;(async () => {
      setLoading(true); setErr('')
      try {
        const res = await fetch(`${backendUrl}/bots/${encodeURIComponent(item.attendee_bot_id)}/transcript?format=text`, {
          headers: { Authorization: `Bearer ${authToken}` },
        })
        const data = await res.json().catch(() => ({}))
        if (!res.ok) throw new Error(JSON.stringify(data))
        setTranscript(data.text || '')
      } catch (e) {
        setErr(String(e.message || e))
      } finally {
        setLoading(false)
      }
    })()
  }, [backendUrl, authToken, item?.attendee_bot_id])
  return (
    <div>
      <button className="btn btn-ghost" onClick={onBack} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <ArrowLeftIcon /> Back
      </button>
      <div className="detail-title">{item.title || 'Untitled meeting'}</div>
      <div className="muted" style={{ marginBottom: 12 }}>{meta}</div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginBottom: 8 }}>
        <button className="btn btn-ghost">Share</button>
        <button className="btn btn-ghost">Export</button>
      </div>
      <div className="card" style={{ background: 'var(--surface-muted)' }}>
        <div className="section-label">Summary</div>
        <div className="markdown" style={{ marginTop: 6 }}>
          {summaryText ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{summaryText}</ReactMarkdown>
          ) : (
            <p className="muted">No summary yet.</p>
          )}
        </div>
      </div>
      <div className="card" style={{ marginTop: 12 }}>
        <div className="card-title" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <FileTextIcon /> Transcript
        </div>
        {loading && <div className="muted">Loading…</div>}
        {err && <div className="muted" style={{ color: 'var(--danger)' }}>Error: {err}</div>}
        {!loading && !err && (
          transcript ? (
            <pre className="summary-body" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{transcript}</pre>
          ) : (
            <div className="muted" style={{ marginTop: 8 }}>No transcript available.</div>
          )
        )}
      </div>
    </div>
  )
}
