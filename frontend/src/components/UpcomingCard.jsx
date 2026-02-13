import React, { useEffect, useState } from 'react'
import { CalendarIcon, ClockIcon, UsersIcon } from './Icon.jsx'
import { backendFetch } from '../api.js'

function PlannedMessagesPanel({ session, backendUrl, event }) {
  const [items, setItems] = useState([])
  const [text, setText] = useState('')
  const [trigger, setTrigger] = useState('keyword') // 'keyword' | 'scheduled' | 'offset'
  const [scheduledAt, setScheduledAt] = useState('')
  const [offsetMinutes, setOffsetMinutes] = useState(5)
  const [keywords, setKeywords] = useState('')
  const [loading, setLoading] = useState(false)

  const load = async () => {
    const params = new URLSearchParams({ event_id: event.event_id })
    const res = await backendFetch(`/planned-messages?${params}`, { session })
    const data = await res.json().catch(() => ({}))
    setItems(Array.isArray(data.items) ? data.items : [])
  }
  useEffect(() => { load() }, [])

  const add = async () => {
    if (!text.trim()) return alert('Type something first')
    setLoading(true)
    try {
      // Convert scheduledAt (local) to ISO UTC for backend
      let scheduledIso = undefined
      if (trigger === 'scheduled' && scheduledAt) {
        try { scheduledIso = new Date(scheduledAt).toISOString() } catch {}
      }
      const body = {
        event_id: event.event_id,
        meet_link: event.meet_link,
        text,
        trigger_type: trigger,
        scheduled_at: scheduledIso || undefined,
        offset_minutes: trigger === 'offset' ? Number(offsetMinutes) : undefined,
        keywords: trigger === 'keyword' ? keywords.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
      }
      const res = await backendFetch('/planned-messages', { session, headers: { 'Content-Type': 'application/json' }, method: 'POST', body: JSON.stringify(body) })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) return alert('Add failed: ' + JSON.stringify(data))
      setText(''); setKeywords(''); setScheduledAt(''); setOffsetMinutes(5); setTrigger('keyword')
      load()
    } finally { setLoading(false) }
  }

  const del = async (id) => {
    const res = await backendFetch(`/planned-messages/${encodeURIComponent(id)}`, { session, method: 'DELETE' })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return alert('Delete failed: ' + JSON.stringify(data))
    load()
  }

  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div className="card-title">Questions / Say Something</div>
      <div style={{ display: 'grid', gap: 8 }}>
        <textarea className="textarea" rows={3} placeholder="Type what you want the bot to say or ask" value={text} onChange={(e) => setText(e.target.value)} />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <label>
            Trigger
            <select className="input" style={{ marginLeft: 6 }} value={trigger} onChange={(e) => setTrigger(e.target.value)}>
              <option value="keyword">Keyword cues</option>
              <option value="scheduled">Scheduled time</option>
              <option value="offset">Relative offset</option>
            </select>
          </label>
          {trigger === 'keyword' && (
            <input className="input" style={{ minWidth: 220 }} placeholder="Keywords (comma-separated)" value={keywords} onChange={(e) => setKeywords(e.target.value)} />
          )}
          {trigger === 'scheduled' && (
            <input className="input" type="datetime-local" value={scheduledAt} onChange={(e) => setScheduledAt(e.target.value)} />
          )}
          {trigger === 'offset' && (
            <input className="input" type="number" min={0} max={240} value={offsetMinutes} onChange={(e) => setOffsetMinutes(e.target.value)} placeholder="Minutes after start" />
          )}
          <button className="btn btn-primary" disabled={loading} onClick={add}>{loading ? 'Adding…' : 'Add'}</button>
        </div>
        {!!items.length && (
          <div className="card-meta small" style={{ display: 'grid', gap: 6 }}>
            {items.map((it) => (
              <div key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="badge">{it.trigger_type}</span>
                <span style={{ flex: 1 }}>{it.text}</span>
                {it.status === 'posted' ? (
                  <span className="muted">posted</span>
                ) : (
                  <button className="btn" onClick={() => del(it.id)}>Delete</button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function UpcomingCard({ e, isScheduled, onAddBot, session, backendUrl }) {
  const start = e.start_time ? new Date(e.start_time) : null
  const dateStr = start ? start.toLocaleDateString() : '-'
  const timeStr = start ? start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '-'
  const attendees = e.attendees || []
  const shown = attendees.slice(0, 2).map((a) => a.displayName || a.email || 'Guest')
  const rest = Math.max(0, attendees.length - shown.length)
  return (
    <div className="card upcoming-card accent-bar">
      <div className="event-title">{e.title || 'Untitled meeting'}</div>
      <div className="muted" style={{ display: 'flex', gap: 16, marginTop: 6 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><CalendarIcon /> {dateStr} {timeStr}</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><ClockIcon /> 30–60 min</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><UsersIcon /> {shown.join(', ')}{rest ? ` +${rest}` : ''}</span>
      </div>
      <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end' }}>
        {isScheduled ? (
          <span className="badge badge-success">Bot scheduled</span>
        ) : (
          <button className="btn btn-accent" onClick={onAddBot}>Add Bot</button>
        )}
      </div>
      <PlannedMessagesPanel session={session} backendUrl={backendUrl} event={e} />
    </div>
  )
}
