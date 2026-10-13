import { expect, it } from 'vitest'
import app from '../App.tsx?raw'
import mapView from '../components/MapView.tsx?raw'

// Structural regression for the App scheduler; not a browser timing proof.
it('does not gate the latest viewport debounce on an older network request', () => {
  const scheduler = app.slice(app.indexOf('  const areaSearchDelay'), app.indexOf('  const placeLabel'))
  expect(scheduler).toContain('window.setTimeout')
  expect(scheduler).toContain('window.clearTimeout')
  expect(scheduler).not.toContain('query.isFetching')
})

it('keeps a selected map result in the accessible panel and hides unverified map prices', () => {
  expect(app).toContain('const panelRows')
  expect(app).toContain('selectedPanelRows(listRows, selectedRow)')
  expect(app).not.toContain('aria-hidden={Boolean(selectedId)}')
  expect(mapView).toContain('if (!hasVerifiedPrice(st)) return null')
  expect(mapView).toContain("Cached evidence — not live. Refresh or check source before driving.")
})
import { discoveryRows, selectedPanelRows } from './discovery'
import type { Station } from '../types'
it('keeps a resolved selected card first without duplicates or changing the input', () => {
  const rows: Station[] = Array.from({ length: 200 }, (_, i) => ({ id: `lead-${i}`, name: `Charger ${i}`, lat: 40, lon: -3, lead: true, price_min: null }))
  const selected = { ...rows[154], name: 'Current resolved name' }
  const panel = selectedPanelRows(rows, selected)
  expect(panel[0]).toBe(selected)
  expect(panel).toHaveLength(rows.length)
  expect(new Set(panel.map(row => row.id)).size).toBe(rows.length)
  expect(panel.slice(1)).toEqual(rows.filter(row => row.id !== selected.id))
  expect(rows[154].name).toBe('Charger 154')
  expect(selectedPanelRows(rows, null)).toBe(rows)
})
it('adds an admitted selected card outside the current list scope once', () => {
  const selected: Station = { id: 'outside', name: 'Current selected', lat: 40, lon: -3, lead: true, price_min: null }
  expect(selectedPanelRows([], selected)).toEqual([selected])
  expect(selectedPanelRows([selected], selected)).toEqual([selected])
})

it('retains static out-of-city inventory, but live rows win stable IDs', () => {
  const lead: Station = { id: 'dgt:1', name: 'Registry', lat: 40, lon: -3, lead: true, price_min: null }
  const live = { ...lead, lead: false, name: 'Current' }
  expect(discoveryRows([lead], [])).toEqual([lead])
  expect(discoveryRows([lead], [live])).toEqual([live])
  expect(discoveryRows([{ ...lead, lead: false }], [])).toEqual([])
})
