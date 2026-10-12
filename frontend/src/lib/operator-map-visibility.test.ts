import { describe, expect, it } from 'vitest'
import type { Station } from '../types'
import { hasVerifiedPrice, isMapLocationVerified, isRouteReady, remoteStartAction, sourceTier } from './format'

const operatorMaps = [
  ['Etecnic EVcharge public charger map', 'https://etecnic.es/mapa-de-recarga/', 41.064699, 1.0576201],
  ['Atlante public charger map', 'https://map.atlante.energy/', 43.316551, -3.073291],
  ['Zunder public charger map', 'https://www.zunder.com/mapa-de-ubicaciones/', 42.71779934, -4.29173329],
  ['Repsol public charging point finder', 'https://www.repsol.es/buscador-eess-y-puntos-de-recarga/', 40.424397, -3.611191],
] as const

const station = (name: string, url: string, lat: number, lon: number): Station => ({
  id: name,
  name,
  lat,
  lon,
  source: name,
  source_url: url,
  lead: true,
  reliability: 'directory-page',
  price_min: null,
  verification: {
    level: 'directory',
    location_verified: true,
    live_verified: false,
    price_verified: false,
    route_checked: false,
  },
  decision: { level: 'lead', label: 'Price lead', reason: 'Check the operator tariff.' },
  primary_source: {
    name,
    url,
    kind: 'official_data',
    quality: 'official',
  },
})

describe('operator feed coordinate map visibility', () => {
  it.each(operatorMaps)('%s uses existing official_data without live, price or start authority', (name, url, lat, lon) => {
    const row = station(name, url, lat, lon)
    expect(sourceTier(row)).toBe('directory')
    expect(isMapLocationVerified(row)).toBe(true)
    expect(hasVerifiedPrice(row)).toBe(false)
    expect(isRouteReady(row)).toBe(false)
    expect(remoteStartAction(row)).toBeNull()
    expect(isMapLocationVerified({ ...row, primary_source: { name, url, kind: 'generic', quality: 'generic' } })).toBe(false)
    expect(isMapLocationVerified({ ...row, verification: { ...row.verification, location_verified: false } })).toBe(false)
    expect(isMapLocationVerified({ ...row, lat: Number.NaN })).toBe(false)
    expect(isMapLocationVerified({ ...row, lon: Number.POSITIVE_INFINITY })).toBe(false)
    expect(isRouteReady({ ...row, available: 4, status: 'AVAILABLE', decision: { level: 'go', label: 'Go now', reason: 'Untrusted fixture decision.' } })).toBe(false)
  })
})
