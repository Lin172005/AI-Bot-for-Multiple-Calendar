import React from 'react'
import { CalendarIcon, FileTextIcon, RadioIcon, LightbulbIcon, SettingsIcon } from './Icon.jsx'

export default function Sidebar({ active, setActive, userEmail, liveCount = 0 }) {
  const initials = (userEmail || 'A').split('@')[0].slice(0, 2).toUpperCase()
  const NavItem = ({ id, label, badge, icon }) => (
    <div
      className={active === id ? 'nav-item active' : 'nav-item'}
      onClick={() => setActive(id)}
      role="button"
      aria-label={label}
    >
      <span className="nav-icon">{icon}</span>
      <span>{label}</span>
      {badge ? <span className="nav-badge">{badge}</span> : null}
    </div>
  )
  return (
    <aside className="sidebar">
      <div className="sidebar-inner">
        <div className="sidebar-logo">
          <div className="icon" />
          <div className="name">AI Bot</div>
        </div>
        <nav className="sidebar-nav">
          <NavItem id="upcoming" label="Upcoming" icon={<CalendarIcon />} />
          <NavItem id="past" label="Past Meetings" icon={<FileTextIcon />} />
          <NavItem id="live" label="Live" icon={<RadioIcon />} badge={liveCount > 0 ? String(liveCount) : ''} />
          <NavItem id="knowledge" label="Knowledge" icon={<LightbulbIcon />} />
          <NavItem id="settings" label="Settings" icon={<SettingsIcon />} />
        </nav>
      </div>
      <div className="sidebar-footer">
        <div className="avatar" title={userEmail}>{initials}</div>
        <div style={{ fontSize: 12 }}>
          <div style={{ fontWeight: 700 }}>JD</div>
          <div className="muted" style={{ fontSize: 11 }}>{userEmail || 'user'}</div>
        </div>
      </div>
    </aside>
  )
}

