import { describe, expect, it } from 'vitest'
import type { Station } from '../types'
import { sourceName, stationSource, stationSourceAction, stationSourceReference } from './stationSource'

const row = (overrides: Partial<Station> = {}): Station => ({ id: 'charger', name: 'Town charger', lat: 28.4, lon: -16.5, ...overrides })
const detail = 'https://canariasterecarga.com/es/p61008/'

describe('named exact station source actions', () => {
  it('uses the exact API action and its source name instead of the operator homepage', () => {
    const station = row({ operator: 'Endesa', primary_source: { name: 'Endesa', url: 'https://www.endesaxway.com/' },
      station_source: { name: 'Directory page', url: detail, kind: 'station_page' } })
    const before = JSON.stringify(station)
    expect(stationSourceAction(station)).toMatchObject({ label: 'Open Canarias te recarga', href: detail })
    expect(stationSourceAction(station)?.ariaLabel).toContain('Town charger')
    expect(JSON.stringify(station)).toBe(before)
  })

  it('finds an existing detail URL behind a generic saved map reference', () => {
    const station = row({ source: 'Canarias te recarga public map', source_url: 'https://canariasterecarga.com/es/mapa/',
      primary_source: { name: 'Directory page', kind: 'live_map', url: 'https://canariasterecarga.com/es/mapa/' },
      source_details: { source_url: detail, source_name: 'Canarias te recarga' } })
    expect(stationSourceAction(station)).toMatchObject({ label: 'Open Canarias te recarga', href: detail })
    expect(stationSourceReference(station, station.source_url)).toMatchObject({ url: detail, exact: true })
  })

  it('honors an explicit missing API action and does not manufacture a provider URL', () => {
    expect(stationSourceAction(row({ station_source: null, operator: 'Endesa', source_url: detail }))).toBeNull()
    expect(stationSourceAction(row({ operator: 'Iberdrola', name: 'Lidl — Test store' }))).toBeNull()
  })

  it.each([
    ['https://canariasterecarga.com/es/mapa/p61008/', 'Canarias te recarga'],
    ['https://canariasterecarga.com/es/punto-de-recarga-hotel-villa-del-conde/', 'Canarias te recarga'],
    ['https://www.openstreetmap.org/node/123', 'OpenStreetMap'],
    ['https://map.openchargemap.io/?mode=details&id=123', 'OpenChargeMap'],
    ['https://map.electromaps.com/en/p/123', 'Electromaps'],
    ['https://chargemap.com/example-car-park.html', 'Chargemap'],
    ['https://chargemap.com/en-gb/charging-stations/123', 'Chargemap'],
    ['https://www.tesla.com/es_es/findus/location/supercharger/example', 'Tesla'],
    ['https://www.telpark.com/es/ciudades/madrid/example/', 'Telpark'],
    ['https://www.interparking.es/es/aparcamientos/madrid/example/', 'Interparking'],
  ])('retains the exact charger destination %s', (url, name) => {
    expect(stationSourceAction(row({ source_url: url }))).toMatchObject({ href: url, label: `Open ${name}` })
  })

  it.each([
    ['https://partner-next.gaiacharge.com/cpo/jump/site/connector', 'Gaia'],
    ['https://recarga.fenieenergia.es/punto-recarga/C123/', 'Feníe Energía'],
  ])('names a genuine start/check destination %s', (url, name) => {
    expect(stationSourceAction(row({ source_url: url }))).toMatchObject({ href: url, label: `Start/check ${name}` })
  })

  it('preserves an OpenChargeMap station ID through the public map handoff', () => {
    expect(stationSourceAction(row({ source_url: 'https://openchargemap.org/site/poi/details/123' }))?.href)
      .toBe('https://map.openchargemap.io/?mode=details&id=123')
  })

  it.each([
    'https://canariasterecarga.com/', 'https://canariasterecarga.com/es/mapa/',
    'https://canariasterecarga.com/es/puntos-de-recarga/', 'https://map.electromaps.com/',
    'https://canariasterecarga.com/es/mapa/punto-de-recarga-legacy/',
    'https://chargemap.com/', 'https://chargemap.com/example.html/extra',
    'https://chargemap.com/index.html', 'https://chargemap.com/contact.html',
    'https://www.tesla.com/es_es/findus', 'https://map.openchargemap.io/',
    'https://map.openchargemap.io/?mode=details&id=123&id=456',
    'https://map.openchargemap.io/?mode=details&id=123&poi=456',
    'https://other.example/locations', 'https://other.example/es/', 'https://other.example/points.json',
    'https://canariasterecarga.com.evil.example/es/p61008/',
    'https://evil.example/https://openchargemap.org/site/poi/details/123',
    'https://user:password@canariasterecarga.com/es/p61008/',
    'http://localhost/charger/123', 'javascript:alert(1)',
  ])('rejects a generic or unsupported station action %s', (url) => {
    const kind = url.includes('evil.example') ? undefined : 'station_page'
    expect(stationSource(row({ primary_source: { name: 'Directory page', kind, url } }))).toBeNull()
  })

  it('keeps a published tariff policy as a named broader reference', () => {
    const url = 'https://canariasterecarga.com/documents/charging-policy.pdf'
    expect(stationSourceReference(row({ source_details: { source_url: detail } }), url))
      .toEqual({ name: 'Canarias te recarga', url, exact: false })
  })

  it('names the linked evidence provider independently of its operator', () => {
    expect(sourceName(detail, 'Directory page')).toBe('Canarias te recarga')
    expect(sourceName('https://example.org/charger/123', 'City directory')).toBe('City directory')
    expect(sourceName('https://example.org/charger/123', 'Directory page')).toBe('example.org')
  })
})
