import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import type { PriceEvidence as PriceEvidenceItem, Station } from '../types'
import { DEFAULT_FILTERS, decisionLevel, hasVerifiedPrice, isRouteReady, passesFilters, priceText, priceUnit } from '../lib/format'
import { chargingEstimate, exampleVehicle } from '../lib/vehicles'
import { PriceEvidence } from './PriceEvidence'
import { StationCard } from './StationCard'
import StationDetails from './StationDetails'

vi.mock('./VehicleEstimate', () => ({ VehicleEstimate: () => null }))

const evidence: PriceEvidenceItem[] = [
  {
    kind: 'repsol_network_tariff', source: 'Repsol', label: 'Upstream English must not select the UI label',
    text: 'From €0.29/kWh; station tariff may differ', source_url: 'https://www.repsol.es/charging/tariffs',
    source_updated_at: '2025-01-03T08:00:00Z', fetched_at: '2026-10-20T01:00:00Z',
    price_per_kwh: 0.29, currency: 'EUR',
    rates: [{ kind: 'energy', label: 'normal', min: 0.36, max: null, from: false, unit: 'kWh', after_minutes: null },
      { kind: 'energy', label: 'fast', min: 0.48, max: null, from: true, unit: 'kWh', after_minutes: null },
      { kind: 'energy', label: 'ultrafast', min: 0.56, max: null, from: false, unit: 'kWh', after_minutes: null }],
  },
  {
    kind: 'electromaps_community', source: 'Electromaps', label: 'Free and live',
    text: 'Reported €0.19/kWh; confirm onsite', source_url: 'https://www.electromaps.com/charging-stations/example',
    source_updated_at: '2024-02-03T23:30:00-05:00', fetched_at: '2026-10-20T02:00:00Z',
    price_per_kwh: 0.19, currency: 'EUR', parking_price: '€2/hour after 30 minutes',
    rates: [{ kind: 'energy', label: null, min: 0.35, max: 0.54, from: false, unit: 'kWh', after_minutes: null }],
  },
  {
    kind: 'chargemap_pass', source: 'Chargemap', label: 'Operator direct price',
    text: 'From €0.48 per started kWh; idle €0.10/min after 45-minute grace',
    source_url: 'https://chargemap.com/charging-station/example', source_updated_at: null,
    fetched_at: '2026-10-19T22:00:00Z', price_per_kwh: 0.48, currency: 'EUR',
    tariff_details: { idle_fee: 0.1, grace_minutes: 45 },
    rates: [{ kind: 'energy', label: 'all', min: 0.599, max: null, from: false, unit: 'started_kWh', after_minutes: null },
      { kind: 'idle', label: null, min: 0.111, max: null, from: false, unit: 'minute', after_minutes: 15 }],
  },
]

const labels = [
  'Repsol published tariff · station may differ',
  'Electromaps community price · 2024-02-03',
  'Price with Chargemap Pass',
]

function station(overrides: Partial<Station> = {}): Station {
  return { id: 'price-evidence', name: 'Example charger', lat: 40, lon: -3, ...overrides }
}

const renderers = [
  ['card', (st: Station) => renderToStaticMarkup(<StationCard st={st} animateIn={false} />)],
  ['details', (st: Station) => renderToStaticMarkup(<StationDetails st={st} />)],
] as const

describe.each(renderers)('additional price evidence on %s', (_name, render) => {
  it.each(evidence.map((item, index) => ({ item, label: labels[index] })))('renders $item.kind with its own label, compact rates and date', ({ item, label }) => {
    const markup = render(station({ price_evidence: [item] }))
    expect(markup).toContain(label)
    expect(markup).not.toContain(item.text)
    expect(markup).toContain([
      'Normal €0.36 · Fast from €0.48 · Ultra-fast €0.56 per kWh',
      '€0.35–0.54 per kWh',
      '€0.599 per started kWh · idle €0.111/min after 15 min',
    ][evidence.indexOf(item)])
    expect(markup).toContain(`data-price-evidence="${item.kind}"`)
    expect(markup).toContain(`href="${item.source_url}"`)
    expect(markup).not.toContain(item.label)
    if (item.kind === 'electromaps_community') {
      expect(markup).not.toContain('2026-10-20')
      expect(markup).not.toContain('2024-02-04')
      expect(markup).toContain('Parking: €2/hour after 30 minutes')
    } else {
      expect(markup).toContain(`Checked: ${item.fetched_at.slice(0, 10)}`)
      expect(markup).not.toContain('2025-01-03')
    }
  })

  it('hides the redundant Repsol guide only on verified-price cards, keeping it in details', () => {
    const markup = render(station({
      price_min: 0.37, verification: { price_verified: true },
      charge_claim: { summary: 'Official station tariff €0.37/kWh', source: 'Operator' },
      price_evidence: evidence,
    }))
    labels.forEach((label, index) => expect(markup.includes(label)).toBe(_name !== 'card' || index !== 0))
    expect(markup.match(/data-price-evidence=/g)).toHaveLength(_name === 'card' ? 2 : 3)
    if (_name === 'card') expect(markup).toContain('€0.37')
    else expect(markup).toContain('Official station tariff €0.37/kWh')
    expect(markup).not.toContain('Operator direct price')
    expect(markup).not.toContain('Free and live')
  })

  it('retains old payload rendering when evidence is missing or empty', () => {
    const old = render(station({ price_min: 0.37 }))
    expect(old).not.toContain('Additional price evidence')
    expect(render(station({ price_min: 0.37, price_evidence: [] }))).toBe(old)
  })

  it('keeps directory claims beside a live tariff without changing its decision', () => {
    const live = station({
      price_min: 0.37, price_evidence: evidence, available: 1,
      verification: { price_verified: true, live_verified: true, level: 'live' },
      decision: { level: 'go', label: 'Go now', reason: 'Live verified connector available' },
      charge_claim: { summary: 'Live station tariff €0.37/kWh' },
    })
    expect(isRouteReady(live)).toBe(true)
    const markup = render(live)
    labels.forEach((label, index) => expect(markup.includes(label)).toBe(_name !== 'card' || index !== 0))
    expect(markup).toContain('Go now')
    expect(priceText(live)).toBe('€0.37')
  })

  it('shows unknown source dates without substituting the fresh download', () => {
    const markup = render(station({ price_evidence: [{ ...evidence[1], source_updated_at: null }] }))
    expect(markup).toContain('Electromaps community price · Date unknown')
    expect(markup).not.toContain('2026-10-20')
    const checked = render(station({ price_evidence: [{ ...evidence[0], fetched_at: '' }] }))
    expect(checked).toContain('Checked: Date unknown')
  })
})

