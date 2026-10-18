import { Component, Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { ChevronDown, LocateFixed, Moon, RefreshCw, Search, Sun, ArrowUpRight, List, Map } from 'lucide-react'
import { publicTransportErrorMessage, useChargers } from './lib/api'
import { discoveryRows, selectedMapRow } from './lib/discovery'
import { boxAround, containsCoordinate, isLiveBbox, normalizeBbox, widerBoxAround } from './lib/geo'
import {
  DEFAULT_FILTERS,
  ageText,
  chooseChargersData,
  inViewport,
  isMapLocationVerified,
  isRouteReady,
  mapsUrl,
  passesFilters,
  resultScopeLabel,
  responseHasMixedLiveAuthority,
  responseHasNoLiveAuthority,
  sortNearbyBySafetyAndDistance,
  statusTrustSummary,
} from './lib/format'
import type { Bbox, Coords, FilterKey, Filters, GeocodeResult, Station, Viewport } from './types'
import { useTheme } from './hooks/useTheme'
import { useGeolocation } from './hooks/useGeolocation'
import { StationList } from './components/StationList'
import { FilterRail } from './components/FilterRail'
import { SearchBar } from './components/SearchBar'
import { CoverageBanner } from './components/CoverageBanner'
import { FlowHint, type FlowHintProps } from './components/FlowHint'
import { EmptyStateVisual } from './components/VisualMoment'
import type { FlyTarget } from './components/MapView'
import {
  countSwap,
  fadeSlide,
  popIn,
  pressSpring,
  quickEase,
  settleSpring,
  smoothSpring,
  statusFlash,
} from './lib/motion'

let mapViewChunk: Promise<typeof import('./components/MapView')> | null = null
/** One shared import: started at mount so MapLibre downloads alongside the charger
 * data instead of after it; the lazy component reuses the same promise. */
const loadMapView = () => (mapViewChunk ??= import('./components/MapView'))
const MapView = lazy(() => loadMapView().then((m) => ({ default: m.MapView })))

/** The map is a backdrop enhancement, never a hard dependency: if the lazy MapLibre chunk
 * fails to load (blocked CDN/proxy, flaky network), swallow it and keep the panel working.
 * Without this the rejection escaped Suspense and white-screened the whole app
 * (found by qa_cdn_blocked_ui). */
class MapErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? null : this.props.children
  }
}

const REGIONS = [
  { label: 'Mainland', lat: 40, lon: -3.4, bbox: { south: 35.8, west: -9.6, north: 43.9, east: 3.4 } },
  { label: 'Canary Islands', lat: 28.4, lon: -15.7, bbox: { south: 27.5, west: -18.3, north: 29.5, east: -13.3 } },
  { label: 'Balearic Islands', lat: 39.6, lon: 2.8, bbox: { south: 38.5, west: 1.1, north: 40.2, east: 4.4 } },
] satisfies (FlyTarget & { label: string })[]
const SPAIN_VIEW: FlyTarget = REGIONS[0]

interface SearchOrigin {
  coords: Coords
  label: string
  kind: 'device' | 'place' | 'map'
}

const HONESTY =
  'Search places worldwide. Charger coverage and live status depend on connected sources and vary by location. Cards distinguish live, official, directory, community and cached evidence. Wide map views show stored evidence; zoom in to check live sources where supported. Charging price and parking cost are separate. Euro price filters use euro tariffs; other or unknown currencies are not converted. Provider apps and onsite parking signs always win.'

function centerOutside(center: Coords, b: Bbox): boolean {
  return !containsCoordinate(b, center)
}

function bboxKey(b: Bbox): string {
  return [b.south, b.west, b.north, b.east].map((v) => v.toFixed(3)).join(',')
}

/** Let the first decision panel paint before downloading/parsing the 1 MB MapLibre chunk. */
function useMapActivation(decisionReady: boolean): boolean {
  const [active, setActive] = useState(false)
  useEffect(() => {
    if (!decisionReady) return
    // Start only after data has committed and the decision card has painted (the
    // frame callback runs before that paint, the timeout right after it). The MapLibre
    // chunk is already downloading since mount, so no further idle wait is needed.
    let timeoutHandle: number | undefined
    const frameHandle = window.requestAnimationFrame(() => {
      timeoutHandle = window.setTimeout(() => setActive(true), 0)
    })
    return () => {
      window.cancelAnimationFrame(frameHandle)
      if (timeoutHandle !== undefined) window.clearTimeout(timeoutHandle)
    }
  }, [decisionReady])
  useEffect(() => {
    // A failed chunk is handled by MapErrorBoundary when the map mounts.
    loadMapView().catch(() => undefined)
  }, [])
  return active
}

