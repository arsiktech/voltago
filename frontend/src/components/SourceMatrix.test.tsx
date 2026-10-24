import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { MatrixTable } from './SourceMatrix'
import { bboxAround, type MatrixResponse } from '../lib/sourceMatrix'

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

  it('keeps the search area inside the API limit', () => {
    const [south, west, north, east] = bboxAround({ lat: 41.61, lon: 0.62 }).split(',').map(Number)
    expect(north - south).toBeLessThan(0.2)
    expect(east - west).toBeLessThan(0.3)
    expect((north + south) / 2).toBeCloseTo(41.61, 4)
  })
})
