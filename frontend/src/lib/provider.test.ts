import { describe, it, expect } from 'vitest'
import { stationTitle, providerIdentity, providerAppUrl, providerHandoff } from './provider'
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

describe('operator client handoff', () => {
  it('maps a source-reported operator to its own public app page', () => {
    const row = st({ name: '22XC11M2M03DB00866', owner: { name: 'ENDESA X WAY, S.L.' } })
    expect(providerAppUrl(row)).toEqual({
      provider: 'Endesa X Way',
      url: 'https://www.endesaxway.com/',
      app: true,
    })
    const handoff = providerHandoff(row)!
    expect(handoff.label).toBe('Open provider app')
    expect(handoff.href).toBe('https://www.endesaxway.com/')
    expect(handoff.ariaLabel).toContain('ENDESA X WAY, S.L. · 22XC11M2M03DB00866')
    // Attribution survives: the source row itself is untouched.
    expect((row.owner as { name: string }).name).toBe('ENDESA X WAY, S.L.')
  })

  it('matches legal-entity spellings and accents without inventing an operator', () => {
    expect(providerAppUrl(st({ operator: 'WENEA EMOBILITY SERVICES S.L.' }))?.url).toBe('https://wenea.com/')
    expect(providerAppUrl(st({ operator: 'Feníe Energía, SA' }))?.url).toBe('https://recarga.fenieenergia.es/')
    expect(providerAppUrl(st({ operator: 'Iberdrola Clientes S.A.U' }))?.url).toBe('https://iberdrola-bppulse.es/')
  })

  it('hands a retailer-hosted row to the retailer page, labelled as a site', () => {
    const lidl = st({
      name: 'Lidl — Puerto de la Cruz - Ctra Icod-Sta Cruz s/n',
      source: 'Lidl official free list',
      reliability: 'official-free-policy',
    })
    expect(providerIdentity(lidl)).toBeNull()
    const handoff = providerHandoff(lidl)!
    expect(handoff.provider).toBe('Lidl')
    expect(handoff.label).toBe('Open provider site')
    expect(handoff.href.startsWith('https://empresa.lidl.es/')).toBe(true)
  })

  it('does not hand off when no operator is source-backed, or when sources conflict', () => {
    expect(providerHandoff(st({ name: 'Calle sin operador', source: 'OpenStreetMap public tags' }))).toBeNull()
    expect(providerHandoff(st({ operator: 'Operator A', raw: { operator: 'Operator B' }, name: 'Repsol way' }))).toBeNull()
  })

  it('never matches a needle inside a longer word', () => {
    expect(providerAppUrl(st({ name: 'Logistic park — nave 4' }))).toBeNull()
    expect(providerAppUrl(st({ operator: 'Gas Natural Fenosa' }))?.provider).not.toBe('GiC (eCoVe)')
  })
})
