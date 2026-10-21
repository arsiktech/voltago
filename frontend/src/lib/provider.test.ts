import { describe, it, expect } from 'vitest'
import { stationTitle, providerIdentity } from './provider'
import type { Station } from '../types'
const st = (extra: Partial<Station>): Station => ({ id: 'source:42', name: 'City car park', lat: 39, lon: -0.4, ...extra })
describe('source-backed provider identity', () => {
  it('puts real operator first without changing the source row', () => {
    const row = st({ operator: 'Iberdrola' })
    expect(stationTitle(row)).toBe('Iberdrola · City car park')
    expect(providerIdentity(row)).toEqual({ name: 'Iberdrola', field: 'operator', raw: 'Iberdrola' })
    expect(row.id).toBe('source:42')
    expect(row.name).toBe('City car park')
  })
  it('does not infer Tesla operator or Supercharger from compatibility or power', () => {
    expect(stationTitle(st({ tesla_ok: true, max_power_kw: 250 }))).toBe('City car park')
    expect(stationTitle(st({ operator: 'Tesla', name: 'Destination charger' }))).toBe('Tesla · Destination charger')
    expect(stationTitle(st({ operator: 'Tesla', name: 'Supercharger Valencia' }))).toBe('Tesla Supercharger · Supercharger Valencia')
  })
  it('uses source-reported OWNER name only as a provenance-preserving fallback', () => {
    const observed = JSON.parse(JSON.stringify({
      id: '6dc159fd-3bcc-41c5-8c0a-6fb959bf858b',
      name: '22XC11M2M03DB00866',
      lat: 28.2916,
      lon: -16.6291,
      owner: { name: 'ENDESA X WAY, S.L.', website: 'www.endesaxway.com', phone: '800760255', logo: null },
      source: 'MapaREVE public live OCPI',
      operator: null,
      operator_name: null,
      network: null,
    })) as Station

    expect(providerIdentity(observed)).toEqual({
      name: 'ENDESA X WAY, S.L.',
      field: 'owner.name',
      raw: 'ENDESA X WAY, S.L.',
    })
    expect(stationTitle(observed)).toBe('ENDESA X WAY, S.L. · 22XC11M2M03DB00866')
    expect(JSON.parse(JSON.stringify(observed)).owner).toEqual(observed.owner)
  })
  it.each(['DE*ABC*E12345', 'AB-001/CD02', 'aB12Cd34', '000123'])('preserves identifier-like station name %s exactly', (name) => {
    const row = st({ name, operator: 'Iberdrola' })
    expect(stationTitle(row)).toBe(`Iberdrola · ${name}`)
    expect(stationTitle(st({ name }))).toBe(name)
    expect(row.name).toBe(name)
  })
  it.each([
    ['  PLAZA   MAYOR  ', 'Plaza Mayor'],
    ['PARKING 24 NORTE', 'Parking 24 Norte'],
    ['VALENCIA', 'Valencia'],
    ['City car park', 'City car park'],
    ['', 'Charging point'],
  ])('keeps existing human-readable formatting for %s', (name, expected) => {
    expect(stationTitle(st({ name, operator: 'Iberdrola' }))).toBe(`Iberdrola · ${expected}`)
  })
  it('fails closed on explicit operator conflict before considering OWNER fallback', () => {
    expect(providerIdentity(st({
      operator: 'Operator A',
      raw: { operator: 'Operator B' },
      owner: { name: 'Owner C' },
    }))).toBeNull()
  })
})
