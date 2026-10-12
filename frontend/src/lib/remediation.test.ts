import { describe, it, expect } from 'vitest'
import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { chargersQueryKey } from './api'
import { DEFAULT_FILTERS } from './format'
import { providerIdentity, stationTitle } from './provider'
import * as discovery from './discovery'
import * as provider from './provider'
import type { Station } from '../types'
const row = (extra: Partial<Station> = {}): Station => ({ id: 'src:42', name: 'Car park', lat: 39, lon: -1, ...extra })
describe('ARS-21 corrective behavior', () => {
  it('keeps mounted inventory/default and free observers independent and restores paid rows', () => {
    const client = new QueryClient()
    const normal = chargersQueryKey({ filters: DEFAULT_FILTERS, overview: true, compact: true })
    const free = chargersQueryKey({ filters: { ...DEFAULT_FILTERS, maxPrice: 0 }, overview: true, compact: true })
    client.setQueryData(normal, [row({ price_min: 1 })])
    const inventory = new QueryObserver(client, { queryKey: normal, enabled: false })
    const filtered = new QueryObserver(client, { queryKey: free, enabled: false })
    const off = [inventory.subscribe(() => {}), filtered.subscribe(() => {})]
    client.setQueryData(free, [])
    expect(inventory.getCurrentResult().data).toEqual([row({ price_min: 1 })])
    expect(filtered.getCurrentResult().data).toEqual([])
    filtered.setOptions({ queryKey: normal, enabled: false })
    expect(filtered.getCurrentResult().data).toEqual([row({ price_min: 1 })])
    off.forEach(fn => fn()); client.clear()
  })
  it('rejects contradictory providers and Tesla classes without mutating sources', () => {
    const conflict = row({ operator: 'Tesla Supercharger', raw: { operator: 'Iberdrola' } })
    const before = JSON.stringify(conflict)
    expect(providerIdentity(conflict)).toBeNull()
    expect(providerIdentity(row({ operator: 'Tesla Supercharger', name: 'Destination charger' }))).toBeNull()
    expect(providerIdentity(row({ operator: 'Tesla Destination Charging', name: 'Supercharger Valencia' }))).toBeNull()
    expect(providerIdentity(row({ operator: 42 as unknown as string, raw: { operator: ' bp pulse ' } }))?.field).toBe('raw.operator')
    expect(providerIdentity(row({ operator: ' ' }))).toBeNull()
    expect(JSON.stringify(conflict)).toBe(before)
    expect(stationTitle(conflict)).toBe('Car park')
  })
  it('reconciles missing selected rows and static to live replacement by exact ID', () => {
    expect(discovery.selectedMapRow('src:42', [])).toBeNull()
    const staticRow = row({ lead: true })
    expect(discovery.selectedMapRow('src:42', [staticRow])).toBe(staticRow)
    const live = row({ lead: false, price_min: 0.2 })
    expect(discovery.selectedMapRow('src:42', discovery.discoveryRows([staticRow], [live]))).toBe(live)
  })
  it('bounds measured long legal and unbroken preview text, retaining full detail', () => {
    for (const name of ['Sociedad de Infraestructuras de Recarga y Servicios '.repeat(10), 'W'.repeat(1000)]) {
      const label = provider.boundedLabel(name, text => text.length * 10, 162)
      expect(label.length * 10).toBeLessThanOrEqual(162)
      expect(label.endsWith('…')).toBe(true)
      expect(stationTitle(row({ operator: name }))).toContain(name.trim())
    }
  })
})
