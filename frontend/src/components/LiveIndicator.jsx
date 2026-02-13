import React from 'react'

export default function LiveIndicator({ meetingTitle, time, attendeesCount, onView }) {
  return (
    <div className="card live-card accent-bar" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <div className="live-ring" aria-hidden />
      <div className="live-label">Live</div>
      <div className="muted" style={{ marginLeft: 8 }}>
        {meetingTitle || 'Meeting'} · {time || 'Now'} · {attendeesCount ?? 0} attendees
      </div>
      <div style={{ marginLeft: 'auto' }}>
        <button className="btn btn-success" onClick={onView}>View</button>
      </div>
    </div>
  )
}
