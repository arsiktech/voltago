import { expect, it } from 'vitest'
import app from '../App.tsx?raw'
import mapView from '../components/MapView.tsx?raw'
import presentation from './stationPresentation.ts?raw'

// Structural regression for the App scheduler; not a browser timing proof.
it('does not gate the latest viewport debounce on an older network request', () => {
  const scheduler = app.slice(app.indexOf('  const areaSearchDelay'), app.indexOf('  const placeLabel'))
  expect(scheduler).toContain('window.setTimeout')
  expect(scheduler).toContain('window.clearTimeout')
  expect(scheduler).not.toContain('query.isFetching')
})

it('keeps a selected map result in the accessible panel and hides unverified map prices', () => {
  expect(app).toContain('const panelRows')
  expect(app).toContain('[selectedRow, ...listRows]')
  expect(app).not.toContain('aria-hidden={Boolean(selectedId)}')
  expect(presentation).toContain('if (!hasVerifiedPrice(st)) return null')
  expect(mapView).toContain('const preview = mapPinPreview(s)')
  expect(mapView).toContain("Cached, not live. Check source before driving.")
})
import { discoveryRows } from './discovery'
import type { Station } from '../types'
it('retains static out-of-city inventory, but live rows win stable IDs', () => {
  const lead: Station = { id: 'dgt:1', name: 'Registry', lat: 40, lon: -3, lead: true, price_min: null }
  const live = { ...lead, lead: false, name: 'Current' }
  expect(discoveryRows([lead], [])).toEqual([lead])
  expect(discoveryRows([lead], [live])).toEqual([live])
  expect(discoveryRows([{ ...lead, lead: false }], [])).toEqual([])
})
