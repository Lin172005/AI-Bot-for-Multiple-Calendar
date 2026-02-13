import React from 'react'

export default function Header({ dark, setDark }) {
  return (
    <div className="header">
      <div className="brand">
        <div className="brand-title">AI Assistant Bot</div>
        <div className="brand-actions">
          <label className="toggle">
            <input type="checkbox" checked={dark} onChange={(e) => setDark(e.target.checked)} /> Dark mode
          </label>
        </div>
      </div>
    </div>
  )
}
