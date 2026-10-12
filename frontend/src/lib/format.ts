import type { Bbox, ChargersResponse, Coords, CorroborationTier, DecisionLevel, FilterKey, Filters, Station } from '../types'

/** Title-case a station name with Spanish locale, preserving small connectors. */
export function displayName(name = ''): string {
  const clean = name.replace(/\s+/g, ' ').trim()
  if (!clean) return 'Charging point'
  if (clean === clean.toUpperCase() && clean.length > 4) {
    return clean
      .toLocaleLowerCase('es-ES')
      .split(' ')
      .map((word) =>
        word
          .split(/([-/])/)
          .map((part) =>
            /[-/]/.test(part) ? part : part.charAt(0).toLocaleUpperCase('es-ES') + part.slice(1),
          )
          .join(''),
      )
      .join(' ')
  }
  return clean
}

export function priceText(st: Station): string {
  if (st.is_free) return 'Free'
  const value = st.price_min
  if (value == null) return '—'
  if (value === 0) return 'Free'
  return `€${value.toFixed(2)}`
}

export function priceUnit(st: Station): string {
  if (st.is_free || st.price_min === 0) return ''
  return '/kWh'
}

/** A numeric/free price is displayable as a map price only with explicit evidence. */
export function hasVerifiedPrice(st: Station): boolean {
  return st.verification?.price_verified === true
}

function isPublicWebHostname(rawHostname: string): boolean {
  const hostname = rawHostname.toLowerCase().replace(/\.$/, '')
  if (
    !hostname ||
    hostname.length > 253 ||
    hostname === 'localhost' ||
    hostname.endsWith('.localhost') ||
    hostname.endsWith('.local') ||
    hostname.endsWith('.internal') ||
    hostname.endsWith('.home.arpa') ||
    hostname.includes(':')
  ) {
    // Literal IPv6 is intentionally fail-closed here. The product's verified handoffs
    // use public DNS names; accepting an address literal adds local/link-only edge cases.
    return false
  }

  const labels = hostname.split('.')
  const numeric = labels.every((label) => /^\d+$/.test(label))
  if (numeric) {
    if (labels.length !== 4) return false
    const octets = labels.map(Number)
    if (octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false
    const [a, b] = octets
    return !(
      a === 0 ||
      a === 10 ||
      a === 127 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 0) ||
      (a === 192 && b === 88 && octets[2] === 99) ||
      (a === 192 && b === 168) ||
      (a === 198 && (b === 18 || b === 19)) ||
      (a === 198 && b === 51 && octets[2] === 100) ||
      (a === 203 && b === 0 && octets[2] === 113) ||
      a >= 224
    )
  }

  // Single-label names are local/intranet identities, not public web hosts. Reject
  // malformed labels too; a syntactically odd host must not become a device-local
  // resolver request merely because it contains a dot.
  return (
    labels.length >= 2 &&
    labels.every((label) =>
      /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label),
    )
  )
}

/** Only absolute, credential-free, public-web handoffs are allowed into href/src attributes. */
export function publicWebUrl(value?: string | null): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  try {
    const url = new URL(value.trim())
    if (!['http:', 'https:'].includes(url.protocol) || !isPublicWebHostname(url.hostname)) return null
    if (url.username || url.password) return null
    return url.href
  } catch {
    return null
  }
}

export function mapsUrl(st: Station): string {
  const nav = (st.navigation || {}) as Record<string, string>
  for (const candidate of [nav.google, nav.directions, nav.apple]) {
    const safe = publicWebUrl(candidate)
    if (safe) return safe
  }
  return `https://www.google.com/maps/search/?api=1&query=${st.lat},${st.lon}`
}

export interface RemoteStartAction {
  href: string
  label: string
  ariaLabel: string
  title: string
}

