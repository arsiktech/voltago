import { describe, expect, it } from 'vitest'
import { boxAround, containsCoordinate, isLiveBbox, longitudeSpan, mapBounds, normalizeBbox, validCoordinates, widerBoxAround, wrapLongitude } from './geo'
import { inViewport, isMapLocationVerified, hasVerifiedPrice, isRouteReady, responseHasNoLiveAuthority, statusTrustSummary } from './format'
import { chargersUrl } from './api'
import { DEFAULT_FILTERS } from './format'

describe('worldwide location and bounds', () => {
  it.each([
    { lat: 35.68, lon: 139.69 }, // Tokyo
    { lat: -33.87, lon: 151.21 }, // Sydney
    { lat: 37.77, lon: -122.42 }, // San Francisco
    { lat: -1.29, lon: 36.82 }, // Nairobi
    { lat: 90, lon: 180 },
    { lat: -90, lon: -180 },
  ])('accepts valid device coordinates %j', coords => {
    expect(validCoordinates(coords)).toBe(true)
    expect(containsCoordinate(boxAround(coords), coords)).toBe(true)
  })

  it.each([
    { lat: NaN, lon: 0 }, { lat: 0, lon: Infinity },
    { lat: 90.01, lon: 0 }, { lat: 0, lon: -180.01 },
  ])('rejects invalid device coordinates %j', coords => {
    expect(validCoordinates(coords)).toBe(false)
  })

  it('normalizes wrapped map copies without rounding ordinary coordinates', () => {
    expect(wrapLongitude(541)).toBe(-179)
    expect(wrapLongitude(-541)).toBe(179)
    expect(wrapLongitude(-0.3763)).toBe(-0.3763)
    const bbox = normalizeBbox({ south: -17, west: 539, north: -16, east: 541 })
    expect(bbox).toEqual({ south: -17, west: 179, north: -16, east: -179 })
    expect(longitudeSpan(bbox)).toBe(2)
    expect(mapBounds(bbox)).toEqual([[179, -17], [181, -16]])
    expect(containsCoordinate(bbox, { lat: -16.5, lon: 179.5 })).toBe(true)
    expect(containsCoordinate(bbox, { lat: -16.5, lon: -179.5 })).toBe(true)
    expect(containsCoordinate(bbox, { lat: -16.5, lon: 0 })).toBe(false)
    expect(inViewport({ id: 'fiji', name: 'Directory', lat: -16.5, lon: -179.5 }, bbox)).toBe(true)
    const url = new URL(chargersUrl({ filters: DEFAULT_FILTERS, bbox, overview: true }), 'https://example.com')
    expect(url.searchParams.get('bbox')).toBe('-17,179,-16,-179')
  })

  it('preserves a full-world viewport and clamps latitude at the poles', () => {
    const world = normalizeBbox({ south: -95, west: -190, north: 95, east: 190 })
    expect(world).toEqual({ south: -90, west: -180, north: 90, east: 180 })
    expect(containsCoordinate(world, { lat: 90, lon: 180 })).toBe(true)
    expect(isLiveBbox(world)).toBe(false)
    expect(boxAround({ lat: 89.95, lon: 179.95 }).north).toBe(90)
  })

  it('widens a date-line search locally without turning it into a global request', () => {
    const center = { lat: -16.5, lon: 179.95 }
    const initial = boxAround(center)
    const wider = widerBoxAround(center, initial)
    expect(longitudeSpan(wider)).toBeGreaterThan(longitudeSpan(initial))
    expect(longitudeSpan(wider)).toBeLessThan(1)
    expect(containsCoordinate(wider, center)).toBe(true)
    expect(isLiveBbox(wider)).toBe(true)
  })

  it('allows local live queries at high latitude and uses overview for country searches', () => {
    expect(isLiveBbox({ south: 79.5, north: 80.5, west: 10, east: 13 })).toBe(true)
    expect(isLiveBbox({ south: -0.5, north: 0.5, west: 10, east: 13 })).toBe(false)
    expect(isLiveBbox({ south: 30, north: 46, west: 129, east: 146 })).toBe(false)
  })

  it('labels successful directory-only regional responses as not live', () => {
    const response = { stations: [], leads: [], cache: { live_supported: false, live_failed: false } }
    expect(responseHasNoLiveAuthority(response)).toBe(true)
    const summary = statusTrustSummary({ overview: false, sourceBackedOnly: responseHasNoLiveAuthority(response),
      isFetching: false, stale: false, updated: 'now', liveReady: 0, leadCount: 2 })
    expect(summary.label).toBe('Saved chargers · not live')
    expect(responseHasNoLiveAuthority({ stations: [], leads: [], meta: { coverage: { mode: 'directory' } } })).toBe(true)
  })

  it('plots a verified OCM station location without granting a price or live authority', () => {
    const station = { id: 'ocm:123', name: 'OCM location', lat: 35.68, lon: 139.69, lead: true,
      reliability: 'directory-community-snapshot', source_url: 'https://openchargemap.org/site/poi/details/123',
      verification: { location_verified: true, price_verified: false, live_verified: false } }
    expect(isMapLocationVerified(station)).toBe(true)
    expect(hasVerifiedPrice(station)).toBe(false)
    expect(isRouteReady(station)).toBe(false)
    expect(isMapLocationVerified({ ...station, source_url: 'https://openchargemap.org/' })).toBe(false)
    expect(isMapLocationVerified({ ...station, source_url: 'https://other.example/openchargemap.org/site/poi/details/123' })).toBe(false)
    expect(isMapLocationVerified({ ...station, verification: { location_verified: false } })).toBe(false)
  })
})
