import React, { useEffect, useRef, useState } from 'react'

// Simple Live Transcript viewer using SSE
// Props:
// - botId: Attendee bot id (e.g., "bot_abc123")
// - apiBase: optional override of backend base URL (defaults to same origin)
export default function LiveTranscript({ botId, apiBase, authToken }) {
  const [lines, setLines] = useState([])
  const [connected, setConnected] = useState(false)
  const esRef = useRef(null)

  useEffect(() => {
    if (!botId) return
    const base = apiBase || ''
  const params = new URLSearchParams()
  if (authToken) params.set('access_token', authToken)
  const qs = params.toString()
  const url = `${base}/bots/${encodeURIComponent(botId)}/transcript/stream${qs ? `?${qs}` : ''}`
  const es = new EventSource(url)
    esRef.current = es

    es.addEventListener('open', () => setConnected(true))
    es.addEventListener('error', () => setConnected(false))
    es.addEventListener('utterance', (evt) => {
      try {
        const data = JSON.parse(evt.data)
        const speaker = data.speaker_name || data.speaker || 'Unknown'
        const text = (data.transcription?.transcript) || data.text || ''
        const tsMs = data.timestamp_ms || null
        setLines((prev) => [
          ...prev,
          {
            key: data.id || data.message_uuid || `${speaker}-${tsMs || Date.now()}-${prev.length}`,
            speaker,
            text,
            tsMs,
          },
        ])
      } catch (e) {
        // ignore parse errors
      }
    })

    return () => {
      try { es.close() } catch {}
      esRef.current = null
      setConnected(false)
    }
  }, [botId, apiBase])

  if (!botId) return null

  return (
    <div style={{ border: '1px solid #eee', borderRadius: 8, padding: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ margin: 0 }}>Live Transcript</h3>
        <span style={{ fontSize: 12, color: connected ? 'green' : '#999' }}>{connected ? 'live' : 'disconnected'}</span>
      </div>
      <div style={{ marginTop: 8, maxHeight: 280, overflowY: 'auto', background: '#fafafa', padding: 8, borderRadius: 6 }}>
        {lines.length === 0 ? (
          <div style={{ color: '#777', fontSize: 14 }}>Waiting for speech…</div>
        ) : (
          lines.map((ln) => (
            <div key={ln.key} style={{ marginBottom: 6 }}>
              <strong>{ln.speaker}:</strong> {ln.text}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
