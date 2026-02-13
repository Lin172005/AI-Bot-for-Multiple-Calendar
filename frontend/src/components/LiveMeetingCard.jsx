import React from 'react'
import { UsersIcon, ClockIcon } from './Icon.jsx'

export default function LiveMeetingCard({ title, timeLabel, attendeesCount = 0, onView }) {
  return (
    <div className="card live-card ring-success" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <div className="live-ring" aria-hidden />
      <div className="live-label">LIVE</div>
      <div style={{ fontWeight: 700 }}>{title}</div>
      <div className="muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 10, marginLeft: 12 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><ClockIcon /> {timeLabel}</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><UsersIcon /> {attendeesCount} attendees</span>
      </div>
      <div style={{ marginLeft: 'auto' }}>
        <button className="btn btn-success" onClick={onView}>View</button>
      </div>
    </div>
  )
}
