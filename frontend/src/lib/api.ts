import { keepPreviousData, queryOptions, useQuery } from '@tanstack/react-query'
import type { Bbox, ChargersResponse, Coords, Filters, GeocodeResult } from '../types'

const PUBLIC_TRANSPORT_ERROR =
  'Live data is temporarily unavailable. Check your connection and try again.'

export type TransportFailureKind = 'http' | 'network' | 'timeout' | 'invalid-response' | 'cancelled'

/** Safe client transport failure: useful diagnostics without retaining a request URL. */
export class TransportError extends Error {
  readonly kind: TransportFailureKind
  readonly status?: number

  constructor(kind: TransportFailureKind, status?: number) {
    super(PUBLIC_TRANSPORT_ERROR)
    this.name = 'TransportError'
    this.kind = kind
    this.status = status
  }
}

export function publicTransportErrorMessage(_error: unknown): string {
  return PUBLIC_TRANSPORT_ERROR
}

export async function getJSON<T>(url: string, timeoutMs = 45000, signal?: AbortSignal): Promise<T> {
  if (signal?.aborted) throw new TransportError('cancelled')
  const ctrl = new AbortController()
  let abortKind: 'cancelled' | 'timeout' | undefined
  const abort = (kind: 'cancelled' | 'timeout') => {
    if (abortKind) return
    abortKind = kind
    // Never propagate caller reasons, which can contain private request details.
    ctrl.abort()
  }
  const onCancel = () => abort('cancelled')
  signal?.addEventListener('abort', onCancel, { once: true })
  const timer = setTimeout(() => abort('timeout'), timeoutMs)
  try {
    const res = await fetch(url, { signal: ctrl.signal, headers: { Accept: 'application/json' } })
    if (abortKind) throw new TransportError(abortKind)
    if (!res.ok) throw new TransportError('http', res.status)
    try {
      const data = (await res.json()) as T
      if (abortKind) throw new TransportError(abortKind)
      return data
    } catch {
      throw new TransportError(abortKind ?? 'invalid-response', abortKind ? undefined : res.status)
    }
  } catch (error) {
    if (abortKind) throw new TransportError(abortKind)
    if (error instanceof TransportError) throw error
    throw new TransportError('network')
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onCancel)
  }
}

export interface ChargersQuery {
  filters: Filters
  coords?: Coords | null
  bbox?: Bbox | null
  overview?: boolean
  force?: boolean
  /** Compact overview keeps every rendered card/provenance field but omits ingestion-only bulk. */
  compact?: boolean
  /** Set false to keep the hook mounted but idle (e.g. the instant-leads layer in overview mode). */
  enabled?: boolean
}

const bboxParam = (b: Bbox): string => `${b.south},${b.west},${b.north},${b.east}`

export function chargersUrl({ filters, coords, bbox, overview, force, compact }: ChargersQuery): string {
  const params = new URLSearchParams()
  params.set('include_leads', 'true')
  const maxPrice = filters.maxPrice === null ? '5.0' : filters.maxPrice.toFixed(2)
  params.set('max_price', maxPrice)
  if (filters.available) params.set('available', 'true')
  if (filters.fast) params.set('fast', 'true')
  if (overview) {
    params.set('overview', 'true')
    if (bbox) params.set('bbox', bboxParam(bbox))
  } else {
    if (bbox) params.set('bbox', bboxParam(bbox))
    if (coords) {
      params.set('lat', String(coords.lat))
      params.set('lon', String(coords.lon))
    }
  }
  if (force) params.set('force', 'true')
  if (compact) params.set('compact', 'true')
  return `/api/chargers?${params.toString()}`
}

export function chargersQueryKey({ filters, coords, bbox, overview, compact }: ChargersQuery) {
  return [
    'chargers',
    overview ? 'overview' : 'live',
    filters.maxPrice,
    filters.available,
    filters.fast,
    overview ? null : (coords?.lat ?? null),
    overview ? null : (coords?.lon ?? null),
    // The backend fetch cache is safely snapped, but each response is re-filtered to the
    // exact requested bbox. The client key must therefore keep the exact bbox too; a coarse
    // key could reuse a neighboring response without giving the server a chance to re-filter.
    bbox ? bboxParam(bbox) : null,
    Boolean(compact),
  ] as const
}

export function chargersQueryOptions(query: ChargersQuery) {
  const { overview, enabled } = query
  return queryOptions({
    queryKey: chargersQueryKey(query),
    queryFn: ({ signal }) => getJSON<ChargersResponse>(chargersUrl(query), 45000, signal),
    enabled: enabled !== false,
    placeholderData: keepPreviousData,
    staleTime: 30000,
    // Live status drifts; overview leads are static, so don't poll them.
    refetchInterval: overview ? false : 60000,
    refetchIntervalInBackground: false,
  })
}

export function useChargers(query: ChargersQuery) {
  return useQuery(chargersQueryOptions(query))
}

export interface GeocodeResponse {
  query?: string
  results: GeocodeResult[]
}

export function geocodeQueryOptions(query: string) {
  const q = query.trim()
  return queryOptions({
    queryKey: ['geocode', q.toLowerCase()],
    queryFn: ({ signal }) => getJSON<GeocodeResponse>(`/api/geocode?q=${encodeURIComponent(q)}`, 12000, signal),
    enabled: q.length >= 3,
    staleTime: 10 * 60 * 1000,
  })
}

export function useGeocode(query: string) {
  return useQuery(geocodeQueryOptions(query))
}

// NOTE: the old useIntel() hook was removed — it was never called. Intelligence-first render
// is delivered by the instant overview=true chargers query in App.tsx (bbox-scoped leads paint
// while the live fetch runs). The /api/intel endpoint itself remains for debugging/QA.
