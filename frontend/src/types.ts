// Types mirroring the Volta Python API (server.py) enriched rows.
// Most fields are optional because confidence/source coverage varies per row.

export type DecisionLevel = 'go' | 'check' | 'verify' | 'lead' | 'avoid'

export interface Decision {
  level: DecisionLevel
  label: string
  reason: string
}

export interface Connector {
  connector_id?: string
  label?: string
  type?: string
  status?: string
  status_label?: string
  status_icon?: string
  power_kw?: number | null
  price?: string
  is_free?: boolean
  tariff_text?: string | null
  tesla_ok?: boolean | null
  fast?: boolean
  source_url?: string | null
}

export interface Claim {
  type?: string
  kind?: string
  source?: string
  url?: string | null
  price_min?: number | null
  cost_hint?: string | null
  summary?: string
}

export interface ClaimConflict {
  level?: string
  summary?: string
  charge_url?: string
  parking_url?: string
}

export interface ParkingProfile {
  kind?: string
  label?: string
  paid?: boolean
  note?: string
  cost_hint?: string | null
  free_likelihood?: string
  peak_hint?: string
}

export interface AccessProfile {
  level?: string
  label?: string
  note?: string
}

export interface SourceRef {
  name?: string
  url?: string
  evidence?: string
  license?: string
  license_url?: string
  attribution?: string
  transformation?: string
  source_fetched_at?: string
  source_last_updated?: string
  kind?: string
  quality?: string
  exactness?: string
}

export interface MediaPhoto {
  url?: string
  thumbnail_url?: string
  source?: string
  source_url?: string
  caption?: string
  license?: string
  /** Explicit owner/legal approval for in-app display or automatic loading. */
  display_rights_approved?: boolean
}

export interface MediaProfile {
  photos?: MediaPhoto[]
  photo_status?: string
  note?: string
}

export interface Verification {
  price_verified?: boolean
  location_verified?: boolean
  route_checked?: boolean
  live_verified?: boolean
  level?: string
  label?: string
  note?: string
}

export type CorroborationTier = 'single' | 'corroborated' | 'strongly_corroborated'

export interface Corroboration {
  independent_sources?: number
  families?: string[]
  tier?: CorroborationTier
}

export interface ConnectorSummary {
  available?: number
  busy?: number
  charging?: number
  out_of_service?: number
  unknown?: number
  total?: number
  max_power_kw?: number | null
  details?: unknown
}

export interface PricingWindow {
  price?: number
  currency?: string
  unit?: string
  valid_from?: string | null
  valid_until?: string | null
  days?: string | string[] | null
  source?: string
  human?: string | null
  label?: string
  is_free?: boolean
  is_within_standard?: boolean
  is_currently_valid?: boolean
}

/** Backend pricing_status summary (server attach_pricing_summary → price_window_status). */
export interface PriceWindowStatus {
  matching_windows?: PricingWindow[]
  current_windows?: PricingWindow[]
  price_min?: number | null
  current_price_min?: number | null
  is_free?: boolean
  is_cheap?: boolean
  is_currently_within_standard?: boolean
  next_matching_window?: PricingWindow | null
  label?: string
}

export interface Station {
  reference_revision?: string
  id: string
  name: string
  operator?: string
  /** Source-reported OCPI OWNER party; it is not proof of the station's CPO role. */
  owner?: {
    name?: string | null
    website?: string | null
    phone?: string | null
    logo?: string | null
    [key: string]: unknown
  } | null
  raw?: { operator?: string; [key: string]: unknown }
  city?: string
  address?: string
  lat: number
  lon: number

  price_min?: number | null
  price_max?: number | null
  current_price_min?: number | null
  price_label?: string
  is_free?: boolean
  is_cheap?: boolean
  free_condition?: string
  price_claim_type?: string

  tesla_ok?: boolean | null
  fast?: boolean
  max_power_kw?: number | null
  connector_labels?: string[]
  connectors?: Connector[]
  connector_summary?: ConnectorSummary
  connector_count?: number
  available_connectors?: number

  status?: string
  available?: number
  total_evse?: number | null

  source?: string
  source_url?: string
  source_label?: string
  sources?: SourceRef[]
  primary_source?: SourceRef
  source_integration?: Record<string, unknown>
  source_count?: number
  reliability?: string
  confidence?: string | number

  charge_claim?: Claim
  parking_claim?: Claim
  claim_conflict?: ClaimConflict
  parking?: ParkingProfile
  access?: AccessProfile
  verification?: Verification
  decision?: Decision
  pricing?: PricingWindow[]
  price_window_status?: PriceWindowStatus

  freshness_minutes?: number | null
  last_seen?: string
  source_fetched_at?: string
  recommendation_score?: number
  distance_km?: number
  drive_eta_min_est?: number
  navigation?: Record<string, string>
  insight?: string
  lead?: boolean
  corroboration?: Corroboration
  media?: MediaProfile
  /** Per-row marker for a station retained from a failed live refresh. */
  cache_stale?: boolean
}

export interface Coverage {
  mode?: 'standard' | 'degraded'
  live_route_ready?: number
  minimum_live_route_ready?: number
  lead_count?: number
  /** The bounded upstream returned a continuation Volta did not fetch. */
  partial?: boolean
  partial_reason?: 'pagination_cap' | 'upstream_error' | 'fetch_budget' | 'invalid_continuation' | string
  summary?: string
  warning?: string
}

export interface ChargersMeta {
  province?: string
  price_cap_eur_kwh?: number
  refresh_policy?: string
  important_caveat?: string
  coverage?: Coverage
  [k: string]: unknown
}

export interface CacheInfo {
  offline?: boolean
  hit?: boolean
  stale?: boolean
  age_seconds?: number
  ttl_seconds?: number
  filter_fallback?: boolean
  live_failed?: boolean
  fresh_live_authority?: boolean
  mixed_live?: boolean
}

export interface ChargersResponse {
  references?: import('./lib/references').ReferenceCatalogue | null
  stations: Station[]
  leads: Station[]
  meta?: ChargersMeta
  cache?: CacheInfo
  warning?: string
  gaia?: Record<string, unknown>
  intel?: Record<string, unknown>
}

export interface Coords {
  lat: number
  lon: number
}

/** Map viewport bounds, south/west/north/east (degrees). */
export interface Bbox {
  south: number
  west: number
  north: number
  east: number
}

export interface Viewport {
  bbox: Bbox
  zoom: number
  center: Coords
  /** True only after a direct map gesture/control; false for fitBounds/flyTo/load. */
  userInitiated?: boolean
  /** performance.now() at the gesture, before the map's delayed viewport report. */
  userInitiatedAt?: number
}

export interface GeocodeBbox {
  latitude_sw: number
  longitude_sw: number
  latitude_ne: number
  longitude_ne: number
}

export interface GeocodeResult {
  label: string
  name: string
  lat: number
  lon: number
  type?: string
  bbox?: GeocodeBbox | null
}

export type FilterKey =
  | 'available'
  | 'verified'
  | 'parking'
  | 'tesla'
  | 'fast'
  | 'leads'

export type Filters = Record<FilterKey, boolean> & {
  /** Reported charging price in €/kWh; null preserves broad discovery. */
  maxPrice: number | null
}