export function stationPhoto(st: Station) {
  for (const photo of st.media?.photos || []) {
    if (photo?.display_rights_approved !== true) continue
    const full = publicWebUrl(photo?.url)
    const thumbnail = publicWebUrl(photo?.thumbnail_url)
    const src = thumbnail || full
    if (!src) continue
    return {
      src,
      href: full || thumbnail || src,
      source: photo.source || 'public source',
      sourceUrl: publicWebUrl(photo.source_url) || undefined,
      caption: photo.caption || st.media?.note || '',
    }
  }
  return null
}

/**
 * Card-level provider handoff is reserved for public start/check pages and exact provider
 * station/app pages only.
 * Directory/store/map URLs remain available in Details as evidence; the card action
 * should not imply a charger can be started remotely when the public source cannot
 * prove that.
 */
export function remoteStartAction(st: Station): RemoteStartAction | null {
  const primary = st.primary_source
  if (!primary?.url) return null
  const kind = (primary.kind || '').toLowerCase()
  if (!['start_page', 'provider_station_page'].includes(kind)) return null

  const provider = primary.name || st.source_label || st.source || 'provider'
  const providerKey = provider.toLowerCase()
  const safeUrl = publicWebUrl(primary.url)
  if (!safeUrl) return null

  return {
    href: safeUrl,
    label: providerKey.includes('gaia')
      ? 'Start/check Gaia'
      : providerKey.includes('fenie') || providerKey.includes('feníe')
        ? 'Start/check Feníe'
        : 'Start/check',
    ariaLabel: `Open public provider page for ${displayName(st.name)}`,
    title: 'Public provider start/check page. Do not start a session unless you are at the charger.',
  }
}

export function priceLimitText(maxPrice: number | null): string {
  if (maxPrice === null) return 'No limit'
  if (maxPrice === 0) return 'Free'
  return `≤ €${maxPrice.toFixed(2)}/kWh`
}

export function resultScopeLabel(filters: Filters): string {
  if (filters.maxPrice === 0) return 'free chargers'
  if (filters.maxPrice !== null) return `chargers ${priceLimitText(filters.maxPrice)}`
  return 'public chargers'
}

export function distanceText(km?: number): string {
  if (km == null) return ''
  if (km < 1) return `${Math.round(km * 1000)} m`
  if (km < 10) return `${km.toFixed(1)} km`
  return `${Math.round(km)} km`
}

export function durationText(min?: number | null): string {
  if (min == null || min < 0) return ''
  if (min < 60) return `${Math.round(min)} min`
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return m ? `${h}h ${m}m` : `${h}h`
}

/** OSM sometimes stores watts in a kW field (7400 → 7.4 kW). Public DC rarely exceeds 500 kW. */
export function sanePowerKw(kw?: number | null): number | null {
  if (kw == null || !Number.isFinite(kw) || kw <= 0) return null
  if (kw > 500) return kw / 1000
  return kw
}

export function powerText(kw?: number | null): string {
  const n = sanePowerKw(kw)
  if (n == null) return ''
  if (n >= 10) return `${Math.round(n)} kW`
  return `${n % 1 === 0 ? n : n.toFixed(1)} kW`
}

export function ageText(iso?: string, minutes?: number | null): string {
  let mins = minutes
  if (mins == null && iso) {
    const t = Date.parse(iso)
    if (!Number.isNaN(t)) mins = (Date.now() - t) / 60000
  }
  if (mins == null) return ''
  if (mins < 1) return 'just now'
  if (mins < 60) return `${Math.round(mins)} min ago`
  const h = mins / 60
  if (h < 48) return `${Math.round(h)} h ago`
  const days = h / 24
  if (days < 60) return `${Math.round(days)} d ago`
  if (days < 730) return `${Math.round(days / 30)} mo ago`
  return `${Math.round(days / 365)} y ago`
}

/** Card trust copy: lead evidence age is not the same claim as live freshness. */
export function stationAgeText(
  st: Pick<Station, 'lead' | 'last_seen' | 'freshness_minutes'>,
): string {
  const age = ageText(st.last_seen, st.freshness_minutes)
  if (!age) return ''
  return `${st.lead ? 'As of' : 'Updated'} ${age}`
}

