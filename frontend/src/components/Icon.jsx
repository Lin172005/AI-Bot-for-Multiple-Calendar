import React from 'react'

const base = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
}

export function CalendarIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  )
}

export function FileTextIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M16 13H8M16 17H8" />
    </svg>
  )
}

export function RadioIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.5 12a7.5 7.5 0 1 1-15 0a7.5 7.5 0 0 1 15 0z" />
    </svg>
  )
}

export function LightbulbIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M9 18h6" />
      <path d="M10 22h4" />
      <path d="M12 2a7 7 0 0 0-7 7c0 2.6 1.4 3.9 3 5l1 1h6l1-1c1.6-1.1 3-2.4 3-5a7 7 0 0 0-7-7z" />
    </svg>
  )
}

export function SettingsIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z" />
      <path d="M19.4 12a7.4 7.4 0 0 0 .1-1l2.1-1.6-2-3.5-2.5.5a7.6 7.6 0 0 0-1.7-1l-.4-2.6H11l-.4 2.6c-.6.2-1.2.6-1.7 1l-2.5-.5-2 3.5 2.1 1.6a7.4 7.4 0 0 0 .1 1l-2.1 1.6 2 3.5 2.5-.5c.5.4 1.1.8 1.7 1l.4 2.6h4.2l.4-2.6c.6-.2 1.2-.6 1.7-1l2.5.5 2-3.5z" />
    </svg>
  )
}

export function UsersIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M16 11a4 4 0 1 0-8 0" />
      <path d="M2 20a8 8 0 0 1 20 0" />
    </svg>
  )
}

export function ClockIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6l4 2" />
    </svg>
  )
}

export function ChevronRightIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M9 6l6 6-6 6" />
    </svg>
  )
}

export function ArrowLeftIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M15 18l-6-6 6-6" />
    </svg>
  )
}

export function SearchIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  )
}

export function SparklesIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 2l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" />
      <path d="M19 3l1 3 3 1-3 1-1 3-1-3-3-1 3-1z" />
    </svg>
  )
}

export function MessageSquareIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M21 15a4 4 0 0 1-4 4H7l-4 4V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" />
    </svg>
  )
}

export function MailIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 7l9 6 9-6" />
    </svg>
  )
}

export default {
  CalendarIcon,
  FileTextIcon,
  RadioIcon,
  LightbulbIcon,
  SettingsIcon,
  UsersIcon,
  ClockIcon,
  ChevronRightIcon,
  ArrowLeftIcon,
  SearchIcon,
  SparklesIcon,
  MessageSquareIcon,
  MailIcon,
}
