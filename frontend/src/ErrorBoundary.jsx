import React from 'react'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  componentDidCatch(error, info) {
    console.error('UI error:', error, info)
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ maxWidth: 900, margin: '40px auto', padding: 16 }}>
          <h2>Something went wrong</h2>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{String(this.state.error?.message || this.state.error)}</pre>
          <p className="muted">Open DevTools console for details.</p>
        </div>
      )
    }
    return this.props.children
  }
}