export const DECISION_META: Record<DecisionLevel, { label: string; cls: string }> = {
  go: { label: 'Go now', cls: 'pill-go' },
  check: { label: 'Check first', cls: 'pill-check' },
  verify: { label: 'Verify first', cls: 'pill-verify' },
  // The backend also emits these (e.g. "Price lead" store-locator rows, "Off cap" rows
  // visible when the price chips are off). Missing entries crashed DecisionPill.
  lead: { label: 'Price lead', cls: 'pill-lead' },
  avoid: { label: 'Avoid', cls: 'pill-avoid' },
}

export function decisionLevel(st: Station): DecisionLevel {
  const level = st.decision?.level
  if (level && level in DECISION_META) return level
  if (!st.lead) return 'check'
  return 'verify'
}

export type SourceTier = 'live' | 'official' | 'directory' | 'community'

export function sourceTier(st: Station): SourceTier {
  if (!st.lead) return 'live'
  const r = (st.reliability || '').toLowerCase()
  if (r.startsWith('official')) return 'official'
  if (r.includes('directory') || r.includes('map') || r.includes('locator')) return 'directory'
  return 'community'
}

function hasExactDirectoryLocation(st: Station): boolean {
  if (sourceTier(st) !== 'directory') return false
  if (st.verification?.location_verified !== true) return false
  const primary = st.primary_source
  const kind = (primary?.kind || '').toLowerCase()
  const quality = (primary?.quality || primary?.exactness || '').toLowerCase()
  if (['station_page', 'start_page', 'provider_station_page', 'map_object', 'live_map', 'official_data'].includes(kind)) return true
  if (['exact', 'map', 'official'].includes(quality)) return true

  const url = (primary?.url || st.source_url || '').toLowerCase()
  return (
    url.includes('map.electromaps.com') ||
    /electromaps\.com\/(?:en|es|fr|pt)\/p\//.test(url) ||
    url.includes('chargemap.com/')
  )
}

function hasExactOsmLocation(st: Station): boolean {
  if (!Number.isFinite(st.lat) || !Number.isFinite(st.lon)) return false
  const url = (st.primary_source?.url || st.source_url || '').toLowerCase()
  return /openstreetmap\.org\/(?:node|way|relation)\/\d+/.test(url)
}

/**
 * Map pins need a stricter bar than list rows. The list may show weak/source-backed
 * leads with honest "verify first" copy, but a map point visually claims location.
 * Keep live rows, official locations, exact public directory station pages, and
 * OSM node/way/relation objects that already carry exact coordinates. Those OSM
 * pins stay Not live — they never become Go-now.
 */
export function isMapLocationVerified(st: Station): boolean {
  if (!Number.isFinite(st.lat) || !Number.isFinite(st.lon)) return false
  if (!st.lead) return st.verification?.location_verified !== false

  if (st.verification?.route_checked === true) return true
  const tier = sourceTier(st)
  if (tier === 'directory' && hasExactDirectoryLocation(st)) return true
  if (hasExactOsmLocation(st)) return true
  // Official registry coordinates prove the place even when no tariff is published; the
  // pin then carries no price (see hasVerifiedPrice).
  return tier === 'official' && st.verification?.location_verified === true
}

/** True route-ready means a live row the backend says is actionable now. Leads never qualify. */
export function isRouteReady(st: Station): boolean {
  if (st.lead || st.cache_stale === true || decisionLevel(st) !== 'go') return false
  if (st.verification?.live_verified !== true) return false

  const verificationLevel = st.verification?.level
  if (verificationLevel && verificationLevel !== 'live') return false

  const available = st.connector_summary?.available ?? st.available
  if (available != null) return available > 0
  return (st.status || '').trim().toUpperCase() === 'AVAILABLE'
}

