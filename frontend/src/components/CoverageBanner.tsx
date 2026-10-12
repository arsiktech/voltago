import { motion, useReducedMotion } from 'framer-motion'
import { TriangleAlert } from 'lucide-react'
import type { CacheInfo, Coverage } from '../types'
import { fadeSlide } from '../lib/motion'

export function CoverageBanner({
  coverage,
  cache,
}: {
  coverage?: Coverage
  cache?: CacheInfo
}) {
  const reduceMotion = useReducedMotion()
  const degraded = coverage?.mode === 'degraded'
  const stale = cache?.stale === true
  const partial = coverage?.partial === true
  // Only surface real coverage problems (few live stations, or the main MapaREVE cache
  // is stale). A routine secondary-source cache note shouldn't trip the alarm.
  // A bounded upstream can stop mid-pagination even when its aggregate count would
  // otherwise meet the live threshold. Keep that incomplete-result warning visible.
  if (!degraded && !stale && !partial) return null

  const title = stale
    ? 'Showing cached data'
    : partial
      ? 'Live results may be incomplete'
      : 'Limited live coverage'
  const message = cache?.offline
    ? 'Offline — showing cached chargers. Nothing is live.'
    : stale
      ? 'Availability may have changed. Verify the source before driving.'
      : partial
        ? coverage?.warning || 'The live source reached its page limit. Refresh or narrow the map before routing.'
    : 'Live coverage is limited here. Check each station’s source before driving.'

  return (
    <motion.div
      role="status"
      variants={fadeSlide(Boolean(reduceMotion), 6)}
      initial="hidden"
      animate="show"
      exit="exit"
      className="flex items-start gap-2 rounded-xl border px-3 py-2 text-xs"
      style={{
        color: 'var(--check)',
        background: 'color-mix(in oklab, var(--check) 12%, transparent)',
        borderColor: 'color-mix(in oklab, var(--check) 36%, transparent)',
      }}
    >
      <TriangleAlert size={14} className="mt-0.5 flex-none" />
      <p className="min-w-0 leading-snug">
        <span className="font-semibold">{title}</span>
        {message && '. '}
        {message && <span className="text-muted">{message}</span>}
      </p>
    </motion.div>
  )
}
