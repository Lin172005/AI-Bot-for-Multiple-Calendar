import React from 'react'

export default function LoginHero({ onGoogle, onMicrosoft }) {
  return (
    <div className="login-wrap">
      <div className="login-card">
        <div style={{ fontSize: 50 }}>📅</div>
        <div className="login-title">AI Meeting Assistant</div>
        <div className="login-sub">Connect your calendar and manage meetings with one click.</div>
        <div style={{ display: 'flex', flexDirection: 'row', gap: 12, marginTop: 36 }}>
          <button className="btn btn-google" onClick={onGoogle}>Sign in with Google</button>
          <button className="btn btn-microsoft" onClick={onMicrosoft}>Sign in with Microsoft</button>
        </div>
      </div>
    </div>
  )
}
