import { motion, useReducedMotion } from 'framer-motion'
import type { Connector } from '../types'
import { powerText } from '../lib/format'
import { staggerList } from '../lib/motion'

function statusColor(status = ''): string {
  const s = status.toUpperCase()
  // UNAVAILABLE also contains AVAIL; failure states must win before availability.
  if (s.includes('OUT') || s.includes('UNAVAIL') || s.includes('FAULT') || s.includes('ERROR'))
    return 'var(--danger)'
  if (s.includes('AVAIL')) return 'var(--go)'
  if (s.includes('CHARG') || s.includes('OCCUP') || s.includes('BUSY')) return 'var(--check)'
  return 'var(--faint)'
}

const MAX_CHIPS = 6

export function ConnectorChips({ connectors }: { connectors?: Connector[] }) {
  const reduceMotion = useReducedMotion()
  if (!connectors?.length) return null
  const overflow = connectors.length - MAX_CHIPS
  return (
    <motion.div className="flex flex-wrap gap-1.5" initial="hidden" animate="show">
      {connectors.slice(0, MAX_CHIPS).map((c, i) => (
        <motion.span
          key={`${c.connector_id || 'connector'}:${i}`}
          custom={i}
          variants={staggerList(Boolean(reduceMotion), 3)}
          className="badge"
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: 999,
              background: statusColor(c.status),
              flex: 'none',
            }}
          />
          {c.label || c.type || 'Plug'}
          {c.power_kw ? ` · ${powerText(c.power_kw)}` : ''}
        </motion.span>
      ))}
      {overflow > 0 && (
        <motion.span
          custom={MAX_CHIPS}
          variants={staggerList(Boolean(reduceMotion), 3)}
          className="badge"
          title={`${overflow} more connector${overflow === 1 ? '' : 's'} — open Details for the full list`}
        >
          +{overflow} more
        </motion.span>
      )}
    </motion.div>
  )
}
