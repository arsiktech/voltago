import { useId } from 'react'
import { PlugZap } from 'lucide-react'

export function EmptyStateVisual({ tone = 'quiet' }: { tone?: 'quiet' | 'offline' | 'search' }) {
  const uid = useId().replace(/:/g, '')
  const accent = tone === 'offline' ? 'var(--danger)' : tone === 'search' ? 'var(--accent)' : 'var(--brand)'
  const warn = tone === 'offline' ? 'var(--danger)' : 'var(--check)'

  return (
    <svg
      className="empty-state-visual mx-auto mb-3 h-24 w-40"
      viewBox="0 0 160 96"
      fill="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`${uid}-glass`} x1="23" y1="18" x2="138" y2="85" gradientUnits="userSpaceOnUse">
          <stop stopColor="white" stopOpacity="0.92" />
          <stop offset="1" stopColor="white" stopOpacity="0.38" />
        </linearGradient>
        <filter id={`${uid}-shadow`} x="0" y="0" width="160" height="96" filterUnits="userSpaceOnUse">
          <feDropShadow dx="0" dy="8" stdDeviation="9" floodColor="#081e14" floodOpacity="0.16" />
        </filter>
      </defs>
      <path
        d="M20 68c19-24 35-23 48-12 14 12 25 13 44-11 7-9 16-13 28-11"
        stroke="color-mix(in oklab, var(--fg) 13%, transparent)"
        strokeWidth="5"
        strokeLinecap="round"
      />
      <path
        d="M20 68c19-24 35-23 48-12 14 12 25 13 44-11 7-9 16-13 28-11"
        stroke={accent}
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray="8 12"
      />
      <g filter={`url(#${uid}-shadow)`}>
        <rect x="45" y="16" width="70" height="56" rx="18" fill={`url(#${uid}-glass)`} />
        <rect x="45.5" y="16.5" width="69" height="55" rx="17.5" stroke="white" strokeOpacity="0.85" />
        <circle cx="80" cy="44" r="17" fill="color-mix(in oklab, var(--surface) 82%, transparent)" />
        {tone === 'search' ? (
          <path
            d="M75 39a7 7 0 1 0 9.9 9.9l6.1 6.1"
            stroke={accent}
            strokeWidth="4"
            strokeLinecap="round"
          />
        ) : tone === 'offline' ? (
          <path d="m72 36 16 16m0-16L72 52" stroke={warn} strokeWidth="4" strokeLinecap="round" />
        ) : (
          <PlugZap size={24} x={68} y={32} color={accent} strokeWidth={2.4} />
        )}
      </g>
      <circle className="empty-state-ring" cx="80" cy="78" r="14" stroke={accent} strokeOpacity="0.38" />
      <circle cx="31" cy="65" r="5" fill={accent} fillOpacity="0.7" />
      <circle cx="131" cy="35" r="4" fill={warn} fillOpacity="0.7" />
      <circle cx="123" cy="70" r="3" fill="var(--accent)" fillOpacity="0.55" />
    </svg>
  )
}
