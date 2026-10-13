import type { Bbox, Coords } from '../types'

/** Keep canonical coordinates unchanged; wrapped map copies can exceed ±180°. */
export function wrapLongitude(lon: number): number {
  if (lon >= -180 && lon <= 180) return lon
  return ((lon + 180) % 360 + 360) % 360 - 180
}

export function validCoordinates(c: Coords): boolean {
  return Number.isFinite(c.lat) && Number.isFinite(c.lon)
    && c.lat >= -90 && c.lat <= 90 && c.lon >= -180 && c.lon <= 180
}

export function longitudeSpan(b: Bbox): number {
  const span = b.east - b.west
  return span < 0 ? span + 360 : span
}

/** west > east represents a small view crossing the international date line. */
export function normalizeBbox(b: Bbox): Bbox {
  const fullWorld = Math.abs(b.east - b.west) >= 360
  return {
    south: Math.max(-90, Math.min(90, b.south)),
    north: Math.max(-90, Math.min(90, b.north)),
    west: fullWorld ? -180 : wrapLongitude(b.west),
    east: fullWorld ? 180 : wrapLongitude(b.east),
  }
}

export function containsCoordinate(b: Bbox, c: Coords): boolean {
  if (!validCoordinates(c) || c.lat < b.south || c.lat > b.north) return false
  // +180 and -180 name the same meridian.
  const lons = Math.abs(c.lon) === 180 ? [-180, 180] : [c.lon]
  return lons.some(lon => b.west > b.east
    ? lon >= b.west || lon <= b.east
    : lon >= b.west && lon <= b.east)
}

/** Match the backend's default live-source budget; wider views use stored evidence. */
export function isLiveBbox(b: Bbox): boolean {
  const midLat = (b.south + b.north) / 2
  const maxLon = 1.2 / Math.max(0.35, Math.cos(midLat * Math.PI / 180))
  return b.north - b.south <= 1.2 && longitudeSpan(b) <= maxLon
}

/** MapLibre needs the eastern edge unwrapped to fit across the date line. */
export function mapBounds(b: Bbox): [[number, number], [number, number]] {
  return [[b.west, b.south], [b.west + longitudeSpan(b), b.north]]
}

export function boxAround(c: Coords, d = 0.16): Bbox {
  return normalizeBbox({ south: c.lat - d, north: c.lat + d, west: c.lon - d * 1.35, east: c.lon + d * 1.35 })
}

export function widerBoxAround(c: Coords, current: Bbox | null): Bbox {
  const halfLat = Math.min(1.2, Math.max(0.32, current ? ((current.north - current.south) / 2) * 1.7 : 0.32))
  const halfLon = Math.min(1.6, Math.max(0.43, current ? (longitudeSpan(current) / 2) * 1.7 : 0.43))
  return normalizeBbox({ south: c.lat - halfLat, north: c.lat + halfLat, west: c.lon - halfLon, east: c.lon + halfLon })
}
