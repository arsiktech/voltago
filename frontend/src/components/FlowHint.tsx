import { motion, useReducedMotion } from 'framer-motion'
import { MapPin, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react'
import { pressSpring, softSpring } from '../lib/motion'

type Tone = 'brand' | 'warn' | 'danger' | 'muted'
type IconName = 'area' | 'loading' | 'ready' | 'verify' | 'offline'

const TONE_COLOR: Record<Tone, string> = {
  brand: 'var(--brand-strong)',
  warn: 'var(--check)',
  danger: 'var(--danger)',
  muted: 'var(--muted)',
}

function FlowIcon({ name }: { name: IconName }) {
  if (name === 'loading') return <RefreshCw size={15} />
  if (name === 'ready') return <ShieldCheck size={15} />
  if (name === 'offline') return <TriangleAlert size={15} />
  return <MapPin size={15} />
}

export interface FlowHintProps {
  icon: IconName
  tone: Tone
  title: string
  message: string
  actionLabel?: string
  actionHref?: string
  onAction?: () => void
}

export function FlowHint({ icon, tone, title, message, actionLabel, actionHref, onAction }: FlowHintProps) {
  const reduceMotion = useReducedMotion()
  const actionClass =
    'col-span-2 mt-0.5 inline-flex min-h-11 w-full flex-none items-center justify-center rounded-lg border border-border bg-surface px-3 py-2 font-semibold text-fg-soft transition-colors hover:border-border-strong sm:col-span-1 sm:w-auto'
  const actionMotion = {
    whileTap: reduceMotion ? undefined : { scale: 0.96 },
    transition: pressSpring,
  }

  return (
    <motion.div
      data-driver-guidance
      role="status"
      aria-live="polite"
      className="grid grid-cols-[auto_1fr] items-start gap-2.5 rounded-xl border bg-surface-2 px-3 py-2.5 text-xs sm:grid-cols-[auto_1fr_auto]"
      layout="position"
      transition={softSpring}
      style={{
        borderColor: `color-mix(in oklab, ${TONE_COLOR[tone]} 30%, var(--border))`,
      }}
    >
      <motion.span
        className="mt-0.5 grid h-6 w-6 flex-none place-items-center rounded-lg"
        animate={
          reduceMotion
            ? undefined
            : icon === 'loading'
              ? { rotate: 360 }
              : tone === 'brand'
                ? { scale: [1, 1.08, 1] }
                : undefined
        }
        transition={icon === 'loading' ? { duration: 1.1, repeat: Infinity, ease: 'linear' } : softSpring}
        style={{
          color: TONE_COLOR[tone],
          background: `color-mix(in oklab, ${TONE_COLOR[tone]} 12%, transparent)`,
        }}
      >
        <FlowIcon name={icon} />
      </motion.span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-fg">{title}</span>
        <span className="mt-0.5 block leading-relaxed text-muted">{message}</span>
      </span>
      {actionLabel && actionHref && (
        <motion.a
          href={actionHref}
          target="_blank"
          rel="noopener noreferrer"
          className={actionClass}
          {...actionMotion}
        >
          {actionLabel}
        </motion.a>
      )}
      {actionLabel && onAction && (
        <motion.button
          type="button"
          onClick={onAction}
          className={actionClass}
          {...actionMotion}
        >
          {actionLabel}
        </motion.button>
      )}
    </motion.div>
  )
}
