import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function fmtWhen(iso) {
  try { return new Date(iso).toLocaleString() } catch { return iso || '' }
}
function cleanSummary(s) {
  if (!s) return ''

  let t = s

  // Remove horizontal rules
  t = t.replace(/^(\*{3,}|-{3,}|_{3,})\s*$/gm, '')

  // Convert section labels into real markdown headings
  t = t.replace(/^Overview:\s*$/gim, '## Overview')
  t = t.replace(/^Key Discussion Points:\s*$/gim, '## Key Discussion Points')
  t = t.replace(/^Action Items:\s*$/gim, '## Action Items')
  t = t.replace(/^Decisions Made:\s*$/gim, '## Decisions Made')

  // Normalize bullets
  t = t.replace(/^\s*[-•]\s+/gm, '- ')

  // Cleanup spacing
  t = t.replace(/[\t ]+$/gm, '')
  t = t.replace(/\n{3,}/g, '\n\n')

  return t.trim()
}

export default function PastMeetingsSection({ backendUrl, session, days = 30, provider, providerToken }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [hideDeleted, setHideDeleted] = useState(true)
  const [selectedDays, setSelectedDays] = useState(String(days))
  const [busyIds, setBusyIds] = useState({})
  const [actionErr, setActionErr] = useState('')
  const [transcripts, setTranscripts] = useState({})
  const [transcriptBusy, setTranscriptBusy] = useState({})

  useEffect(() => {
    if (!session) return
    ;(async () => {
      setLoading(true); setErr('')
      try {
        const verify = hideDeleted && provider === 'google' && providerToken ? '&verify_calendar=true' : ''
        const res = await fetch(`${backendUrl}/meetings/past?days=${encodeURIComponent(selectedDays)}&limit=200&order=desc${verify}`, {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
            ...(verify ? { 'x-provider-token': providerToken } : {}),
          }
        })
        const data = await res.json().catch(() => ({}))
        if (!res.ok) throw new Error(data.detail || JSON.stringify(data))
        const sorted = (Array.isArray(data) ? data : []).sort((a, b) => {return new Date(b.start_time) - new Date(a.start_time)})  
        
        setItems(sorted)
      } catch (e) {
        setErr(String(e.message || e))
      } finally {
        setLoading(false)
      }
    })()
  }, [backendUrl, session, selectedDays, hideDeleted, provider, providerToken])

  if (!session) return null

  return (
    <section className="card">
      <div className="card-title">Past meetings</div>
      <div className="card-meta">{selectedDays === 'all' || selectedDays === '0' ? 'All time' : `Last ${selectedDays} days`} with meeting links</div>
      <div className="controls" style={{marginTop:8}}>
        <label style={{display:'inline-flex', alignItems:'center', gap:6}}>
          <input type="checkbox" checked={hideDeleted} onChange={(e) => setHideDeleted(e.target.checked)} />
          Hide items deleted in Google
        </label>
        <div style={{display:'inline-flex', alignItems:'center', gap:6, marginLeft:16}}>
          <span className="muted">Range:</span>
          <select value={selectedDays} onChange={(e) => setSelectedDays(e.target.value)}>
            <option value="7">7 days</option>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="all">All</option>
          </select>
        </div>
      </div>

      {loading && <div className="muted" style={{marginTop:8}}>Loading…</div>}
      {err && <div className="muted" style={{color:'var(--danger)', marginTop:8}}>Error: {err}</div>}
      {!loading && !err && items.length === 0 && (
        <div className="empty" style={{marginTop:12}}>No past meetings found.</div>
      )}

      <div className="summary-list">
        {items.map((m, i) => (
          <div key={(m.event_id || m.attendee_bot_id || i) + ''} className="summary-card">
            <div className="event-title">{m.title || 'Untitled meeting'}</div>
            <div className="summary-when">{fmtWhen(m.start_time)}</div>
            {m.meet_link && (
              <div className="muted" style={{marginBottom:8, overflowWrap:'anywhere'}}>
                Meet: <a href={m.meet_link} target="_blank" rel="noreferrer">{m.meet_link}</a>
              </div>
            )}
            <div style={{display:'flex', gap:8, flexWrap:'wrap', margin:'6px 0'}}>
              <button
                className="btn btn-sm"
                disabled={!m.attendee_bot_id || !!busyIds[m.attendee_bot_id]}
                onClick={async () => {
                  if (!m.attendee_bot_id) return alert('No bot id associated with this meeting')
                  setActionErr('')
                  setBusyIds((b) => ({ ...b, [m.attendee_bot_id]: true }))
                  try {
                    const res = await fetch(`${backendUrl}/bots/summarize`, {
                      method: 'POST',
                      headers: {
                        'Content-Type': 'application/json',
                        Authorization: `Bearer ${session.access_token}`,
                      },
                      body: JSON.stringify({ bot_id: m.attendee_bot_id }),
                    })
                    const data = await res.json().catch(() => ({}))
                    if (!res.ok) {
                      setActionErr('Summarize failed: ' + (data.detail ? JSON.stringify(data.detail) : JSON.stringify(data)))
                    } else {
                      const summaryText = data.summary || ''
                      setItems((prev) => prev.map((it) => (
                        (it.event_id === m.event_id) ? { ...it, summary: summaryText } : it
                      )))
                    }
                  } catch (e) {
                    setActionErr('Summarize error: ' + String(e.message || e))
                  } finally {
                    setBusyIds((b) => ({ ...b, [m.attendee_bot_id]: false }))
                  }
                }}
              >{busyIds[m.attendee_bot_id] ? 'Summarizing…' : 'Summarize now'}</button>

              <button
                className="btn btn-sm btn-secondary"
                disabled={!m.attendee_bot_id || !!busyIds[m.attendee_bot_id]}
                title="Ingest structured summary units into Supabase pgvector"
                onClick={async () => {
                  setActionErr('')
                  setBusyIds((b) => ({ ...b, [m.attendee_bot_id]: true }))
                  try {
                    // Resolve bot id from Supabase if not present (avoids in-memory reset issues)
                    let botId = m.attendee_bot_id
                    if (!botId) {
                      const params = new URLSearchParams()
                      if (m.event_id) params.set('event_id', m.event_id)
                      if (m.meet_link) params.set('meet_link', m.meet_link)
                      const r0 = await fetch(`${backendUrl}/meetings/resolve-bot-id?${params.toString()}`, {
                        method: 'GET',
                        headers: { Authorization: `Bearer ${session.access_token}` },
                      })
                      const j0 = await r0.json().catch(() => ({}))
                      if (!r0.ok || !j0.bot_id) {
                        setActionErr('Could not resolve bot id for this meeting.')
                        return
                      }
                      botId = j0.bot_id
                    }

                    const res = await fetch(`${backendUrl}/rag/ingest-summary-units`, {
                      method: 'POST',
                      headers: {
                        'Content-Type': 'application/json',
                        Authorization: `Bearer ${session.access_token}`,
                      },
                      body: JSON.stringify({ event_id: m.event_id }),
                    })
                    const data = await res.json().catch(() => ({}))
                    if (!res.ok || !data.ok) {
                      setActionErr('Ingest failed: ' + (data.detail ? JSON.stringify(data.detail) : JSON.stringify(data)))
                    } else {
                      const inserted = data.inserted ?? 0
                      setActionErr(`Ingested ${inserted} units into pgvector.`)
                    }
                  } catch (e) {
                    setActionErr('Ingest error: ' + String(e.message || e))
                  } finally {
                    setBusyIds((b) => ({ ...b, [m.attendee_bot_id]: false }))
                  }
                }}
              >{busyIds[m.attendee_bot_id] ? 'Ingesting…' : 'Ingest'}</button>

              <button
                className="btn btn-sm btn-ghost"
                disabled={!m.attendee_bot_id || !!transcriptBusy[m.attendee_bot_id]}
                title="Show meeting transcript"
                onClick={async () => {
                  if (!m.attendee_bot_id) return alert('No bot id associated with this meeting')
                  setTranscriptBusy((b) => ({ ...b, [m.attendee_bot_id]: true }))
                  try {
                    const res = await fetch(`${backendUrl}/bots/${encodeURIComponent(m.attendee_bot_id)}/transcript?format=text`, {
                      method: 'GET',
                      headers: { Authorization: `Bearer ${session.access_token}` },
                    })
                    const data = await res.json().catch(() => ({}))
                    if (!res.ok) {
                      setActionErr('Transcript fetch failed: ' + (data.detail ? JSON.stringify(data.detail) : JSON.stringify(data)))
                    } else {
                      const txt = data.text || ''
                      setTranscripts((t) => ({ ...t, [m.event_id]: txt }))
                    }
                  } catch (e) {
                    setActionErr('Transcript error: ' + String(e.message || e))
                  } finally {
                    setTranscriptBusy((b) => ({ ...b, [m.attendee_bot_id]: false }))
                  }
                }}
              >{transcriptBusy[m.attendee_bot_id] ? 'Loading…' : 'Transcript'}</button>
            </div>
            {actionErr && <div className="muted" style={{color:'var(--danger)'}}>{actionErr}</div>}
            <details>
              <summary className="btn btn-ghost">Summary</summary>
              <div className="summary-body markdown" >
                {m.summary ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanSummary(m.summary)}</ReactMarkdown>
                ) : (
                  <span className="muted">No summary yet.</span>
                )}
              </div>
            </details>
            {transcripts[m.event_id] && (
              <details style={{marginTop:8}}>
                <summary className="btn btn-ghost">Transcript</summary>
                <pre className="summary-body" style={{marginTop:8, whiteSpace:'pre-wrap'}}>{transcripts[m.event_id]}</pre>
              </details>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
