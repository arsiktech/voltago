import type { GeocodeResult } from '../types'

export type CellState = 'listed' | 'not_listed' | 'unknown' | 'uncertain' | 'restricted'

export interface MatrixCell {
  state: CellState
  entries?: Record<string, string | number | boolean>[]
  reason?: string
  candidate?: { id: string; name: string; distance_m: number }
}

export interface MatrixColumn {
  id: string
  label: string
  coverage: 'complete' | 'network' | 'price'
  degraded: string | null
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

// About 1.4 km around the chosen place: small enough to read, inside the API's limit.
export function bboxAround(place: Pick<GeocodeResult, 'lat' | 'lon'>, halfKm = 0.7): string {
  const dLat = halfKm / 111.32
  const dLon = halfKm / (111.32 * Math.cos((place.lat * Math.PI) / 180))
  return [place.lat - dLat, place.lon - dLon, place.lat + dLat, place.lon + dLon].map((v) => v.toFixed(5)).join(',')
}
