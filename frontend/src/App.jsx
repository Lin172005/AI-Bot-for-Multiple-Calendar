import React, { useEffect, useState } from 'react'
import { createClient } from '@supabase/supabase-js'
import Header from './components/Header.jsx'
import Sidebar from './components/Sidebar.jsx'
import LiveIndicator from './components/LiveIndicator.jsx'
import LiveMeetingCard from './components/LiveMeetingCard.jsx'
import UpcomingCard from './components/UpcomingCard.jsx'
import RecentPast from './components/RecentPast.jsx'
import PastMeetingDetail from './components/PastMeetingDetail.jsx'
import LiveView from './components/LiveView.jsx'
import KnowledgeView from './components/KnowledgeView.jsx'
import Toolbar from './components/Toolbar.jsx'
import LoginHero from './components/LoginHero.jsx'
// import QASection from './components/QASection.jsx'
// import EventCard from './components/EventCard.jsx'
import PastMeetingsSection from './components/PastMeetingsSection.jsx'

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
)

export default function App() {
  const backendUrl = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8010'

  // Auth/session
  const [session, setSession] = useState(null)
  const [googleToken, setGoogleToken] = useState(null) // provider access token (Google or Microsoft)
  const [provider, setProvider] = useState(null) // 'google' | 'azure' | null
  const userEmail = session?.user?.email || ''

  // Theme
  const [dark, setDark] = useState(false)

  // Events and controls
  const [events, setEvents] = useState([])
  const [eventsLoading, setEventsLoading] = useState(false)
  const [linksOnly, setLinksOnly] = useState(true)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [refreshMs, setRefreshMs] = useState(30000)
  const [showQA, setShowQA] = useState(false)
  const [activeTab, setActiveTab] = useState('upcoming') // 'upcoming' | 'past' | 'live' | 'knowledge' | 'settings' | 'pastDetail'
  const [selectedPast, setSelectedPast] = useState(null)

  // Bots and summaries
  const [botMap, setBotMap] = useState({}) // event_id -> bot_id
  const [summaryLoading, setSummaryLoading] = useState({})
  const [summaries, setSummaries] = useState({})
  const [botStateMap, setBotStateMap] = useState({}) // event_id -> state ('ended'|'unknown')

  // Calendars (optional diagnostics)
  const [calendars, setCalendars] = useState([])

  // Q&A (RAG) state
  const [qaQuestion, setQaQuestion] = useState('')
  const [qaSources, setQaSources] = useState({ transcript: true, email: true })
  const [qaAnswer, setQaAnswer] = useState('')
  const [qaContexts, setQaContexts] = useState([])
  const [qaLoading, setQaLoading] = useState(false)
  const [gmailLoading, setGmailLoading] = useState(false)

  // Initialize session and token
  useEffect(() => {
    // Handle OAuth code exchange if redirected back with ?code=...
    (async () => {
      const url = new URL(window.location.href)
      const hasCode = url.searchParams.get('code')
      const hasError = url.searchParams.get('error')
      if (hasError) {
        console.warn('OAuth error:', hasError, url.searchParams.get('error_description'))
      }
      if (hasCode) {
        try {
          const { data, error } = await supabase.auth.exchangeCodeForSession(window.location.href)
          if (error) {
            console.warn('exchangeCodeForSession error:', error.message)
          } else {
            // Clean the URL (remove code/state params) without reloading
            window.history.replaceState({}, document.title, window.location.origin + window.location.pathname)
          }
        } catch (e) {
          console.warn('exchangeCodeForSession exception:', e)
        }
      }
    })()

    let cleanup
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session)
      const prov = session?.user?.app_metadata?.provider || null
      setProvider(prov)
      const pt = session?.provider_token || session?.provider_token?.access_token
      if (pt) setGoogleToken(pt)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session)
      const prov = session?.user?.app_metadata?.provider || null
      setProvider(prov)
      const pt = session?.provider_token || session?.provider_token?.access_token
      setGoogleToken(pt || null)
    })
    cleanup = () => sub?.subscription?.unsubscribe()
    return cleanup
  }, [])

  const signInWithGoogle = async () => {
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: window.location.origin,
        flowType: 'pkce',
        scopes: [
          'https://www.googleapis.com/auth/calendar.readonly',
          'https://www.googleapis.com/auth/gmail.readonly',
        ].join(' '),
      },
    })
  }

  const signInWithMicrosoft = async () => {
    try {
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider: 'azure',
        options: {
          redirectTo: window.location.origin,
          flowType: 'pkce',
          scopes: [
            'openid',
            'offline_access',
            'email',
            'User.Read',
            'Calendars.Read',
          ].join(' '),
        },
      })
      if (error) throw error
      return data
    } catch (err) {
      const msg = String(err?.message || err)
      if (msg.toLowerCase().includes('unsupported provider') || msg.toLowerCase().includes('provider is not enabled')) {
        alert('Microsoft provider is not enabled in Supabase. In Supabase Dashboard → Authentication → Providers → Azure, enable it and add your Client ID and Secret. Then retry.')
      } else {
        alert('Microsoft sign-in failed: ' + msg)
      }
    }
  }

  const signOut = async () => {
    await supabase.auth.signOut()
    setSession(null)
    setGoogleToken(null)
    setProvider(null)
  }

  // Backend fallback
  const fetchEventsBackend = async () => {
    const res = await fetch(`${backendUrl}/events?links_only=${linksOnly ? 'true' : 'false'}`, {
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        ...(googleToken ? { 'x-provider-token': googleToken } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.error || 'Failed to fetch events')
    return Array.isArray(data) ? data : data.events || []
  }

  // Direct Google fetch across ALL calendars (primary + others)
  const fetchEventsGoogle = async () => {
    const now = new Date()
    const start = new Date(now.getTime() - 5 * 60 * 1000)
    const end = new Date(now.getTime() + 180 * 24 * 60 * 60 * 1000)

    // 1) Get calendar list
    const calRes = await fetch('https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=100', {
      headers: { Authorization: `Bearer ${googleToken}` },
    })
    const calData = await calRes.json().catch(() => ({}))
    if (!calRes.ok) throw new Error('Failed to list calendars: ' + JSON.stringify(calData))
    const cals = (calData.items || []).filter((c) => !c.deleted && c.accessRole)

    // Helper to extract URLs from text
    const extractFromText = (txt) => {
      if (!txt) return ''
      const urlRe = /(https?:\/\/[^\s)]+)/gi
      const all = (txt.match(urlRe) || [])
      if (!all.length) return ''
      // Prefer known meeting domains
      const priority = ['meet.google.com', 'zoom.us', 'teams.microsoft.com', 'teams.live.com', 'webex.com']
      for (const u of all) {
        if (priority.some((d) => u.includes(d))) return u
      }
      return all[0]
    }

    // 2) Pull upcoming events for each calendar
    const results = await Promise.allSettled(
      cals.map(async (cal) => {
        const params = new URLSearchParams({
          calendarId: cal.id,
          timeMin: start.toISOString(),
          timeMax: end.toISOString(),
          singleEvents: 'true',
          orderBy: 'startTime',
          maxResults: '250',
        })
        const evRes = await fetch(`https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(cal.id)}/events?${params}`, {
          headers: { Authorization: `Bearer ${googleToken}` },
        })
        const data = await evRes.json().catch(() => ({}))
        if (!evRes.ok) {
          return []
        }
        return (data.items || []).map((item) => {
          let meet = item.hangoutLink
          if (!meet && item.conferenceData && Array.isArray(item.conferenceData.entryPoints)) {
            const ep = item.conferenceData.entryPoints.find((p) => p.entryPointType === 'video' && p.uri)
            if (ep) meet = ep.uri
          }
          if (!meet) {
            meet = extractFromText(item.description) || extractFromText(item.location)
          }
          return {
            event_id: item.id,
            title: item.summary,
            start_time: item.start?.dateTime || item.start?.date,
            end_time: item.end?.dateTime || item.end?.date,
            meet_link: meet,
            calendar: { id: cal.id, summary: cal.summary },
          }
        })
      })
    )

    // 3) Flatten results and sort, dedupe per calendar
    const merged = results.flatMap((r) => (r.status === 'fulfilled' ? r.value : []))
    const key = (e) => `${e.event_id}::${e.calendar?.id || 'primary'}`
    const dedup = Array.from(new Map(merged.map((e) => [key(e), e])).values())
    dedup.sort((a, b) => new Date(a.start_time) - new Date(b.start_time))
    return dedup
  }

  // Direct Microsoft fetch across ALL calendars
  const fetchEventsMicrosoft = async () => {
    const now = new Date()
    const start = new Date(now.getTime() - 5 * 60 * 1000)
    const end = new Date(now.getTime() + 180 * 24 * 60 * 60 * 1000)

    // Use CalendarView for time-bounded query
    const params = new URLSearchParams({
      startDateTime: start.toISOString(),
      endDateTime: end.toISOString(),
      $top: '250',
      $orderby: 'start/dateTime',
    })
    const res = await fetch(`https://graph.microsoft.com/v1.0/me/calendarView?${params}`, {
      headers: { Authorization: `Bearer ${googleToken}` },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error('Failed to fetch Microsoft events: ' + JSON.stringify(data))
    const items = Array.isArray(data.value) ? data.value : []

    const stripHtml = (html) => (html || '').replace(/<[^>]*>/g, ' ')
    const extractFromText = (txt) => {
      if (!txt) return ''
      const urlRe = /(https?:\/\/[^\s)]+)/gi
      const all = (txt.match(urlRe) || [])
      if (!all.length) return ''
      const priority = ['teams.microsoft.com', 'teams.live.com', 'zoom.us', 'meet.google.com', 'webex.com']
      for (const u of all) {
        if (priority.some((d) => u.includes(d))) return u
      }
      return all[0]
    }

    return items.map((item) => {
      let meet = item?.onlineMeeting?.joinUrl || item?.onlineMeetingUrl || ''
      if (!meet) {
        meet = extractFromText(stripHtml(item?.body?.content)) || extractFromText(item?.bodyPreview) || extractFromText(item?.location?.displayName)
      }
      return {
        event_id: item.id,
        title: item.subject,
        start_time: item.start?.dateTime,
        end_time: item.end?.dateTime,
        meet_link: meet,
        calendar: { id: item.calendarId || 'default', summary: 'Microsoft' },
      }
    })
  }

  const fetchEvents = async () => {
    if (!session) return alert('Please sign in first')
    if (!googleToken) return alert('Google token not available yet; sign out and sign in again if needed')
    setEventsLoading(true)
    try {
  // Direct-first to guarantee events even if backend/CORS is blocked
  const items = provider === 'azure' ? await fetchEventsMicrosoft() : await fetchEventsGoogle()
      const filtered = linksOnly ? items.filter((i) => !!i.meet_link) : items
      setEvents(filtered)
      // Seed bot map from backend
      const links = Array.from(new Set(filtered.map((i) => i.meet_link).filter(Boolean)))
      if (links.length) {
        const res = await fetch(`${backendUrl}/bots/status`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${session.access_token}`,
          },
          body: JSON.stringify({ meet_links: links }),
        })
        const data = await res.json().catch(() => ({}))
        if (res.ok && data.status) {
          const map = {}
          const stateMap = {}
          for (const ev of filtered) {
            const bid = data.status[ev.meet_link]
            if (bid) map[ev.event_id] = bid
            const det = data.detail?.[ev.meet_link]
            if (det?.state) stateMap[ev.event_id] = det.state
          }
          setBotMap(map)
          setBotStateMap(stateMap)
        }
      }
    } catch (e) {
      console.warn('Direct fetch failed, trying backend fallback:', e)
      try {
        const items = await fetchEventsBackend()
        const filtered = linksOnly ? items.filter((i) => !!i.meet_link) : items
        setEvents(filtered)
        const links = Array.from(new Set(filtered.map((i) => i.meet_link).filter(Boolean)))
        if (links.length) {
          const res = await fetch(`${backendUrl}/bots/status`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${session.access_token}`,
            },
            body: JSON.stringify({ meet_links: links }),
          })
          const data = await res.json().catch(() => ({}))
          if (res.ok && data.status) {
            const map = {}
            const stateMap = {}
            for (const ev of filtered) {
              const bid = data.status[ev.meet_link]
              if (bid) map[ev.event_id] = bid
              const det = data.detail?.[ev.meet_link]
              if (det?.state) stateMap[ev.event_id] = det.state
            }
            setBotMap(map)
            setBotStateMap(stateMap)
          }
        }
      } catch (e2) {
        alert('Failed to fetch events: ' + e2.message)
      }
    } finally {
      setEventsLoading(false)
    }
  }

  // Load once on login
  useEffect(() => {
    if (session && googleToken) {
      fetchEvents()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, googleToken])

  const listCalendars = async () => {
    if (!session || !googleToken) return alert('Sign in first; token missing')
    const res = await fetch(`${backendUrl}/calendars`, {
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        'x-provider-token': googleToken,
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      alert('List calendars failed: ' + JSON.stringify(data))
      return
    }
    setCalendars(data.calendars || [])
  }

  const scheduleBot = async (evt) => {
    if (!session) return alert('Please sign in first')
    // Build WSS URL from a configured public base; Attendee requires wss://
    const publicBase = import.meta.env.VITE_WS_PUBLIC_URL || backendUrl
    let wsUrl = ''
    try {
      const u = new URL(publicBase)
      // Always force wss scheme
      wsUrl = `wss://${u.host}/attendee-websocket`
    } catch {}
    const hasValidWss = wsUrl.startsWith('wss://')
    const res = await fetch(`${backendUrl}/schedule-bot`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${session.access_token}`,
      },
      body: JSON.stringify({
        event_id: evt.event_id,
        title: evt.title,
        start_time: evt.start_time,
        meet_link: evt.meet_link,
        // Provide realtime audio websocket settings only when we have a valid wss:// URL
        ...(hasValidWss ? {
          websocket_settings: {
            audio: {
              url: wsUrl,
              sample_rate: 16000,
            }
          }
        } : {})
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      alert('Failed to schedule bot: ' + JSON.stringify(data))
      if (!hasValidWss) {
        alert('Tip: Set VITE_WS_PUBLIC_URL to your public https base (e.g., https ngrok URL). The WS URL must start with wss://')
      }
      return
    }
    const ar = data.attendee_response || {}
    const botId = data.bot_id || ar.id || ar.bot_id || ar.bot?.id
    if (botId) {
      setBotMap((m) => ({ ...m, [evt.event_id]: botId }))
    }
    alert(`Bot scheduled${botId ? ` (id: ${botId})` : ''}!`)
  }

  const removeBot = async (evt) => {
    if (!session) return alert('Please sign in first')
    const botId = botMap[evt.event_id]
    if (!botId) return alert('No bot id found for this event')
    const res = await fetch(`${backendUrl}/schedule-bot/${botId}`, {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${session.access_token}`,
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      alert('Failed to remove bot: ' + JSON.stringify(data))
      return
    }
    setBotMap((m) => {
      const copy = { ...m }
      delete copy[evt.event_id]
      return copy
    })
    alert('Bot removed')
  }

  const getSummary = async (evt) => {
    if (!session) return alert('Please sign in first')
    const botId = botMap[evt.event_id]
    if (!botId) return alert('No bot id found for this event')
    setSummaryLoading((m) => ({ ...m, [evt.event_id]: true }))
    try {
      const res = await fetch(`${backendUrl}/bots/summarize`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ bot_id: botId }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        alert('Failed to get summary: ' + JSON.stringify(data))
        return
      }
      const summaryText = data.summary || ''
      setSummaries((m) => ({ ...m, [evt.event_id]: summaryText }))
    } finally {
      setSummaryLoading((m) => ({ ...m, [evt.event_id]: false }))
    }
  }

  const ingestTranscriptForEvent = async (evt) => {
    if (!session) return alert('Please sign in first')
    const botId = botMap[evt.event_id]
    if (!botId) return alert('No bot id found for this event')
    const res = await fetch(`${backendUrl}/rag/ingest-bot`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${session.access_token}`,
      },
      body: JSON.stringify({ bot_id: botId, meeting_link: evt.meet_link }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      alert('Ingest failed: ' + JSON.stringify(data))
      return
    }
    alert(`Ingested ${data.ingested_chunks || 0} transcript chunks for this event.`)
  }

  const askQuestion = async () => {
    if (!session) return alert('Please sign in first')
    if (!qaQuestion.trim()) return alert('Type a question first')
    setQaLoading(true)
    setQaAnswer('')
    setQaContexts([])
    try {
      const sources = Object.entries(qaSources)
        .filter(([_, v]) => v)
        .map(([k]) => k)
      const body = { question: qaQuestion, sources }
      const res = await fetch(`${backendUrl}/rag/query`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify(body),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        alert('Q&A failed: ' + JSON.stringify(data))
        return
      }
      setQaAnswer(data.answer || '')
      setQaContexts(Array.isArray(data.contexts) ? data.contexts : [])
    } finally {
      setQaLoading(false)
    }
  }

  const ingestGmail = async () => {
    if (!session) return alert('Please sign in first')
    if (!googleToken) return alert('Google token not available yet; sign out and sign in again if needed')
    setGmailLoading(true)
    try {
      const res = await fetch(`${backendUrl}/rag/ingest-gmail`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
          'x-provider-token': googleToken,
        },
        body: JSON.stringify({ days: 30, max_messages: 5, label_ids: ['INBOX'] }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        alert('Gmail ingest failed: ' + JSON.stringify(data))
        return
      }
      alert(`Ingested ${data.ingested_messages || 0} messages (${data.ingested_chunks || 0} chunks).`)
    } finally {
      setGmailLoading(false)
    }
  }

  // Auto-refresh polling
  useEffect(() => {
    if (!autoRefresh || !session || !googleToken) return
    const id = setInterval(() => {
      fetchEvents()
    }, refreshMs)
    return () => clearInterval(id)
  }, [autoRefresh, refreshMs, session, googleToken])

  // Live badge count: simple heuristic - meetings starting within 30 minutes with bot scheduled
  const fallbackLiveWindowMs = 2 * 60 * 60 * 1000 // if end_time missing, consider live up to 2h after start
  const liveEvents = events.filter((e) => {
    const st = e.start_time ? new Date(e.start_time).getTime() : NaN
    const et = e.end_time ? new Date(e.end_time).getTime() : NaN
    const scheduled = !!botMap[e.event_id]
    const state = botStateMap[e.event_id]
    const now = Date.now()
    if (!scheduled || Number.isNaN(st)) return false
    // If webhook reported end, do not show as live
    if (state === 'ended') return false
    // Prefer precise end_time window; otherwise fallback to 2h window after start
    if (!Number.isNaN(et)) {
      return st <= now && now < et
    }
    return st <= now && (now - st) <= fallbackLiveWindowMs
  })
  const liveCount = liveEvents.length
  const currentLive = liveEvents[0] || null

  // Exclude ended meetings from upcoming UI section
  const upcomingEvents = events.filter((e) => {
    const now = Date.now()
    const et = e.end_time ? new Date(e.end_time).getTime() : NaN
    const state = botStateMap[e.event_id]
    // If backend/UI marked as ended or end_time has passed, do not show in upcoming
    if (state === 'ended') return false
    if (!Number.isNaN(et) && now >= et) return false
    return true
  })

  // Auto-finalize when meetings end (fallback if webhooks aren’t configured)
  const finalizeTriggeredRef = React.useRef(new Set())
  const checkAndFinalize = async () => {
    if (!session) return
    const now = Date.now()
    for (const e of events) {
      const botId = botMap[e.event_id]
      if (!botId) continue
      const st = e.start_time ? new Date(e.start_time).getTime() : NaN
      const et = e.end_time ? new Date(e.end_time).getTime() : NaN
      const shouldFinalize = (!Number.isNaN(et) && now >= et) || (!Number.isNaN(st) && Number.isNaN(et) && (now - st) >= (75 * 60 * 1000))
      if (shouldFinalize && !finalizeTriggeredRef.current.has(botId)) {
        try {
          const res = await fetch(`${backendUrl}/bots/${encodeURIComponent(botId)}/finalize`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${session.access_token}` },
          })
          // Best-effort; ignore errors for now
        } catch {}
        finalizeTriggeredRef.current.add(botId)
        // Immediately mark as ended locally to suppress LIVE NOW
        setBotStateMap((m) => ({ ...m, [e.event_id]: 'ended' }))
      }
    }
  }

  // Run check when events change and periodically
  useEffect(() => {
    if (session) {
      checkAndFinalize()
      const id = setInterval(checkAndFinalize, 60000)
      return () => clearInterval(id)
    }
  }, [session, events, botMap, backendUrl])

  return (
  <div className={dark ? 'dark app-bg' : 'app-bg'}>

    {!session ? (
      // ================= LOGIN SCREEN =================
      <main className="main-area">
        <Header dark={dark} setDark={setDark} />

        <details style={{ marginBottom: 12 }}>
          <summary>Debug</summary>
          <div>Has session: {session ? 'yes' : 'no'}</div>
          <div>Provider: {provider || '-'}</div>
          <div>Has provider token: {googleToken ? 'yes' : 'no'}</div>
        </details>

        <LoginHero
          onGoogle={signInWithGoogle}
          onMicrosoft={signInWithMicrosoft}
        />
      </main>

    ) : (
      // ================= LOGGED IN LAYOUT =================
      <div className="app-layout">

        <Sidebar
          active={activeTab}
          setActive={setActiveTab}
          userEmail={userEmail}
          liveCount={liveCount}
        />

        <main className="main-area">
          <Header dark={dark} setDark={setDark} />

          <details style={{ marginBottom: 12 }}>
            <summary>Debug</summary>
            <div>Has session: {session ? 'yes' : 'no'}</div>
            <div>Provider: {provider || '-'}</div>
            <div>Has provider token: {googleToken ? 'yes' : 'no'}</div>
          </details>

          <div className="muted" style={{ marginBottom: 6 }}>
            Signed in as <b>{userEmail}</b>
          </div>

          <Toolbar
            autoRefresh={autoRefresh}
            setAutoRefresh={setAutoRefresh}
            refreshMs={refreshMs}
            setRefreshMs={setRefreshMs}
            linksOnly={linksOnly}
            setLinksOnly={setLinksOnly}
            itemsCount={events.length}
            eventsLoading={eventsLoading}
            onFetchEvents={fetchEvents}
            onAskAI={() => { setActiveTab('knowledge'); setShowQA(true); }}
            signOut={signOut}
          />

          {/* Sections controlled by sidebar nav */}
          {activeTab === 'live' && (
            <div className="section" style={{ animation: 'fadeIn 260ms ease' }}>
              {currentLive ? (
                <LiveView
                  title={currentLive.title || 'Live meeting'}
                  startedLabel={`Started ${new Date(currentLive.start_time).toLocaleTimeString()}`}
                  participantsCount={(currentLive.attendees?.length || 0)}
                  botId={botMap[currentLive.event_id] || null}
                  backendUrl={backendUrl}
                  authToken={session?.access_token || null}
                />
              ) : (
                <div className="empty">
                  <div className="icon">🟢</div>
                  No live meeting right now.
                </div>
              )}
            </div>
          )}

          {activeTab === 'knowledge' && (
            <div className="section" style={{ animation: 'fadeIn 260ms ease' }}>
              <KnowledgeView
                qaQuestion={qaQuestion}
                setQaQuestion={setQaQuestion}
                qaSources={qaSources}
                setQaSources={setQaSources}
                qaLoading={qaLoading}
                askQuestion={askQuestion}
                qaAnswer={qaAnswer}
                qaContexts={qaContexts}
              />
            </div>
          )}

          {activeTab === 'upcoming' && (
            <div className="section" style={{ marginTop: 12, animation: 'slideUp 220ms ease' }}>
              <div className="section-label">LIVE NOW</div>
              {currentLive ? (
                <LiveMeetingCard
                  title={currentLive.title || 'Live meeting'}
                  timeLabel={'In progress'}
                  attendeesCount={(currentLive.attendees?.length || 0)}
                  onView={() => setActiveTab('live')}
                />
              ) : (
                <div className="empty">
                  <div className="icon">📅</div>
                  No live meeting at the moment.
                </div>
              )}

              <div className="section-label" style={{ marginTop: 14 }}>UPCOMING</div>
              {!upcomingEvents.length ? (
                <div className="empty">
                  <div className="icon">📅</div>
                  No upcoming items yet.
                </div>
              ) : (
                <div className="cards">
                  {upcomingEvents.slice(0, 3).map((e) => (
                    <UpcomingCard
                      key={e.event_id}
                      e={e}
                      isScheduled={!!botMap[e.event_id]}
                      onAddBot={() => scheduleBot(e)}
                      session={session}
                      backendUrl={backendUrl}
                    />
                  ))}
                </div>
              )}

              <div className="section-label" style={{ marginTop: 14 }}>RECENT</div>
              <RecentPast
                backendUrl={backendUrl}
                session={session}
                provider={provider}
                providerToken={googleToken}
                onOpen={(m) => { setSelectedPast(m); setActiveTab('pastDetail') }}
              />
            </div>
          )}

          {activeTab === 'past' && (
            <div className="section" style={{ marginTop: 12, animation: 'slideUp 220ms ease' }}>
              <PastMeetingsSection
                backendUrl={backendUrl}
                session={session}
                days={90}
                provider={provider}
                providerToken={googleToken}
              />
            </div>
          )}

          {activeTab === 'pastDetail' && (
            <div className="section" style={{ marginTop: 12, animation: 'fadeIn 220ms ease' }}>
              <PastMeetingDetail
                item={selectedPast}
                onBack={() => setActiveTab('upcoming')}
                backendUrl={backendUrl}
                authToken={session?.access_token || null}
              />
            </div>
          )}

        </main>
      </div>
    )}

  </div>
)

}
