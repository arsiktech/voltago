import { providerHandoff, stationTitle } from '../lib/provider'
import { Component, lazy, memo, Suspense, useState, type ReactNode } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import {
  ChevronDown,
  Clock,
  ExternalLink,
  Lock,
  MapPin,
  Navigation,
  Plug,
  SquareParking,
  Store,
  Zap,
} from 'lucide-react'
import type { Station } from '../types'
import {
  TIER_META,
  availability,
  cityLabel,
  corroboration,
  displayName,
  distanceText,
  durationText,
  isRouteReady,
  isParkingRisk,
  mapsUrl,
  powerText,
  priceText,
  priceUnit,
  privateAccess,
  remoteStartAction,
  sourceTier,
  stationAgeText,
  tariffWindow,
} from '../lib/format'
import {
  countSwap,
  fadeSlide,
  liveMetricSwap,
  pressSpring,
  quickEase,
  smoothSpring,
  softSpring,
  staggerList,
  statusFlash,
} from '../lib/motion'
import { Badge } from './Badge'
import { DecisionPill } from './DecisionPill'
import { ConnectorChips } from './ConnectorChips'
const StationDetails = lazy(() => import('./StationDetails'))

/** Keep the card and its source/Maps actions usable if the optional chunk fails. */
class DetailsBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? (
      <div role="status" className="mt-3 text-sm text-muted">
        Details could not load. Check your connection and reload the page, or use the source link above.
      </div>
    ) : this.props.children
  }
}

function Fact({
  icon: Icon,
  children,
  warn,
}: {
  icon: typeof Zap
  children: React.ReactNode
  warn?: boolean
}) {
  return (
    <span
      className="inline-flex items-center gap-1 text-xs font-medium"
      style={{ color: warn ? 'var(--check)' : 'var(--muted)' }}
    >
      <Icon size={13} className="flex-none" />
      {children}
    </span>
  )
}

function Dot({ color }: { color: string }) {
  return <span style={{ width: 7, height: 7, borderRadius: 999, background: color, flex: 'none' }} />
}

function PowerFact({ kw }: { kw: number | null }) {
  if (kw == null) return null
  return (
    <span className="inline-flex items-center gap-1 font-medium text-muted">
      <Zap size={13} /> up to {powerText(kw)}
    </span>
  )
}

