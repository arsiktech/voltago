import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import type { Station } from '../types'
import { cardCautions, mapPinPreview } from '../lib/stationPresentation'
import { StationCard } from './StationCard'
import StationDetails from './StationDetails'

vi.mock('./VehicleEstimate', () => ({ VehicleEstimate: () => null }))
const row = (overrides: Partial<Station> = {}): Station => ({
  id: 'audit', name: 'Example charger', lat: 41.38, lon: 2.18, lead: true,
  price_min: 0.22, decision: { level: 'verify', label: 'Verify first', reason: 'Not live' },
  access: { level: 'restricted', note: 'Members only.' },
  parking: { paid: true, label: 'Paid parking', note: 'Parking costs extra.' },
  claim_conflict: { summary: 'Check both sources.' },
  pricing: [{ price: 0.22, valid_from: '00:00', valid_until: '08:00' }],
  price_window_status: { label: '€0.22/kWh · 00:00–08:00', is_currently_within_standard: false },
  ...overrides,
})

describe('compact station cards', () => {
  it('has one decision and one caution indicator, exposing every caution to assistive technology', () => {
    const markup = renderToStaticMarkup(<StationCard st={row()} animateIn={false} />)
    expect(markup.match(/data-card-caution=/g)).toHaveLength(1)
    expect(markup).toContain('Restricted · +3 cautions')
    expect(markup).toContain('aria-label="Restricted; Sources disagree; Paid parking; Time-limited price. Open Details for all cautions."')
    expect(markup).toContain('Charging price')
    expect(markup).toContain('Conditions apply')
    expect(markup).toContain('Not live')
    expect(markup).not.toContain('>Charging<')
  })

  it('keeps all conditions available even when deferred source details are pending', () => {
    const client = new QueryClient()
    const markup = renderToStaticMarkup(<QueryClientProvider client={client}><StationDetails st={row({ detail_snapshot: 'pending' })} /></QueryClientProvider>)
    for (const text of ['Restricted', 'Members only.', 'Sources disagree', 'Paid parking', 'Parking costs extra.', 'Price window, not active now:', '00:00–08:00']) expect(markup).toContain(text)
    client.clear()
  })

  it.each<Partial<Station>>([
    { source_reported_unavailable: { summary: 'Provider reports a fault.' } },
    { status: 'OUT_OF_SERVICE' },
    { status: 'OUTOFORDER' },
    { decision: { level: 'avoid', label: 'Unavailable', reason: 'Source reports an outage.' } },
    { lead: false, connector_summary: { total: 2, out_of_service: 2 } },
  ])('prioritizes an out-of-service warning above other cautions', overrides => {
    expect(cardCautions(row(overrides))[0].label).toBe('Out of service')
  })

  it('does not turn a stale occupied connector count into a new outage claim', () => {
    expect(cardCautions(row({ connector_summary: { total: 2, out_of_service: 2 } }))[0].label).toBe('Restricted')
  })

  it('does not label stale or unverified source data live', () => {
    for (const overrides of [{ cache_stale: true }, { verification: { live_verified: false } }]) {
      const markup = renderToStaticMarkup(<StationCard st={row({ ...overrides, lead: false })} animateIn={false} />)
      expect(markup).toContain('Not live')
      expect(markup).toContain('Source data')
      expect(markup).not.toContain('>Live<')
    }
  })

  it('keeps an unknown price explicit, never substituting zero', () => {
    const markup = renderToStaticMarkup(<StationCard st={row({ price_min: null })} animateIn={false} />)
    expect(markup).toContain('Unknown')
    expect(markup).not.toContain('>Free<')
  })
})

describe('map pin labels', () => {
  it('shows only the verified price and power, never connector or operator prose', () => {
    expect(mapPinPreview(row({ verification: { price_verified: true }, max_power_kw: 3.6,
      operator: 'A very long operator name', connector_labels: ['Wall outlet (connector unspecified)'] })))
      .toEqual({ price: '€0.22', detail: '3.6 kW' })
  })
  it('leaves unverified and unknown prices as unpriced points', () => {
    expect(mapPinPreview(row())).toBeNull()
    expect(mapPinPreview(row({ price_min: null, verification: { price_verified: true } }))).toBeNull()
  })
  it('allows a verified Free label and omits an unknown power hint', () => {
    expect(mapPinPreview(row({ price_min: 0, verification: { price_verified: true } }))).toEqual({ price: 'Free', detail: '' })
  })
})