/**
 * Near-home ordering keeps Volta's fail-closed contract, then makes distance decisive.
 * A live Go-now row always wins; live Check-first rows come next; source-checked
 * official/directory backups follow, then paid and unpriced chargers. Within each
 * honesty tier, the nearest row wins.
 * The input array is never mutated so React Query's cached response stays untouched.
 */
export function sortNearbyBySafetyAndDistance(rows: Station[], origin: Coords | null): Station[] {
  if (!origin) return rows

  const distanceFromOrigin = (st: Station): number => {
    if (!Number.isFinite(st.lat) || !Number.isFinite(st.lon)) return Number.POSITIVE_INFINITY
    const earthRadiusKm = 6371
    const toRadians = (value: number) => (value * Math.PI) / 180
    const dLat = toRadians(st.lat - origin.lat)
    const dLon = toRadians(st.lon - origin.lon)
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRadians(origin.lat)) * Math.cos(toRadians(st.lat)) * Math.sin(dLon / 2) ** 2
    return earthRadiusKm * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
  }

  const tier = (st: Station): number => {
    if (isRouteReady(st)) return 0
    const decision = decisionLevel(st)
    if (!st.lead && decision === 'check') return 1
    if (decision === 'avoid') return 5
    // Paid and unpriced chargers (backend `lead`) follow every free/cheap backup.
    if (decision === 'lead') return 4
    const source = sourceTier(st)
    if (!st.lead || st.verification?.route_checked === true || source === 'official' || source === 'directory') {
      return 2
    }
    return 3
  }

  return rows
    .map((station, index) => {
      const distance = distanceFromOrigin(station)
      if (!Number.isFinite(distance)) return { station, index, distance }
      return {
        station: {
          ...station,
          distance_km: Math.round(distance * 100) / 100,
          drive_eta_min_est: Math.max(2, Math.round((distance / 32) * 60)),
        },
        index,
        distance,
      }
    })
    .sort((a, b) => {
      const tierDelta = tier(a.station) - tier(b.station)
      if (tierDelta !== 0) return tierDelta

      if (a.distance !== b.distance) return a.distance - b.distance

      const scoreDelta = (b.station.recommendation_score ?? 0) - (a.station.recommendation_score ?? 0)
      if (scoreDelta !== 0) return scoreDelta
      return a.index - b.index
    })
    .map(({ station }) => station)
}

export const TIER_META: Record<SourceTier, { label: string; tone: 'brand' | 'default' | 'warn' }> = {
  live: { label: 'Live', tone: 'brand' },
  official: { label: 'Official', tone: 'brand' },
  directory: { label: 'Directory', tone: 'default' },
  community: { label: 'Community', tone: 'default' },
}

export function isParkingRisk(st: Station): boolean {
  const kind = st.parking?.kind || ''
  return st.parking?.paid === true || kind === 'paid_or_metered' || kind === 'time_limited_or_validated'
}

export interface PrivateAccess {
  label: string
  short: string
  icon: 'lock' | 'store'
}

/**
 * Private / customers-only parking — i.e. NOT free public access. Covers proven access
 * restrictions plus retail parking lots (Lidl/Consum/Carrefour etc.), which are private
 * property where you're expected to be a customer even if access wasn't separately proven.
 * Street/municipal and public-access spots return null (genuinely free public parking).
 */
export function privateAccess(st: Station): PrivateAccess | null {
  const level = (st.access?.level || '').toLowerCase()
  if (level === 'restricted') return { label: 'Restricted access', short: 'Restricted', icon: 'lock' }
  if (
    level === 'customer' ||
    ['retail_parking', 'facility_or_customer_parking'].includes(st.parking?.kind || '')
  )
    return { label: 'Customer/visitor parking', short: 'Customers only', icon: 'store' }
  return null
}

export function isPrivateAccess(st: Station): boolean {
  return privateAccess(st) != null
}

