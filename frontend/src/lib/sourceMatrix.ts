import type { GeocodeResult } from '../types'

export type CellState = 'listed' | 'not_listed' | 'unknown' | 'uncertain' | 'restricted'

// A null clock means this source's listing does not retain that time.
export interface ListingClocks {
  fetched_at: string | null
  published_at: string | null
  source_last_updated: string | null
}

export type MatrixEntry = Record<string, string | number | boolean | ListingClocks> & { clocks?: ListingClocks }

export interface MatrixCell {
  state: CellState
  entries?: MatrixEntry[]
  reason?: string
  candidate?: { id: string; name: string; distance_m: number }
}

export interface ColumnFetch {
  at: string
  oldest_part_at: string
  parts: number
  failed_parts: number
}

export interface MatrixColumn {
  id: string
  label: string
  coverage: 'complete' | 'network' | 'price'
  degraded: string | null
  last_fetch?: ColumnFetch | null
}

export interface MatrixLocation {
  id: string
  name: string
  address?: string
  city?: string
  listed_count: number
  cells: Record<string, MatrixCell>
}

export interface MatrixResponse {
  generated_at: string | null
  columns: MatrixColumn[]
  locations: MatrixLocation[]
  truncated: boolean
  note: string
}

export const STATE_LABEL: Record<CellState, string> = {
  listed: 'Listed',
  not_listed: 'Not listed',
  unknown: 'Unknown',
  uncertain: 'Uncertain match',
  restricted: 'Price only',
}

// Reads the API's canonical clock text as is: parsing it here could invent a time zone.
export function clockText(value: string | null | undefined): string {
  const match = /^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}):\d{2}(Z?))?$/.exec(value ?? '')
  if (!match) return 'Unknown'
  const [, date, time, utc] = match
  if (!time) return date
  return `${date} ${time} ${utc ? 'UTC' : '(time zone not stated)'}`
}

export const DEFAULT_RADIUS_KM = 0.7
export const RADIUS_OPTIONS_KM = [0.7, 2, 5, 10]

// /api/location-sources refuses more than 0.2 degrees of latitude or 0.3 of longitude.
// These half-widths stay just under that after the bbox is rounded to five decimals.
export const MAX_HALF_LAT = 0.099
export const MAX_HALF_LON = 0.149

const KM_PER_DEGREE = 111.32

export interface SearchArea {
  bbox: string
  widthKm: number
  heightKm: number
  // True when the geocoder gave this place a real extent; false when the radius sets the area.
  fromPlace: boolean
  capped: boolean
}

// Centred on the place, because the API lists the locations nearest the centre of the box.
export function searchArea(place: Pick<GeocodeResult, 'lat' | 'lon' | 'bbox'>, radiusKm = DEFAULT_RADIUS_KM): SearchArea {
  const { lat, lon, bbox } = place
  const cosLat = Math.max(Math.cos((lat * Math.PI) / 180), 0.01)
  const kmToLat = (km: number) => km / KM_PER_DEGREE
  const kmToLon = (km: number) => km / (KM_PER_DEGREE * cosLat)
  const placeLat = bbox ? Math.max(bbox.latitude_ne - lat, lat - bbox.latitude_sw) : 0
  const placeLon = bbox ? Math.max(bbox.longitude_ne - lon, lon - bbox.longitude_sw) : 0
  // A house or a short street has a tiny extent: it keeps the local default, and the radius can widen it.
  const fromPlace =
    (Number.isFinite(placeLat) && placeLat > kmToLat(DEFAULT_RADIUS_KM)) ||
    (Number.isFinite(placeLon) && placeLon > kmToLon(DEFAULT_RADIUS_KM))
  const wantLat = fromPlace ? Math.max(placeLat, kmToLat(DEFAULT_RADIUS_KM)) : kmToLat(radiusKm)
  const wantLon = fromPlace ? Math.max(placeLon, kmToLon(DEFAULT_RADIUS_KM)) : kmToLon(radiusKm)
  const halfLat = Math.min(wantLat, MAX_HALF_LAT)
  const halfLon = Math.min(wantLon, MAX_HALF_LON)
  const south = Math.max(lat - halfLat, -90)
  const north = Math.min(lat + halfLat, 90)
  const west = Math.max(lon - halfLon, -180)
  const east = Math.min(lon + halfLon, 180)
  return {
    bbox: [south, west, north, east].map((value) => value.toFixed(5)).join(','),
    widthKm: (east - west) * KM_PER_DEGREE * cosLat,
    heightKm: (north - south) * KM_PER_DEGREE,
    fromPlace,
    capped: wantLat > MAX_HALF_LAT || wantLon > MAX_HALF_LON,
  }
}

export function areaText(area: Pick<SearchArea, 'widthKm' | 'heightKm'>): string {
  const km = (value: number) => {
    const tenths = Math.round(value * 10) / 10
    return tenths < 10 ? tenths.toFixed(1) : Math.round(tenths).toString()
  }
  return `about ${km(area.widthKm)} km × ${km(area.heightKm)} km`
}