function LiveNumber({ value }: { value: number }) {
  const reduceMotion = useReducedMotion()
  return (
    <span className="relative inline-grid min-w-[1.1ch] overflow-hidden align-baseline">
      <AnimatePresence initial={false} mode="popLayout">
        <motion.span key={value} variants={liveMetricSwap(Boolean(reduceMotion))} initial="hidden" animate="show" exit="exit">
          {value}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

/** Real-time port breakdown: how many connectors are free / in use / out of service right now. */
function AvailabilityRow({ st }: { st: Station }) {
  const reduceMotion = useReducedMotion()
  const a = availability(st)

  // Leads aren't live — no real-time occupancy to claim. Show static port count only.
  if (!a.live) {
    return (
      <motion.div
        data-availability="not-live"
        variants={fadeSlide(Boolean(reduceMotion), 4)}
        initial="hidden"
        animate="show"
        className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-semibold"
      >
        <span className="inline-flex items-center gap-1.5 text-muted">
          <Dot color="var(--muted)" /> Not live
        </span>
        {a.total != null && (
          <span className="inline-flex items-center gap-1 font-medium text-muted tabular-nums">
            <Plug size={13} /> {a.total} port{a.total === 1 ? '' : 's'}
          </span>
        )}
        <PowerFact kw={a.maxKw} />
      </motion.div>
    )
  }

  const hasCounts = a.available != null && a.total != null
  const occupied = a.busy ?? 0
  const out = a.out ?? 0

  return (
    <motion.div
      data-availability="live"
      variants={fadeSlide(Boolean(reduceMotion), 4)}
      initial="hidden"
      animate="show"
      className="relative mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 overflow-hidden rounded-lg text-xs font-semibold tabular-nums"
    >
      <AnimatePresence initial={false}>
        {hasCounts && (
          <motion.span
            key={`${a.available}:${occupied}:${out}:${a.total}`}
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-px origin-left bg-brand"
            variants={statusFlash(Boolean(reduceMotion))}
            initial="hidden"
            animate="show"
            exit="exit"
          />
        )}
      </AnimatePresence>
      {hasCounts ? (
        <>
          <span
            className="inline-flex items-center gap-1.5"
            style={{ color: (a.available as number) > 0 ? 'var(--go)' : 'var(--muted)' }}
          >
            {(a.available as number) > 0 ? <span className="live-dot" /> : <Dot color="var(--muted)" />}
            <LiveNumber value={a.available as number} /> free
          </span>
          {occupied > 0 && (
            <span className="inline-flex items-center gap-1.5" style={{ color: 'var(--check)' }}>
              <Dot color="var(--check)" /> <LiveNumber value={occupied} /> in use
            </span>
          )}
          {out > 0 && (
            <span className="inline-flex items-center gap-1.5" style={{ color: 'var(--danger)' }}>
              <Dot color="var(--danger)" /> <LiveNumber value={out} /> out
            </span>
          )}
          <span className="inline-flex items-center gap-1 font-medium text-muted">
            <Plug size={13} /> <LiveNumber value={a.total as number} /> port{a.total === 1 ? '' : 's'}
          </span>
        </>
      ) : (
        <span className="inline-flex items-center gap-1.5 text-muted">
          <Dot color="var(--muted)" /> Live · status unknown
        </span>
      )}
      <PowerFact kw={a.maxKw} />
    </motion.div>
  )
}

function StationCardBase({
  st,
  selected,
  onSelect,
  index = 0,
  animateIn = true,
}: {
  st: Station
  selected?: boolean
  onSelect?: (id: string) => void
  index?: number
  animateIn?: boolean
}) {
  const reduceMotion = useReducedMotion()
  const [open, setOpen] = useState(false)
  const tier = sourceTier(st)
  const tm = TIER_META[tier]
  const risk = isParkingRisk(st)
  const access = privateAccess(st)
  const AccessIcon = access?.icon === 'lock' ? Lock : Store
  const startAction = remoteStartAction(st)
  const corr = corroboration(st)
  const windowInfo = tariffWindow(st)
  const conflict = st.claim_conflict?.summary
  const routeReady = isRouteReady(st)
  const price = priceText(st)
  const unit = priceUnit(st)
  const actionClass =
    'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand'
  const primaryActionClass = `${actionClass} flex-1 bg-brand text-brand-contrast hover:bg-brand-strong`
  const sourcePrimaryClass = `${actionClass} flex-1 bg-fg text-surface hover:opacity-90`
  const secondaryActionClass = `${actionClass} border border-border bg-surface text-fg-soft hover:border-border-strong hover:bg-surface-2`
  const mapsAction = (
    <motion.a
      href={mapsUrl(st)}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Open ${stationTitle(st)} in Maps`}
      title={routeReady ? 'Live route-ready candidate.' : 'Not route-ready: verify source/provider before driving.'}
      whileTap={reduceMotion ? undefined : { scale: 0.96 }}
      transition={pressSpring}
      className={routeReady ? primaryActionClass : `flex-1 ${secondaryActionClass}`}
    >
      <MapPin size={15} /> Open in Maps
    </motion.a>
  )
  const sourceAction = startAction ? (
    <motion.a
      href={startAction.href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={startAction.ariaLabel}
      title={startAction.title}
      whileTap={reduceMotion ? undefined : { scale: 0.96 }}
      transition={pressSpring}
      className={routeReady ? `flex-1 ${secondaryActionClass}` : sourcePrimaryClass}
    >
      <ExternalLink size={14} /> {startAction.label}
    </motion.a>
  ) : null
  // The operator's own client. Only renders where the source publishes no start page of
  // its own, so the row never carries two competing provider destinations.
  const handoff = startAction ? null : providerHandoff(st)
  const providerAction = handoff ? (
    <motion.a
      href={handoff.href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={handoff.ariaLabel}
      title={handoff.title}
      whileTap={reduceMotion ? undefined : { scale: 0.96 }}
      transition={pressSpring}
      className={routeReady ? `flex-1 ${secondaryActionClass}` : sourcePrimaryClass}
    >
      <ExternalLink size={14} /> {handoff.label}
    </motion.a>
  ) : null

  return (
    <motion.article
      initial={animateIn ? (reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8 }) : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, delay: Math.min(index, 8) * 0.035, ease: [0.2, 0, 0, 1] }}
      className="card relative overflow-hidden p-4 tabular-nums transition-colors"
      style={
        selected
          ? { borderColor: 'var(--brand)', background: 'var(--surface)' }
          : undefined
      }
    >
      <div
        role="button"
        aria-current={selected ? 'true' : undefined}
        tabIndex={0}
        onClick={() => onSelect?.(st.id)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onSelect?.(st.id)
          }
        }}
        className="relative z-10 cursor-pointer rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h3 className="[overflow-wrap:anywhere] text-base font-semibold leading-snug text-fg">
              {stationTitle(st)}
            </h3>
            {(st.city || st.address) && (
              <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted [overflow-wrap:anywhere]">
                {[cityLabel(st.city), st.address].filter(Boolean).join(' · ')}
              </p>
            )}
            {st.distance_km != null && (
              <p className="mt-1 text-xs font-medium text-muted">{distanceText(st.distance_km)}</p>
            )}
          </div>
          <div className="max-w-[44%] flex-none text-right">
            <span className="block text-[10px] font-medium uppercase tracking-[0.08em] text-muted">Charging</span>
            <span className="relative mt-0.5 inline-grid min-h-7 overflow-hidden align-baseline">
              <AnimatePresence initial={false} mode="popLayout">
                <motion.span
                  key={`${price}:${unit || ''}`}
                  variants={countSwap(Boolean(reduceMotion))}
                  initial="hidden"
                  animate="show"
                  exit="exit"
                  className="inline-block text-xl font-semibold leading-tight tracking-tight text-fg [overflow-wrap:anywhere]"
                >
                  {price}
                </motion.span>
              </AnimatePresence>
            </span>
            {unit && <span className="block text-[11px] leading-snug text-muted">{unit}</span>}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <DecisionPill st={st} />
          <span
            className="badge text-xs font-medium text-muted"
            style={{ background: 'transparent', border: 0, padding: 0, borderRadius: 0 }}
            title={st.reliability}
          >
            {tm.label}
          </span>
          {corr.count >= 2 && (
            <span
              className="text-xs text-muted"
              title={`Confirmed by ${corr.count} independent public sources${corr.families.length ? ': ' + corr.families.join(', ') : ''}. Raises confidence it exists at this price — not a live status.`}
            >
              · {corr.count} sources
            </span>
          )}
          {windowInfo && (
            <Badge
              tone={windowInfo.currentlyCheap ? 'brand' : 'warn'}
              title={
                windowInfo.currentlyCheap
                  ? 'Time-of-use tariff — the cheap/free window applies right now.'
                  : 'Time-of-use tariff — the cheap/free price does NOT apply right now; check the window before routing.'
              }
            >
              <Clock size={11} className="flex-none" />
              {windowInfo.label}
            </Badge>
          )}
          {conflict && (
            <Badge tone="danger" title={conflict}>Sources disagree</Badge>
          )}
          {access && (
            <Badge tone="warn" title={st.access?.note || st.parking?.label}>
              <AccessIcon size={11} className="flex-none" />
              {access.short}
            </Badge>
          )}
          {risk && (
            <Badge tone="warn">{st.parking?.paid ? 'Paid parking' : 'Limited parking'}</Badge>
          )}
        </div>

        {/* Real-time availability + sockets + speed */}
        <AvailabilityRow st={st} />

        {/* Route facts */}
        <motion.div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1" initial="hidden" animate="show">
          {st.distance_km != null && st.drive_eta_min_est != null && (
            <motion.span custom={0} variants={staggerList(Boolean(reduceMotion), 4)}>
              <Fact icon={Navigation}>
                {distanceText(st.distance_km)} · {durationText(st.drive_eta_min_est)} drive
              </Fact>
            </motion.span>
          )}
          {st.distance_km == null && (
            <motion.span custom={0} variants={staggerList(Boolean(reduceMotion), 4)}>
              <Fact icon={Navigation}>Locate to see ETA</Fact>
            </motion.span>
          )}
          {st.parking?.label && (
            <motion.span
              custom={1}
              variants={staggerList(Boolean(reduceMotion), 4)}
              data-route-fact="parking"
              aria-label={`Parking: ${st.parking.label}`}
            >
              <Fact icon={SquareParking} warn={risk}>
                {st.parking.label}
              </Fact>
            </motion.span>
          )}
          {(st.last_seen || st.freshness_minutes != null) && (
            <motion.span custom={2} variants={staggerList(Boolean(reduceMotion), 4)}>
              <Fact icon={Clock}>{stationAgeText(st)}</Fact>
            </motion.span>
          )}
        </motion.div>

        {st.connectors && st.connectors.length > 0 && (
          <div className="mt-2.5">
            <ConnectorChips connectors={st.connectors} />
          </div>
        )}
      </div>

      {/* Actions. aria-labels carry the station name so a screen-reader links list is not
          just "Open in Maps" x N (found by qa_screen_reader_contract). */}
      <motion.div className="relative z-10 mt-3 flex flex-wrap items-center gap-2" layout="position" transition={softSpring}>
        {routeReady ? (
          <>
            {mapsAction}
            {sourceAction ?? providerAction}
          </>
        ) : (
          <>
            {sourceAction ?? providerAction}
            {mapsAction}
          </>
        )}
        <motion.button
          onClick={() => setOpen((v) => !v)}
          onKeyDown={(e) => {
            // Escape collapses the disclosure, focus stays on the trigger (a11y contract).
            if (e.key === 'Escape' && open) {
              e.stopPropagation()
              setOpen(false)
            }
          }}
          aria-expanded={open}
          aria-label={`Source details for ${stationTitle(st)}`}
          whileTap={reduceMotion ? undefined : { scale: 0.96 }}
          transition={pressSpring}
          className={secondaryActionClass}
        >
          Details
          <motion.span animate={{ rotate: open ? 180 : 0 }} transition={quickEase} className="grid">
            <ChevronDown size={14} />
          </motion.span>
        </motion.button>
      </motion.div>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={reduceMotion ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
            exit={
              reduceMotion
                ? { opacity: 0, transition: quickEase }
                : { height: 0, opacity: 0, transition: quickEase }
            }
            transition={smoothSpring}
            className="relative z-10 overflow-hidden"
          >
            <DetailsBoundary key={`${st.id}:${st.detail_snapshot ?? 'inline'}`}>
              <Suspense fallback={<p role="status" className="mt-3 text-sm text-muted">Loading source details…</p>}>
                <StationDetails st={st} />
              </Suspense>
            </DetailsBoundary>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  )
}

type StationCardProps = Parameters<typeof StationCardBase>[0]

/** One broken row must never blank the whole driving UI: fail closed to an honest
 * placeholder instead (a DECISION_META gap once white-screened the app via this path). */
class StationCardBoundary extends Component<{ st: Station; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    if (this.state.failed) {
      return (
        <div className="card p-3.5 text-xs text-muted">
          Couldn't display {displayName(this.props.st.name)} safely — hidden rather than shown wrong.
        </div>
      )
    }
    return this.props.children
  }
}

function StationCardWithBoundary(props: StationCardProps) {
  return (
    <StationCardBoundary st={props.st}>
      <StationCardBase {...props} />
    </StationCardBoundary>
  )
}

export const StationCard = memo(StationCardWithBoundary)