export function tripTimeText(st: Station): string {
  const drive = st.drive_eta_min_est
  if (drive == null) return ''
  return durationText(drive)
}

export interface AvailabilityInfo {
  live: boolean
  available: number | null
  /** Connectors in use right now (occupied + charging). */
  busy: number | null
  /** Connectors out of service right now. */
  out: number | null
  unknown: number | null
  total: number | null
  maxKw: number | null
  tone: 'go' | 'check' | 'danger' | 'muted'
  label: string
}

/** Real-time connector availability (free / in-use / out) + socket count + max power. */
export function availability(st: Station): AvailabilityInfo {
  const cs = st.connector_summary
  let total = cs?.total ?? st.total_evse ?? st.connector_count ?? (st.connectors?.length || null)
  const available = cs?.available ?? st.available ?? null
  // Upstream feeds occasionally report available > total; clamp for a sane display.
  if (available != null && total != null && available > total) total = available
  const maxKw = cs?.max_power_kw ?? st.max_power_kw ?? null
  const busy = cs?.busy ?? null
  const out = cs?.out_of_service ?? null
  const unknown = cs?.unknown ?? null
  const live = !st.lead
  const base = { live, available, busy, out, unknown, total, maxKw }

  if (!live) return { ...base, tone: 'muted', label: 'Not live' }
  if (available == null || total == null) return { ...base, tone: 'muted', label: 'Live' }
  if (available > 0) return { ...base, tone: 'go', label: `${available}/${total} available` }
  if (total > 0 && (out ?? 0) >= total) return { ...base, tone: 'danger', label: 'Out of service' }
  return { ...base, tone: 'check', label: `Busy · 0/${total} free` }
}

/** Client-side display filters layered on top of the server's source-honest dataset. */
export function passesFilters(st: Station, filters: Filters, search: string): boolean {
  if (filters.leads === false && st.lead) return false
  if (filters.available && !isRouteReady(st)) return false

  if (filters.maxPrice !== null) {
    // Unknown tariffs cannot satisfy a chosen cap. An explicit free claim remains
    // usable when the source has no numeric tariff; contradictory numeric prices win.
    const price = st.price_min ?? (st.is_free ? 0 : null)
    if (price === null || !Number.isFinite(price) || price < 0 || price > filters.maxPrice) return false
  }

  if (filters.tesla && st.tesla_ok === false) return false
  if (filters.fast && !st.fast) return false

  if (filters.verified) {
    const tier = sourceTier(st)
    const routeChecked = st.verification?.route_checked === true
    if (!(tier === 'live' || tier === 'official' || tier === 'directory' || routeChecked)) return false
  }

  if (filters.parking && isParkingRisk(st)) return false

  if (search.trim()) {
    const q = search.trim().toLowerCase()
    const hay = [st.name, st.city, st.address, st.source, st.source_label, ...(st.connector_labels || [])]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    if (!hay.includes(q)) return false
  }
  return true
}

/** True when a station falls inside the current map viewport (used to scope the list to what's on screen). */
export function inViewport(st: Station, bbox: Bbox | null): boolean {
  if (!bbox) return true
  if (typeof st.lat !== 'number' || typeof st.lon !== 'number') return true
  return st.lat >= bbox.south && st.lat <= bbox.north && st.lon >= bbox.west && st.lon <= bbox.east
}

export interface CorroborationInfo {
  count: number
  tier: CorroborationTier
  families: string[]
}

/**
 * How many INDEPENDENT public sources agree a station exists at this price/location.
 * Falls back gracefully to source_count/sources length so it works even before the
 * backend corroboration block is present. This raises confidence in existence/price —
 * it never implies the station is live (that stays the source tier / availability).
 */
