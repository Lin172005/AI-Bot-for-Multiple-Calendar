import React, { useEffect, useState } from 'react'
import { ChevronRightIcon } from './Icon.jsx'

export default function RecentPast({ backendUrl, session, provider, providerToken, onOpen }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!session) return
    ;(async () => {
      setLoading(true); setErr('')
      try {
        const verify = provider === 'google' && providerToken ? '&verify_calendar=true' : ''
        const res = await fetch(`${backendUrl}/meetings/past?days=30&limit=5&order=desc${verify}`, {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
            ...(verify ? { 'x-provider-token': providerToken } : {}),
          }
        })
        const data = await res.json().catch(() => ([]))
        setItems(Array.isArray(data) ? data : [])
      } catch (e) {
        setErr(String(e.message || e))
      } finally {
        setLoading(false)
      }
    })()
  }, [backendUrl, session, provider, providerToken])

  if (!session) return null

  const cleanLine = (s) => {
    if (!s) return ''
    return s.replace(/[\t ]+$/gm, '').replace(/^\s+/gm, '').trim()
  }

  return (
    <div className="card">
      {loading && <div className="muted">Loading…</div>}
      {err && <div className="muted" style={{ color: 'var(--danger)' }}>Error: {err}</div>}
      {!loading && !err && items.length === 0 && (
        <div className="empty" style={{ marginTop: 8 }}>No recent meetings.</div>
      )}
      <div>
        {items.map((m, i) => (
          <div key={(m.event_id || i) + ''} className="row-card" onClick={() => onOpen(m)} role="button">
            <div>
              <div className="row-title">{m.title || 'Untitled meeting'}</div>
              <div className="row-sub muted">{cleanLine((m.summary || '').split('\n').find((ln) => !!ln) || '') || 'No summary yet.'}</div>
            </div>
            <div style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <div className="row-meta muted">{new Date(m.start_time).toLocaleDateString()}</div>
              <ChevronRightIcon />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
