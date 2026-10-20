import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { SourceRef, Station } from '../types'
import { SourceProvenance } from './SourceProvenance'
import { availability, isRouteReady, passesFilters, DEFAULT_FILTERS } from '../lib/format'

const row = (source: SourceRef, overrides: Partial<Station> = {}): Station => ({
  id: 'existing-station', name: 'Source fixture', lat: 41.4, lon: 2.1,
  status: 'AVAILABLE', available: 1, total_evse: 2, lead: false,
  verification: { live_verified: true, level: 'live' },
  decision: { level: 'go', label: 'Go now', reason: 'Fresh source status' },
  sources: [source], ...overrides,
})

const endolla: SourceRef = {
  name: 'Barcelona Endolla live', url: 'https://opendata-ajuntament.barcelona.cat/',
  attribution: 'Ajuntament de Barcelona', license: 'CC BY 4.0',
  license_url: 'https://creativecommons.org/licenses/by/4.0/',
  transformation: 'Joined location, station and port identifiers; normalized status.',
  source_fetched_at: '2026-10-20T16:30:00Z',
  source_published_at: '2026-10-20T16:29:00Z',
  source_last_updated: '2024-01-01T00:00:00Z',
}

describe('public live status disclosure', () => {
  it('keeps retrieval, publication and last-change evidence separate with Endolla attribution', () => {
    const station = row(endolla)
    const markup = renderToStaticMarkup(<SourceProvenance st={station} />)
    expect(markup).toContain('Ajuntament de Barcelona')
    expect(markup).toContain('https://creativecommons.org/licenses/by/4.0/')
    expect(markup).toContain('CC BY 4.0')
    expect(markup).toContain('Joined location, station and port identifiers')
    for (const [label, value] of [
      ['Retrieved:', endolla.source_fetched_at],
      ['Feed published:', endolla.source_published_at],
      ['Source updated:', endolla.source_last_updated],
    ]) {
      expect(markup).toContain(label)
      expect(markup).toContain(value)
    }
    expect(isRouteReady(station)).toBe(true)
  })

  it('credits Spirii and the operator without a tariff or invented free price', () => {
    const station = row({ name: 'Spirii AFIR', attribution: 'Spirii; PowerGo',
      url: 'https://docs.spirii.com/developers/afir-compliance' })
    const markup = renderToStaticMarkup(<SourceProvenance st={station} />)
    expect(markup).toContain('Spirii; PowerGo')
    expect(markup).not.toContain('Charging tariff')
    expect(markup).not.toContain('€/kWh')
    expect(passesFilters(station, { ...DEFAULT_FILTERS, maxPrice: 0.25 }, '')).toBe(false)
  })

  it('does not promote stale or failed source rows with retained AVAILABLE counters', () => {
    for (const station of [
      row(endolla, { cache_stale: true }),
      row(endolla, { verification: { live_verified: false, level: 'unverified' } }),
      row(endolla, { lead: true }),
    ]) {
      expect(isRouteReady(station)).toBe(false)
      expect(availability(station).live).toBe(false)
    }
  })

  it('rejects unsafe provider and licence links', () => {
    const markup = renderToStaticMarkup(<SourceProvenance st={row({
      ...endolla, url: 'javascript:alert(1)', license_url: 'data:text/html,unsafe',
    })} />)
    expect(markup).not.toContain('href="javascript:')
    expect(markup).not.toContain('href="data:')
  })
})