export function corroboration(st: Station): CorroborationInfo {
  const c = st.corroboration
  let count = c?.independent_sources ?? st.source_count ?? (st.sources ? st.sources.length : 0)
  if (!count || count < 1) count = 1
  const tier: CorroborationTier =
    c?.tier ?? (count >= 3 ? 'strongly_corroborated' : count >= 2 ? 'corroborated' : 'single')
  return { count, tier, families: c?.families ?? [] }
}

export interface ChargersDataChoice {
  data?: ChargersResponse
  /** True when we're painting instant intel leads while the live fetch runs. */
  usingInstant: boolean
  /** True when the live fetch FAILED and we fell back to leads-only (fail-closed). */
  liveFailed: boolean
}

/**
 * Intelligence-first render selector. In live mode, paint the instant leads-only overview
 * while the live fetch runs — both on a cold first load AND when keepPreviousData is holding
 * another area's rows that all fall outside the current viewport (which would otherwise read
 * "0 in view" for the whole fetch). Once real live data for the area exists, always prefer it.
 *
 * FAIL-CLOSED (Prime Directive 4): when the live fetch ERRORS, never keep presenting the
 * previously fetched live cards as current — TanStack retains last-good data on error, which
 * would silently show stale "Go now / N free" claims. Fall back to the leads-only instant
 * layer (honestly labeled "Not live") and let the UI disclose the failure.
 */
export function chooseChargersData(args: {
  overview: boolean
  live?: ChargersResponse
  instant?: ChargersResponse
  isFetching: boolean
  isPlaceholder: boolean
  isError?: boolean
  viewportBbox: Bbox | null
}): ChargersDataChoice {
  const { overview, live, instant, isFetching, isPlaceholder, isError, viewportBbox } = args
  // A region shortcut can return from a live query while keepPreviousData still
  // carries its rows. Only the overview inventory may bridge that mode transition.
  // Only the national inventory is stored offline, never a user's viewport.
  // Retain that honest fallback when the regional request is missing or fails.
  if (overview) {
    const useInventory = isPlaceholder || !live || (isError && !isFetching)
    return { data: useInventory ? instant : live, usingInstant: false, liveFailed: false }
  }
  if (isError && !isFetching) {
    // Live source unreachable: drop stale live rows, keep honest leads if we have them.
    return { data: instant, usingInstant: false, liveFailed: true }
  }
  if (!live) {
    return instant
      ? { data: instant, usingInstant: isFetching, liveFailed: false }
      : { data: undefined, usingInstant: false, liveFailed: false }
  }
  if (isFetching && isPlaceholder && instant) {
    const rows = [...(live.stations || []), ...(live.leads || [])]
    const anyInView = rows.some((st) => inViewport(st, viewportBbox))
    if (!anyInView) return { data: instant, usingInstant: true, liveFailed: false }
  }
  return { data: live, usingInstant: false, liveFailed: false }
}

export interface TariffWindowInfo {
  /** Human label from the backend pricing_status, e.g. "Now Free 22:00–08:00" / "Cheap window €0.20 22:00–08:00". */
  label: string
  /** True when the cheap/free window applies right now. */
  currentlyCheap: boolean
}

export interface StatusTrustSummary {
  label: string
  badge: string
}

/** True when at least one final station row still has current live authority. */
export function responseHasFreshLiveAuthority(response?: ChargersResponse): boolean {
  if (typeof response?.cache?.fresh_live_authority === 'boolean') {
    return response.cache.fresh_live_authority
  }
  return Boolean(response?.stations?.some((station) => (
    station.lead !== true &&
    station.cache_stale !== true &&
    station.verification?.live_verified === true
  )))
}

/** A successful HTTP response can still have no live authority after upstream failure/offline fallback. */
export function responseHasNoLiveAuthority(response?: ChargersResponse): boolean {
  if (response?.meta?.offline === true) return true
  const fresh = responseHasFreshLiveAuthority(response)
  if (response?.cache?.live_failed === true && !fresh) return true
  return response?.cache?.stale === true && !fresh
}