/** The result list shares its scroller with the results header. The virtualizer needs to
 * know where the list starts and how tall the pinned heading is. */
function useListOffsets(
  headingRef: React.RefObject<HTMLDivElement | null>,
  statusRef: React.RefObject<HTMLDivElement | null>,
  bodyRef: React.RefObject<HTMLDivElement | null>,
) {
  const [offsets, setOffsets] = useState({ margin: 0, heading: 0 })
  useLayoutEffect(() => {
    const heading = headingRef.current
    const status = statusRef.current
    const body = bodyRef.current
    if (!heading || !status || !body) return
    const measure = () => {
      const next = { margin: body.offsetTop, heading: heading.offsetHeight }
      setOffsets((current) => (current.margin === next.margin && current.heading === next.heading ? current : next))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(heading)
    observer.observe(status)
    return () => observer.disconnect()
  }, [headingRef, statusRef, bodyRef])
  return offsets
}

/** Mobile sheet grabber: drag up for the full list, down for the map. A pointer
 * gesture only; the labelled List/Map button in the results heading is the accessible
 * control for the same switch. */
function SheetGrabber({ expanded, onChange }: { expanded: boolean; onChange: (expanded: boolean) => void }) {
  const reduceMotion = useReducedMotion()
  const dragged = useRef(false)
  return (
    <motion.div
      className="sheet-grabber-zone"
      aria-hidden="true"
      drag={reduceMotion ? false : 'y'}
      dragConstraints={{ top: 0, bottom: 0 }}
      dragElastic={0.18}
      dragSnapToOrigin
      onDragStart={() => { dragged.current = true }}
      onDragEnd={(_, info) => {
        if (info.offset.y < -24 || info.velocity.y < -300) onChange(true)
        else if (info.offset.y > 24 || info.velocity.y > 300) onChange(false)
      }}
      onClick={() => {
        if (dragged.current) {
          dragged.current = false
          return
        }
        onChange(!expanded)
      }}
    >
      <span className="sheet-grabber" />
    </motion.div>
  )
}

function ViewToggle({ expanded, onChange }: { expanded: boolean; onChange: (expanded: boolean) => void }) {
  const reduceMotion = useReducedMotion()
  return (
    <motion.button
      type="button"
      className="mobile-view-toggle"
      onClick={() => onChange(!expanded)}
      aria-label={expanded ? 'Show map and chargers' : 'Show full charger list'}
      whileTap={reduceMotion ? undefined : { scale: 0.96 }}
      transition={pressSpring}
    >
      {expanded ? <Map size={15} /> : <List size={15} />}
      {expanded ? 'Map' : 'List'}
    </motion.button>
  )
}

function IconButton({
  label,
  onClick,
  children,
  pressed,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
  pressed?: boolean
}) {
  const reduceMotion = useReducedMotion()
  return (
    <motion.button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={pressed}
      whileTap={reduceMotion ? undefined : { scale: 0.94 }}
      transition={pressSpring}
      className="grid h-11 w-11 place-items-center rounded-full text-fg-soft transition-colors hover:bg-surface-2"
    >
      {children}
    </motion.button>
  )
}

function ThemeIcon({ theme }: { theme: 'light' | 'dark' }) {
  const reduceMotion = useReducedMotion()
  return (
    <span className="relative grid h-[18px] w-[18px] place-items-center">
      <AnimatePresence initial={false} mode="sync">
        <motion.span
          key={theme}
          initial={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.25, filter: 'blur(4px)' }}
          animate={{ opacity: 1, scale: 1, filter: 'blur(0px)' }}
          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.25, filter: 'blur(4px)' }}
          transition={{ type: 'spring', duration: 0.3, bounce: 0 }}
          className="absolute inset-0 grid place-items-center"
        >
          {theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

function CountSwap({
  value,
  className,
}: {
  value: React.ReactNode
  className?: string
}) {
  const reduceMotion = useReducedMotion()
  return (
    <span className="relative inline-grid min-w-[1.7ch] overflow-hidden align-baseline">
      <motion.span
        key={String(value)}
        variants={countSwap(Boolean(reduceMotion))}
        initial="hidden"
        animate="show"
        className={className}
      >
        {value}
      </motion.span>
    </span>
  )
}

function LoadingCards() {
  const reduceMotion = useReducedMotion()
  return (
    <motion.div className="space-y-3" initial="hidden" animate="show">
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          custom={i}
          variants={{
            hidden: reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8 },
            show: {
              opacity: 1,
              y: 0,
              transition: { duration: 0.22, delay: reduceMotion ? 0 : i * 0.055, ease: [0.2, 0, 0, 1] },
            },
          }}
          className="skeleton h-40 rounded-[1.4rem]"
        />
      ))}
    </motion.div>
  )
}