describe('price evidence boundaries', () => {
  it('falls back for missing, empty or invalid rates, never to raw source prose or numeric metadata', () => {
    for (const rates of [undefined, [], [{ kind: 'energy', label: null, min: -0.35, max: null, from: false, unit: 'kWh', after_minutes: null }]]) {
      const item = { ...evidence[1], rates } as PriceEvidenceItem
      const markup = renderToStaticMarkup(<PriceEvidence items={[item]} />)
      expect(markup).toContain('See source for prices')
      expect(markup).not.toContain(item.text)
      expect(markup).not.toContain('€0.19')
    }
  })

  it('keeps Repsol on unpriced and unverified cards, including a stray verification flag', () => {
    for (const st of [station(), station({ price_min: 0.37 }), station({ verification: { price_verified: true } })]) {
      const markup = renderToStaticMarkup(<StationCard st={{ ...st, price_evidence: evidence }} animateIn={false} />)
      expect(markup).toContain(labels[0])
    }
  })

  it('keeps all numeric metadata out of primary prices, filters, decisions and EV estimates', () => {
    const now = Date.parse('2026-10-20T10:00:00Z')
    const vehicle = exampleVehicle(new Date(now).toISOString())
    const onlyEvidence = station({ price_evidence: evidence.map(item => ({
      ...item, price_per_kwh: 0, tariff_details: { energy_eur_per_kwh: 0, unconditional_free: true },
      rates: item.rates?.map(rate => ({ ...rate, min: 0, max: null })),
    })) })
    expect(priceText(onlyEvidence)).toBe('—')
    expect(priceUnit(onlyEvidence)).toBe('')
    expect(hasVerifiedPrice(onlyEvidence)).toBe(false)
    expect(isRouteReady(onlyEvidence)).toBe(false)
    expect(decisionLevel(onlyEvidence)).toBe(decisionLevel(station()))
    expect(passesFilters(onlyEvidence, { ...DEFAULT_FILTERS, maxPrice: 0 }, '')).toBe(false)
    expect(passesFilters(onlyEvidence, { ...DEFAULT_FILTERS, maxPrice: 0.5 }, '')).toBe(false)
    expect(chargingEstimate(vehicle, 80, onlyEvidence, now).cost).toBeNull()
    const verified = { ...onlyEvidence, price_min: 0.37, verification: { price_verified: true } }
    expect(priceText(verified)).toBe('€0.37')
    expect(passesFilters(verified, { ...DEFAULT_FILTERS, maxPrice: 0.25 }, '')).toBe(false)
    expect(chargingEstimate(vehicle, 80, verified, now).cost).toBeCloseTo(5.55)
  })

  it('renders literal text and rejects unsafe links through the existing sanitizer', () => {
    const markup = renderToStaticMarkup(<PriceEvidence items={evidence.map(item => ({
      ...item, text: '<img src=x onerror=alert(1)> from €0.40/kWh',
      source_url: 'javascript:alert(1)',
    }))} />)
    expect(markup).not.toContain('onerror')
    expect(markup).not.toContain('<img')
    expect(markup).not.toContain('href=')
    expect(markup).not.toMatch(/pill-|badge|text-go|live-dot/)
  })

  it('ignores unknown kinds and fields without borrowing their upstream label', () => {
    const payload = JSON.parse(JSON.stringify({ ...station(), price_evidence: [
      ...evidence, { ...evidence[0], kind: 'future_price', label: 'Verified tariff', future_field: true },
    ], future_station_field: true })) as Station
    const markup = renderToStaticMarkup(<StationCard st={payload} animateIn={false} />)
    expect(markup.match(/data-price-evidence=/g)).toHaveLength(3)
    expect(markup).not.toContain('Verified tariff')
  })

  it.each(['2026-02-30', 'not a date', ''])('keeps invalid date %s explicit', source_updated_at => {
    const markup = renderToStaticMarkup(<PriceEvidence items={[{ ...evidence[1], source_updated_at }]} />)
    expect(markup).toContain('Electromaps community price · Date unknown')
  })

  it('keeps summary evidence visible while deferred details load and when old details omit it', () => {
    const client = new QueryClient()
    const summary = station({ detail_snapshot: 'fixture-snapshot', price_evidence: evidence })
    const render = () => renderToStaticMarkup(<QueryClientProvider client={client}><StationDetails st={summary} /></QueryClientProvider>)
    const waiting = render()
    expect(waiting).toContain('Loading source details…')
    labels.forEach(label => expect(waiting).toContain(label))
    client.setQueryData(['station-details', summary.id, summary.detail_snapshot], station())
    const loaded = render()
    labels.forEach(label => expect(loaded).toContain(label))
    client.clear()
  })
})