/** Some rows are current while other source rows are explicitly stale. */
export function responseHasMixedLiveAuthority(response?: ChargersResponse): boolean {
  return response?.cache?.mixed_live === true || (
    responseHasFreshLiveAuthority(response) && Boolean(response?.stations?.some((station) => station.cache_stale === true))
  )
}

/**
 * The top status row describes the response's trust level, not merely when React fetched it.
 * A national overview contains source-backed leads only, while a stale response has explicitly
 * lost live authority; neither may be announced as "Live" even when the HTTP request just ran.
 */
export function statusTrustSummary(args: {
  overview: boolean
  sourceBackedOnly: boolean
  isFetching: boolean
  stale: boolean
  mixedLive?: boolean
  updated: string
  liveReady: number
  leadCount: number
}): StatusTrustSummary {
  const { overview, sourceBackedOnly, isFetching, stale, mixedLive = false, updated, liveReady, leadCount } = args
  // A background retry must not temporarily restore live wording to a response that has
  // already lost live authority. The refresh spinner remains visible on its own control.
  if (mixedLive) {
    return {
      label: isFetching
        ? 'Refreshing live + cached evidence…'
        : updated
          ? `Live source + cached evidence · updated ${updated}`
          : 'Live source + cached evidence',
      badge: `${liveReady} live · cached fallback`,
    }
  }
  if (stale) return { label: 'Cached evidence · not live', badge: 'Not live' }
  // In nearby mode the instant intelligence layer remains visible while live data loads and
  // after a live-source failure. Its cards are useful backups, but they have no live authority.
  if (sourceBackedOnly) {
    return { label: 'Source-backed leads · not live', badge: `${leadCount} leads · not live` }
  }
  if (isFetching) {
    return {
      label: overview ? 'Refreshing known chargers…' : 'Refreshing…',
      badge: overview ? `${leadCount} leads · not live` : `${liveReady} live · ${leadCount} leads`,
    }
  }
  if (overview) return { label: 'Source-backed leads · not live', badge: `${leadCount} leads · not live` }
  return {
    label: updated ? `Live · updated ${updated}` : 'Live',
    badge: `${liveReady} live · ${leadCount} leads`,
  }
}

/**
 * Time-of-use tariff info — only when the station has GENUINE time-bound pricing
 * (a window with valid_from/valid_until/days). A flat always-on price returns null:
 * showing a clock chip for it would imply a schedule that doesn't exist.
 */
export function tariffWindow(st: Station): TariffWindowInfo | null {
  const windows = st.pricing || []
  const timeBound = windows.some((w) => w.valid_from || w.valid_until || w.days)
  if (!timeBound) return null
  const status = st.price_window_status
  const label = status?.label || st.price_label
  if (!label) return null
  return { label, currentlyCheap: status?.is_currently_within_standard !== false }
}

/** Honest, readable city/province label — fixes ALL-CAPS source values (e.g. "BARCELONA"). */
export function cityLabel(city?: string): string {
  const c = (city || '').trim()
  if (!c) return ''
  if (c === c.toLocaleUpperCase('es-ES') && c.length > 3) {
    return c
      .toLocaleLowerCase('es-ES')
      .replace(/(^|[\s\-/])([a-záéíóúñü])/g, (_m, pre, ch: string) => pre + ch.toLocaleUpperCase('es-ES'))
  }
  return c
}

export const DEFAULT_FILTERS: Filters = {
  available: false,
  verified: false,
  parking: false,
  maxPrice: null,
  tesla: false,
  fast: false,
  leads: true,
}

export const FILTER_LABELS: { key: FilterKey; label: string }[] = [
  { key: 'available', label: 'Open now' },
  { key: 'verified', label: 'Source checked' },
  { key: 'parking', label: 'Easy parking' },
  { key: 'tesla', label: 'Tesla OK' },
  { key: 'fast', label: 'Fast' },
  { key: 'leads', label: 'Leads (Verify first)' },
]