function DataHonestyDisclosure() {
  const reduceMotion = useReducedMotion()
  const [open, setOpen] = useState(false)
  return (
    <div className="data-honesty flex-none border-t border-border px-6 py-2 text-xs text-muted">
      <motion.button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls="data-honesty-panel"
        whileTap={reduceMotion ? undefined : { scale: 0.98 }}
        transition={pressSpring}
        className="flex w-full items-center justify-between gap-3 rounded-lg py-1 text-left font-medium text-fg-soft"
      >
        Data honesty
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={quickEase} className="grid flex-none">
          <ChevronDown size={15} />
        </motion.span>
      </motion.button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id="data-honesty-panel"
            initial={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={reduceMotion ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={smoothSpring}
            className="overflow-hidden"
          >
            <p className="mt-2 leading-relaxed">{HONESTY}</p>
            <a href="/coverage/" className="inline-flex min-h-11 items-center font-semibold text-fg underline underline-offset-4">Coverage &amp; sources</a>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default function App() {
  const reduceMotion = useReducedMotion()
  const { theme, toggle } = useTheme()
  const geo = useGeolocation()
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const [requestMaxPrice, setRequestMaxPrice] = useState(filters.maxPrice)
  // Keep slider feedback immediate without starting an upstream lookup for every
  // pointer move. Only the settled price changes the request and cache identity.
  useEffect(() => {
    const timer = setTimeout(() => setRequestMaxPrice(filters.maxPrice), 250)
    return () => clearTimeout(timer)
  }, [filters.maxPrice])
  const requestFilters = { ...filters, maxPrice: requestMaxPrice }
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [overview, setOverview] = useState(true)
  const [region, setRegion] = useState('Mainland')
  const [fullList, setFullList] = useState(false)
  const [committedBbox, setCommittedBbox] = useState<Bbox | null>(REGIONS[0].bbox)
  const [viewport, setViewport] = useState<Viewport | null>(null)
  const [flyTarget, setFlyTarget] = useState<FlyTarget | null>(null)
  const [origin, setOrigin] = useState<SearchOrigin | null>(null)
  const lastAutoAreaKey = useRef<string | null>(null)
  const explicitAreaIntentAt = useRef(0)
  const listRef = useRef<HTMLDivElement>(null)
  const headingRef = useRef<HTMLDivElement>(null)
  const statusRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const [scrolled, setScrolled] = useState(false)
  const listOffsets = useListOffsets(headingRef, statusRef, bodyRef)

  const inventory = useChargers({
    filters: DEFAULT_FILTERS, overview: true, compact: true,
    bbox: committedBbox ?? viewport?.bbox ?? REGIONS[0].bbox,
  })
  const query = useChargers({
    filters: requestFilters,
    coords: overview ? null : origin?.coords,
    bbox: overview ? (committedBbox ?? viewport?.bbox) : committedBbox,
    overview,
    compact: overview,
  })
  // Intelligence-first render: in live mode a parallel leads-only overview query (no live
  // fetch — served from the cached intel DB, ~instant) paints source-backed leads while the
  // bounded live-source fetch runs. Swapped in place when live data arrives;
  // leads are honestly labeled "Not live" throughout.
  const instant = useChargers({
    filters: requestFilters,
    bbox: committedBbox,
    overview: true,
    compact: true,
    enabled: !overview,
  })
  const data = query.data
  const { data: effectiveData, usingInstant, liveFailed } = useMemo(
    () =>
      chooseChargersData({
        overview,
        live: data,
        instant: overview ? inventory.data : instant.data,
        isFetching: query.isFetching,
        isPlaceholder: query.isPlaceholderData,
        isError: query.isError,
        viewportBbox: overview ? (viewport?.bbox ?? null) : committedBbox,
      }),
    [overview, data, inventory.data, instant.data, query.isFetching, query.isPlaceholderData, query.isError, viewport, committedBbox],
  )
  const responseNotLive = responseHasNoLiveAuthority(effectiveData)
  const responseMixedLive = responseHasMixedLiveAuthority(effectiveData)
  const mapActive = useMapActivation(Boolean(effectiveData) || query.isError)

  // Only a successful location request takes ownership of the search area.
  // Later renders or a chosen place must not replay the last device location.
  const claimSearchArea = useCallback(() => {
    explicitAreaIntentAt.current = performance.now()
    // Cancel automatic area search without discarding the current visible scope
    // while GPS is pending (or if permission is denied).
    setViewport(current => current ? { ...current, userInitiated: false } : null)
  }, [])

  const requestLocation = () => {
    claimSearchArea()
    geo.request((coords) => {
      setOrigin({ coords, label: 'your location', kind: 'device' })
      setOverview(false)
      setCommittedBbox(boxAround(coords, 0.18))
      setFlyTarget({ lat: coords.lat, lon: coords.lon, zoom: 16, nonce: Date.now() })
    })
  }

  const onPlaceSelect = (r: GeocodeResult) => {
    claimSearchArea()
    geo.cancel()
    const coords = { lat: r.lat, lon: r.lon }
    setOrigin({ coords, label: r.name, kind: 'place' })
    const bb = normalizeBbox(r.bbox
      ? { south: r.bbox.latitude_sw, west: r.bbox.longitude_sw, north: r.bbox.latitude_ne, east: r.bbox.longitude_ne }
      : boxAround({ lat: r.lat, lon: r.lon }, 0.12))
    setOverview(!isLiveBbox(bb))
    setRegion('')
    setCommittedBbox(bb)
    setFlyTarget({ lat: r.lat, lon: r.lon, bbox: bb, nonce: Date.now() })
  }

  const searchThisArea = () => {
    if (!viewport) return
    claimSearchArea()
    geo.cancel()
    lastAutoAreaKey.current = bboxKey(viewport.bbox)
    setOrigin({ coords: viewport.center, label: 'map center', kind: 'map' })
    setOverview(!isLiveBbox(viewport.bbox))
    setCommittedBbox(viewport.bbox)
  }

  const allRows: Station[] = useMemo(
    () =>
      sortNearbyBySafetyAndDistance(
        [...(effectiveData?.stations || []), ...(effectiveData?.leads || [])],
        !overview ? (origin?.coords ?? null) : null,
      ),
    [effectiveData, origin, overview],
  )

  // Shared chip-filtered rows. The list can still show weaker leads with honest labels;
  // the map gets a stricter verified-location subset below so a pin never implies a
  // directory/community coordinate is exact.
  const filteredRows = useMemo(
    () => allRows.filter((st) => passesFilters(st, filters, '')),
    [allRows, filters],
  )
  const mapRows = useMemo(() => discoveryRows(inventory.data?.leads || [], allRows)
    .filter(st => passesFilters(st, filters, '') && isMapLocationVerified(st)),
    [inventory.data, allRows, filters])
  // Region shortcuts and location searches own their bounds independently of the map.
  // Intentional map exploration clears an overview region to follow the visible bounds;
  // camera-only changes (selection/resize) must not silently alter the chosen search area.
  const listScopeBbox = overview ? (committedBbox ?? viewport?.bbox ?? REGIONS[0].bbox) : committedBbox
  // The mainland camera rectangle overlaps Mallorca. Keep island inventory in its
  // own region when the shortcut owns scope; manually explored map bounds stay literal.
  const excludeBalearics = overview && committedBbox != null && region === 'Mainland'
  const listRows = useMemo(
    () => filteredRows.filter((st) => inViewport(st, listScopeBbox) && !(
      excludeBalearics && typeof st.lat === 'number' && typeof st.lon === 'number' && inViewport(st, REGIONS[2].bbox)
    )),
    [filteredRows, listScopeBbox, excludeBalearics],
  )
  const routeReadyRows = useMemo(() => listRows.filter(isRouteReady), [listRows])
  const selectedRow = selectedMapRow(selectedId, mapRows)
  // Map pins are a visual enhancement, not the only way to inspect a result. A static
  // discovery point can sit outside the current list viewport; place it at the top of
  // the primary panel while selected so keyboard and assistive-tech users can reach it.
  const panelRows = useMemo(
    () => selectedRow && !listRows.some((row) => row.id === selectedRow.id)
      ? [selectedRow, ...listRows]
      : listRows,
    [listRows, selectedRow],
  )
  // Clear before committing the panel/map; retaining an invalid ID would revive
  // an old selection if a filter or later response makes that station reappear.
  if (selectedId && !selectedRow) setSelectedId(null)
  const coverage = effectiveData?.meta?.coverage
  const liveReady = coverage?.live_route_ready ?? (effectiveData?.stations || []).filter(isRouteReady).length
  const leadCount = effectiveData?.leads?.length || 0

  const toggleFilter = (k: FilterKey) => setFilters((f) => ({ ...f, [k]: !f[k] }))

  const updated = query.dataUpdatedAt ? ageText(new Date(query.dataUpdatedAt).toISOString()) : ''
  const trustStatus = statusTrustSummary({
    overview,
    sourceBackedOnly: usingInstant || liveFailed || responseNotLive,
    isFetching: query.isFetching,
    stale: effectiveData?.cache?.stale === true,
    mixedLive: responseMixedLive,
    updated,
    liveReady,
    leadCount,
  })
  const trustIsNotLive = overview || usingInstant || liveFailed || responseNotLive

  const showAreaPill =
    viewport != null &&
    viewport.userInitiated === true &&
    isLiveBbox(viewport.bbox) &&
    (overview
      ? viewport.zoom >= 8.5
      : committedBbox != null && centerOutside(viewport.center, committedBbox))
  const areaSearchDelay = overview ? 1000 : 750

  useEffect(() => {
    // A new viewport must not wait for the old city's live response. Switching
    // query keys detaches the old observer and aborts its consumed fetch signal.
    if (!showAreaPill || !viewport) return
    const key = bboxKey(viewport.bbox)
    if (lastAutoAreaKey.current === key) return
    const t = window.setTimeout(() => {
      lastAutoAreaKey.current = key
      setOrigin({ coords: viewport.center, label: 'map center', kind: 'map' })
      setOverview(false)
      setCommittedBbox(viewport.bbox)
    }, areaSearchDelay)
    return () => window.clearTimeout(t)
  }, [showAreaPill, viewport, areaSearchDelay])

  const placeLabel = overview
    ? origin?.kind === 'place' ? `Explore ${origin.label}` : 'Explore chargers'
    : origin?.kind === 'device'
      ? 'Near you'
      : origin?.label
        ? `Near ${origin.label}`
    : coverage?.mode === 'degraded'
      ? 'Limited live coverage'
      : 'Best options now'
  const scopeLabel = resultScopeLabel(filters)
  const firstRouteReady = routeReadyRows[0]
  const areaMotionKey = overview ? 'overview' : committedBbox ? bboxKey(committedBbox) : 'nearby'
  const flowHint: FlowHintProps | null = overview && geo.status === 'locating'
    ? {
        icon: 'loading',
        tone: 'brand',
        title: 'Finding chargers near you',
        message: 'Live public chargers first. Choose a charging price limit when you need one.',
      }
    : overview && (geo.status === 'denied' || geo.status === 'unavailable')
      ? {
          icon: 'offline',
          tone: 'warn',
          title: 'Location is unavailable',
          message: 'Enter your home address or city above to search from there instead.',
          actionLabel: 'Try location again',
          onAction: requestLocation,
        }
      : liveFailed
    ? {
        icon: 'offline',
        tone: 'danger',
        title: 'Source-backed only',
        message: 'Open Source before Maps; no live availability is claimed right now.',
        actionLabel: 'Try live again',
        onAction: () => query.refetch(),
      }
    : usingInstant
      ? {
          icon: 'loading',
          tone: 'warn',
          title: 'Live check running',
          message: 'Use these rows as backups until a Live / Go now card appears.',
        }
      : overview
        ? {
            icon: 'area',
            tone: 'brand',
            title: 'Find a charger near home',
            message: 'Search a city above or use your location. Cheap and free stay optional.',
          }
        : firstRouteReady
          ? {
              icon: 'ready',
              tone: 'brand',
              title: `${routeReadyRows.length} Go now option${routeReadyRows.length === 1 ? '' : 's'} nearby`,
              message: 'The closest live option is ready; confirm provider signs before parking.',
              actionLabel: 'Open closest in Maps',
              actionHref: mapsUrl(firstRouteReady),
            }
          : listRows.length > 0 && !query.isFetching && coverage?.mode !== 'degraded' && !effectiveData?.cache?.stale
            ? {
                icon: 'verify',
                tone: 'warn',
                title: 'No confirmed live bay nearby',
                message: 'Nearest source-checked backups are below. Verify the provider before driving.',
              }
            : null

  let body: React.ReactNode
  if (query.isLoading && !effectiveData) {
    body = <LoadingCards />
  } else if (query.isError && !effectiveData) {
    body = (
      <motion.div
        className="card p-5 text-center"
        variants={popIn(Boolean(reduceMotion))}
        initial="hidden"
        animate="show"
        exit="exit"
      >
        <EmptyStateVisual tone="offline" />
        <p className="text-sm font-semibold text-fg">Couldn&apos;t reach the live source</p>
        <p className="mt-1 text-xs text-muted">{publicTransportErrorMessage(query.error)}</p>
        <motion.button
          onClick={() => query.refetch()}
          whileTap={reduceMotion ? undefined : { scale: 0.96 }}
          transition={pressSpring}
          className="mt-3 min-h-11 rounded-xl bg-brand px-5 text-sm font-semibold text-brand-contrast transition-colors hover:bg-brand-strong"
        >
          Try again
        </motion.button>
      </motion.div>
    )
  } else if (!panelRows.length) {
    body = (
      <motion.div
        className="card p-5 text-center"
        variants={popIn(Boolean(reduceMotion))}
        initial="hidden"
        animate="show"
        exit="exit"
      >
        <EmptyStateVisual tone={search.trim() ? 'search' : liveFailed ? 'offline' : 'quiet'} />
        <p className="text-sm font-semibold text-fg">
          {overview ? 'No chargers match these filters' : 'No charger nearby yet'}
        </p>
        <p className="mt-1 text-xs text-muted">
          {overview
            ? 'Loosen a filter to see more source-backed options.'
            : 'Show source-backed backups or search a wider area around this point.'}
        </p>
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          {!overview && origin && (
            <motion.button
              onClick={() => {
                claimSearchArea()
                geo.cancel()
                const bbox = widerBoxAround(origin.coords, committedBbox)
                setOverview(!isLiveBbox(bbox))
                setCommittedBbox(bbox)
                setFlyTarget({ ...origin.coords, bbox, nonce: Date.now() })
              }}
              whileTap={reduceMotion ? undefined : { scale: 0.96 }}
              transition={pressSpring}
              className="min-h-11 rounded-xl border border-border bg-surface-2 px-4 text-xs font-semibold text-fg-soft transition-colors hover:border-border-strong"
            >
              Search wider area
            </motion.button>
          )}
          <motion.button
            // "available" must clear too: leads are never live-available, so leaving Open now
            // on would keep the list empty and strand the driver (found by qa_driver_readiness).
            onClick={() => setFilters((f) => ({ ...f, leads: true, verified: false, parking: false, available: false }))}
            whileTap={reduceMotion ? undefined : { scale: 0.96 }}
            transition={pressSpring}
            className="min-h-11 rounded-xl border border-border bg-surface-2 px-4 text-xs font-semibold text-fg-soft transition-colors hover:border-border-strong"
          >
            Show all leads
          </motion.button>
          <motion.button
            // Clear both the chosen tariff cap and live-only constraint for recovery.
            onClick={() => setFilters((f) => ({ ...f, maxPrice: null, available: false }))}
            whileTap={reduceMotion ? undefined : { scale: 0.96 }}
            transition={pressSpring}
            className="min-h-11 rounded-xl border border-border bg-surface-2 px-4 text-xs font-semibold text-fg-soft transition-colors hover:border-border-strong"
          >
            Broaden price
          </motion.button>
          {search && (
            <motion.button
              onClick={() => setSearch('')}
              whileTap={reduceMotion ? undefined : { scale: 0.96 }}
              transition={pressSpring}
              className="min-h-11 rounded-xl border border-border bg-surface-2 px-4 text-xs font-semibold text-fg-soft transition-colors hover:border-border-strong"
            >
              Clear search
            </motion.button>
          )}
        </div>
      </motion.div>
    )
  } else {
    body = (
      <StationList
        rows={panelRows}
        selectedId={selectedId}
        onSelect={setSelectedId}
        scrollRef={listRef}
        scrollMargin={listOffsets.margin}
        scrollPaddingStart={listOffsets.heading}
      />
    )
  }
  const bodyKey = query.isLoading && !effectiveData ? 'loading' : query.isError && !effectiveData ? 'error' : !panelRows.length ? 'empty' : 'list'

  return (
    <div className="discovery-app relative h-[100dvh] w-full overflow-hidden" data-view={fullList ? 'list' : 'split'}>
      <MapErrorBoundary>
        {mapActive ? (
          <Suspense fallback={<div className="absolute inset-0 bg-surface-2" aria-hidden="true" />}>
            <MapView
              stations={mapRows}
              selectedId={selectedId}
              onSelect={setSelectedId}
              theme={theme}
              userCoords={geo.coords}
              onUserViewportIntent={geo.cancel}
              onViewportChange={(nextViewport) => {
                // Map reports are debounced. A gesture made before a newer search
                // choice must not cancel GPS or schedule another search afterward.
                if (nextViewport.userInitiated && (nextViewport.userInitiatedAt ?? 0) < explicitAreaIntentAt.current) return
                setViewport(nextViewport)
                // Region shortcuts own a geographic scope even without WebGL. Only
                // intentional map exploration hands that scope back to the viewport.
                if (nextViewport.userInitiated && (overview || !isLiveBbox(nextViewport.bbox))) {
                  setOverview(true)
                  setOrigin(null)
                  setCommittedBbox(null)
                  setRegion('')
                }
              }}
              flyTarget={flyTarget}
              initialView={SPAIN_VIEW}
            />
          </Suspense>
        ) : (
          <div className="absolute inset-0 bg-surface-2" aria-hidden="true" />
        )}
      </MapErrorBoundary>
      <header className="app-header">
        <a href="/" className="brand-lockup" aria-label="Volta Go home">
          <img src="/volta-icon.svg" alt="" className="h-9 w-9" />
          <span>Volta <span className="font-normal">Go</span><span className="brand-period">.</span></span>
        </a>
        <p className="hidden text-sm text-muted md:block">Good stops. Less guesswork.</p>
        <div className="ml-auto flex items-center gap-2">
          <IconButton label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'} onClick={toggle}>
            <ThemeIcon theme={theme} />
          </IconButton>
        </div>
      </header>
      <nav className="region-nav glass" aria-label="Explore a region">
        {REGIONS.map((item) => (
          <button type="button" key={item.label}
            aria-pressed={overview && region === item.label}
            onClick={() => {
              claimSearchArea()
              geo.cancel()
              lastAutoAreaKey.current = null
              setRegion(item.label)
              setOverview(true)
              setOrigin(null)
              setSelectedId(null)
              setCommittedBbox(item.bbox)
              setFlyTarget({ ...item, nonce: Date.now() })
            }}>
            {item.label}
          </button>
        ))}
      </nav>

      {/* Search this area — floating glass pill */}
      <AnimatePresence>
        {showAreaPill && (
          <motion.button
            type="button"
            onClick={searchThisArea}
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.96 }}
            transition={reduceMotion ? quickEase : settleSpring}
            whileTap={reduceMotion ? undefined : { scale: 0.98 }}
            className="area-search glass-strong absolute z-30 inline-flex min-h-11 overflow-hidden rounded-full border border-border px-3.5 py-2 text-sm font-semibold text-fg hover:border-brand"
          >
            <span className="relative z-10 inline-flex items-center gap-1.5">
              <Search size={15} className="text-brand-strong" />
              Search this area
            </span>
            <motion.span
              key={`${areaMotionKey}:${viewport ? bboxKey(viewport.bbox) : ''}`}
              aria-hidden="true"
              className="absolute inset-x-0 bottom-0 h-0.5 origin-left bg-brand"
              initial={{ scaleX: 0, opacity: reduceMotion ? 0 : 0.55 }}
              animate={reduceMotion ? { opacity: 0 } : { scaleX: 1, opacity: 0.55 }}
              transition={{ duration: areaSearchDelay / 1000, ease: 'linear' }}
            />
          </motion.button>
        )}
      </AnimatePresence>

      {/* The map popup is an enhancement. The panel always remains the accessible station path. */}
      <motion.section
        className="discovery-panel bg-surface absolute z-20 flex min-h-0 flex-col overflow-hidden"
        initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={smoothSpring}
      >
        <SheetGrabber expanded={fullList} onChange={setFullList} />
        <div className="discovery-search">
          {/* The headline sells the product on arrival. Once the driver has a place, the
              space goes to results. */}
          <AnimatePresence initial={false}>
            {overview && (
              <motion.div
                key="hero"
                className="discovery-hero overflow-hidden"
                initial={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
                animate={reduceMotion ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
                exit={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
                transition={smoothSpring}
              >
                <p className="discovery-title">Find your next<br />charge<span className="text-mark">.</span></p>
                <p className="discovery-intro">Free first. Cheap next. Always know what to check.</p>
              </motion.div>
            )}
          </AnimatePresence>
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1"><SearchBar value={search} onChange={setSearch} onPlaceSelect={onPlaceSelect} /></div>
            <IconButton label="Use my location" onClick={requestLocation}>
              <LocateFixed size={20} className={geo.status === 'locating' ? 'animate-pulse' : ''} />
            </IconButton>
          </div>
          <div className="mt-3">
            <FilterRail filters={filters} onPriceChange={(maxPrice) => setFilters((f) => ({ ...f, maxPrice }))} onToggle={toggleFilter} onReset={() => setFilters({ ...DEFAULT_FILTERS })} />
          </div>
        </div>

        {/* One scroller: the count stays pinned while status notes scroll away with the
            results, so the list gets the height instead of fixed chrome. */}
        <div
          ref={listRef}
          data-result-scroller
          className="result-scroller relative min-h-0 flex-1 overflow-y-auto"
          onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 2)}
          // On a phone, reading down the list means the driver wants the list: raise the
          // sheet like a native one. Only a finger drives this; programmatic scrolls
          // (map selection, keyboard focus) must never hide the map.
          onTouchMove={(e) => {
            if (!fullList && e.currentTarget.scrollTop > 48 && window.matchMedia('(max-width: 767px)').matches) setFullList(true)
          }}
        >
          <div ref={headingRef} className="results-heading" data-scrolled={scrolled || undefined}>
            <div className="min-w-0">
              <p className="text-xs text-muted" data-search-area>{placeLabel}</p>
              <h1 className="mt-0.5 text-lg font-semibold">
                <CountSwap value={panelRows.length} className="tabular-nums" />{' '}
                {overview ? scopeLabel : 'nearby'}
              </h1>
            </div>
            <div className="flex flex-none items-center gap-1">
              <ViewToggle expanded={fullList} onChange={setFullList} />
              <IconButton label={overview ? 'Refresh known charger leads' : 'Refresh live data'} onClick={() => query.refetch()}>
                <RefreshCw size={16} className={query.isFetching ? 'animate-spin' : ''} />
              </IconButton>
            </div>
          </div>

          <div ref={statusRef} className="discovery-status space-y-2">
            {/* role=status: screen readers hear the auto-refresh happen (qa_realtime_refresh). */}
            <motion.div
              className="relative flex items-center gap-2 overflow-hidden rounded-lg px-0.5 py-0.5 text-xs"
              role="status"
              aria-live="polite"
              aria-atomic="true"
              layout
              transition={settleSpring}
            >
              <AnimatePresence initial={false}>
                {query.dataUpdatedAt > 0 && !query.isFetching && (
                  <motion.span
                    key={query.dataUpdatedAt}
                    aria-hidden="true"
                    variants={statusFlash(Boolean(reduceMotion))}
                    initial="hidden"
                    animate="show"
                    exit="exit"
                    className="pointer-events-none absolute inset-y-0 left-0 w-full origin-left rounded-lg bg-brand"
                  />
                )}
              </AnimatePresence>
              <span className={`live-dot ${trustIsNotLive ? 'live-dot-muted' : ''}`} />
              <span className="text-muted">{trustStatus.label}</span>
              <span className="sr-only badge tabular-nums" aria-label={trustStatus.badge}>
                {trustIsNotLive || responseMixedLive ? (
                  trustStatus.badge
                ) : (
                  <><CountSwap value={liveReady} /> live · <CountSwap value={leadCount} /> leads</>
                )}
              </span>
            </motion.div>
            <AnimatePresence initial={false}>
              {query.isFetching && (
                <motion.div
                  aria-hidden="true"
                  className="h-1 overflow-hidden rounded-full bg-surface-2"
                  variants={fadeSlide(Boolean(reduceMotion), 3)}
                  initial="hidden"
                  animate="show"
                  exit="exit"
                >
                  <motion.div
                    className="h-full w-1/3 rounded-full bg-brand"
                    initial={reduceMotion ? { opacity: 0.7 } : { x: '-120%' }}
                    animate={reduceMotion ? { opacity: 1 } : { x: ['-120%', '320%'] }}
                    transition={
                      reduceMotion
                        ? { duration: 0.18, ease: [0.2, 0, 0, 1] }
                        : { duration: 1.05, repeat: Infinity, ease: [0.4, 0, 0.2, 1] }
                    }
                  />
                </motion.div>
              )}
            </AnimatePresence>
            <CoverageBanner coverage={coverage} cache={effectiveData?.cache} />
            {effectiveData?.meta?.truncated && (
              <p role="status" className="text-xs leading-relaxed text-muted">
                Showing part of this area’s charger inventory. Zoom in or search a city to see more.
              </p>
            )}
            {flowHint && (overview && geo.status === 'idle' ? (
              <div data-driver-guidance role="status" className="overview-guidance">
                <span>Search a city or use your location to check nearby chargers.</span>
                <ArrowUpRight size={16} aria-hidden="true" />
              </div>
            ) : <FlowHint {...flowHint} />)}
          </div>

          <div ref={bodyRef} className="results-body">
            {filters.available ? (
              // Open now is a safety boundary: remove stale/non-live rows immediately.
              // An exit animation briefly left old leads visible under an active chip.
              <div key={bodyKey}>{body}</div>
            ) : (
              <AnimatePresence initial={false} mode="wait">
                <motion.div
                  key={bodyKey}
                  variants={fadeSlide(Boolean(reduceMotion), 8)}
                  initial="hidden"
                  animate="show"
                  exit="exit"
                >
                  {body}
                </motion.div>
              </AnimatePresence>
            )}
          </div>
        </div>

        {/* Pinned, not after the list: at the end of a long virtual list it would be
            unreachable, and focusing it would scroll past every result. */}
        <DataHonestyDisclosure />
      </motion.section>
    </div>
  )
}
