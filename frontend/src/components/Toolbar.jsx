import React from 'react'

export default function Toolbar({
  autoRefresh,
  setAutoRefresh,
  refreshMs,
  setRefreshMs,
  linksOnly,
  setLinksOnly,
  itemsCount,
  eventsLoading,
  onFetchEvents,
  onAskAI,
  signOut,
}) {
  return (
    <div className="toolbar" style={{ marginBottom: 12 }}>
      <button className="btn btn-primary" onClick={onFetchEvents} disabled={eventsLoading} title="Fetch Upcoming Events">
        {eventsLoading ? <span className="spinner" aria-label="loading" /> : 'Fetch Upcoming Events'}
      </button>
      <label className="toggle">
        <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} /> Auto refresh
      </label>
      {autoRefresh && (
        <input className="input" title="Refresh interval (ms)" type="number" min={5000} step={1000} value={refreshMs} onChange={(e) => setRefreshMs(parseInt(e.target.value || '0', 10))} />
      )}
      <label className="toggle">
        <input type="checkbox" checked={linksOnly} onChange={(e) => setLinksOnly(e.target.checked)} /> Links only
      </label>
      <button className="btn btn-accent" onClick={onAskAI} title="Open AI Q&A">Ask AI</button>
      <div className="spacer" />
      <div className="badge badge-gray">Items: {itemsCount}</div>
      <button className="btn" onClick={signOut}>Sign out</button>
    </div>
  )
}
