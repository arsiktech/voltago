import { motion, useReducedMotion } from 'framer-motion'
import { Ban, Check, Clock, MapPin, ShieldAlert } from 'lucide-react'
import type { DecisionLevel, Station } from '../types'
import { DECISION_META, decisionLevel } from '../lib/format'
import { popIn, quickEase } from '../lib/motion'

const ICONS: Record<DecisionLevel, typeof Check> = {
  go: Check,
  check: Clock,
  verify: ShieldAlert,
  lead: MapPin,
  avoid: Ban,
}

export function DecisionPill({ st }: { st: Station }) {
  const reduceMotion = useReducedMotion()
  const level = decisionLevel(st)
  const meta = DECISION_META[level]
  const label = st.decision?.label || meta.label
  const Icon = ICONS[level]
  return (
    <motion.span
      className={`pill ${meta.cls}`}
      title={st.decision?.reason || ''}
      variants={popIn(Boolean(reduceMotion), 0.96)}
      initial="hidden"
      animate="show"
      transition={quickEase}
    >
      <motion.span
        className="grid"
        animate={reduceMotion ? undefined : { scale: level === 'go' ? [1, 1.12, 1] : 1 }}
        transition={{ duration: 0.42, ease: [0.2, 0, 0, 1] }}
      >
        <Icon size={13} strokeWidth={2.6} />
      </motion.span>
      {label}
    </motion.span>
  )
}
