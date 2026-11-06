import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { MatrixTable } from './SourceMatrix'
import { MAX_HALF_LAT, MAX_HALF_LON, areaText, clockText, searchArea, type MatrixResponse } from '../lib/sourceMatrix'

const data: MatrixResponse = {
  generated_at: '2026-10-24T16:39:16Z',
  note: 'Cells show which sources list a location.',
  truncated: false,
  columns: [
    { id: 'dgt', label: 'DGT registry', coverage: 'complete', degraded: null },
    { id: 'osm', label: 'OpenStreetMap', coverage: 'complete', degraded: 'Latest fetch failed; cached copy from 2026-10-20.' },
    { id: 'repsol', label: 'Repsol (price)', coverage: 'price', degraded: null },
  ],
  locations: [
    {
      id: 'a',
      name: 'Repsol — Av. Test',
      address: 'Av. Test 1',
      city: 'Lleida',
      listed_count: 1,
      cells: {
        dgt: { state: 'listed', entries: [{ name: 'DGT National Access Point charging registry', url: 'https://nap.dgt.es/x' }] },
        osm: { state: 'unknown', reason: 'Latest fetch failed; cached copy from 2026-10-20.' },
        repsol: { state: 'restricted', entries: [{ source: 'Repsol', price_per_kwh: 0.49 }] },
      },
    },
  ],
}

describe('location × source matrix', () => {
  it('labels every cell with its source and state for screen readers', () => {
    const markup = renderToStaticMarkup(<MatrixTable data={data} />)
    expect(markup).toContain('aria-label="DGT registry: Listed"')
    expect(markup).toContain('aria-label="OpenStreetMap: Unknown"')
    expect(markup).toContain('aria-label="Repsol (price): Price only"')
    expect(markup).toContain('Repsol — Av. Test')
  })

  it('marks a degraded column so a stale source is visible in the header', () => {
    const markup = renderToStaticMarkup(<MatrixTable data={data} />)
    expect(markup).toContain('title="Latest fetch failed; cached copy from 2026-10-20."')
  })

  it('shows a missing column fetch as unknown and a retained one as its date', () => {
    const markup = renderToStaticMarkup(
      <MatrixTable
        data={{
          ...data,
          columns: [
            { ...data.columns[0], last_fetch: { at: '2026-11-03T12:39:31Z', oldest_part_at: '2026-11-03T12:39:31Z', parts: 1, failed_parts: 0 } },
            { ...data.columns[1], last_fetch: null },
            data.columns[2],
          ],
        }}
      />,
    )
    expect(markup).toContain('fetched 2026-11-03')
    expect(markup.match(/fetched unknown/g)).toHaveLength(2)
  })
})

describe('clockText', () => {
  it('reads a missing or unreadable clock as Unknown', () => {
    for (const value of [null, undefined, '', 'yesterday', '03/11/2026']) expect(clockText(value)).toBe('Unknown')
  })

  it('keeps only what the source stated', () => {
    expect(clockText('2026-11-03T12:39:31Z')).toBe('2026-11-03 12:39 UTC')
    expect(clockText('2026-11-03')).toBe('2026-11-03')
    expect(clockText('2024-08-08T14:21:38')).toBe('2024-08-08 14:21 (time zone not stated)')
  })
})

describe('searchArea', () => {
  const box = (s: number, w: number, n: number, e: number) => ({ latitude_sw: s, longitude_sw: w, latitude_ne: n, longitude_ne: e })
  const parts = (bbox: string) => bbox.split(',').map(Number)
  // The same comparison server.py applies to /api/location-sources.
  const apiAccepts = (bbox: string) => {
    const [south, west, north, east] = parts(bbox)
    return north - south <= 0.2 && east - west <= 0.3
  }

  it('reaches a store 2.26 km from the town centre through the town extent', () => {
    const town = { lat: 28.414, lon: -16.5449, bbox: box(28.385, -16.585, 28.43, -16.51) }
    const [south, west, north, east] = parts(searchArea(town).bbox)
    const store = { lat: 28.414 - 2.26 / 111.32, lon: -16.5449 }
    expect(south).toBeLessThan(store.lat)
    expect(north).toBeGreaterThan(store.lat)
    expect(west).toBeLessThan(store.lon)
    expect(east).toBeGreaterThan(store.lon)
    expect(searchArea(town).fromPlace).toBe(true)
    expect(searchArea(town).capped).toBe(false)
  })

  it('caps a large municipality to the API limit and says so', () => {
    const area = searchArea({ lat: 42.133346, lon: -1.1365589, bbox: box(41.9904276, -1.4133747, 42.2768911, -1.01438) })
    const [south, west, north, east] = parts(area.bbox)
    expect(area.capped).toBe(true)
    expect(apiAccepts(area.bbox)).toBe(true)
    expect((north + south) / 2).toBeCloseTo(42.133346, 3)
    expect((east + west) / 2).toBeCloseTo(-1.1365589, 3)
  })

  it('keeps a street address local, whatever tiny extent it has', () => {
    const area = searchArea({ lat: 40.41626, lon: -3.70465, bbox: box(40.41602, -3.70481, 40.41649, -3.70448) })
    expect(area.fromPlace).toBe(false)
    expect(area.capped).toBe(false)
    expect(areaText(area)).toBe('about 1.4 km × 1.4 km')
  })

  it('uses the radius when the result has no extent', () => {
    expect(searchArea({ lat: 41.61, lon: 0.62, bbox: null }, 5).fromPlace).toBe(false)
    expect(areaText(searchArea({ lat: 41.61, lon: 0.62 }, 5))).toBe('about 10 km × 10 km')
  })

  it('stays accepted by the API across latitudes and the largest radius', () => {
    for (let lat = -70; lat <= 70; lat += 3.37) {
      for (let lon = -179; lon <= 179; lon += 17.13) {
        expect(apiAccepts(searchArea({ lat, lon }, 10).bbox)).toBe(true)
        const huge = box(lat - 5, lon - 5, lat + 5, lon + 5)
        expect(apiAccepts(searchArea({ lat, lon, bbox: huge }).bbox)).toBe(true)
      }
    }
    expect(MAX_HALF_LAT * 2).toBeLessThan(0.2)
    expect(MAX_HALF_LON * 2).toBeLessThan(0.3)
  })
})
