import { describe, expect, it, vi } from 'vitest'
import { createElement } from 'react'

// Source-detail tests render StationDetails without the app's vehicle garage
// provider; vehicle estimates have their own coverage in vehicles.test.ts.
vi.mock('../components/VehicleEstimate', () => ({ VehicleEstimate: () => null }))
import { renderToStaticMarkup } from 'react-dom/server'
import type { DecisionLevel, Filters, Station } from '../types'
import StationDetails from '../components/StationDetails'
import stationTruthCasesRaw from '../../../contracts/station_truth_cases.json?raw'
import {
  DECISION_META,
  DEFAULT_FILTERS,
  availability,
  chooseChargersData,
  cityLabel,
  corroboration,
  connectorInventoryText,
  decisionLevel,
  distanceText,
  hasVerifiedPrice,
  googleMapsLink,
  inViewport,
  isMapLocationVerified,
  isParkingRisk,
  isRouteReady,
  mapsUrl,
  passesFilters,
  powerText,
  priceText,
  priceUnit,
  privateAccess,
  publicWebUrl,
  remoteStartAction,
  responseHasMixedLiveAuthority,
  responseHasNoLiveAuthority,
  resultScopeLabel,
  sortNearbyBySafetyAndDistance,
  sourceTier,
  sourcePhotoLinks,
  sourcePageConnectors,
  stationAgeText,
  stationPhoto,
  statusTrustSummary,
  tariffWindow,
  tariffNotes,
} from './format'

const mk = (partial: Partial<Station>): Station => ({
  id: 't',
  name: 'Test',
  lat: 39.47,
  lon: -0.37,
  ...partial,
})

const filters = (over: Partial<Filters> = {}): Filters => ({ ...DEFAULT_FILTERS, ...over })

describe('priceText', () => {
  it('free flags and zero price read Free', () => {
    expect(priceText(mk({ is_free: true }))).toBe('Free')
    expect(priceText(mk({ price_min: 0 }))).toBe('Free')
  })
  it('cheap price formats euros; unknown is dash', () => {
    expect(priceText(mk({ price_min: 0.19 }))).toBe('€0.19')
    expect(priceText(mk({ price_min: null }))).toBe('—')
  })
})

describe('source tariff presentation', () => {
  it('shows a published hourly charge without inventing an energy price', () => {
    const station = mk({ price_min: null, tariff_details: { hour_eur: 2, conditions: ['First hour free'] } })
    expect(priceText(station)).toBe('€2.00')
    expect(priceUnit(station)).toBe('/hour')
    expect(tariffNotes(station)).toEqual(['First hour free'])
    expect(passesFilters(station, filters({ maxPrice: 0.25 }), '')).toBe(false)
  })

  it('keeps an energy price, minimum charge and extra time component together', () => {
    const station = mk({ price_min: 0.35, tariff_details: {
      hour_eur: 1, minimum_eur: 2, conditions: ['Customers only', 'Customers only'],
    } })
    expect(priceText(station)).toBe('€0.35')
    expect(priceUnit(station)).toBe('/kWh')
    expect(tariffNotes(station)).toEqual(['Minimum charge €2.00', 'Charging time €1.00/hour', 'Customers only'])
  })

  it('keeps an unknown or conditional energy price unknown and rejects malformed amounts', () => {
    const station = mk({ tariff_details: { energy_eur_per_kwh: 0, unconditional_free: false, conditions: ['With a purchase'] } })
    expect(priceText(station)).toBe('—')
    expect(priceUnit(station)).toBe('')
    expect(tariffNotes(station)).toEqual(['With a purchase'])
    expect(priceText(mk({ tariff_details: { hour_eur: -1 } }))).toBe('—')
    expect(priceUnit(mk({ tariff_details: { hour_eur: Number.NaN } }))).toBe('')
  })

  it('preserves minimum and flat session components without treating them as energy charges', () => {
    const station = mk({ tariff_details: { components: [
      { type: 'MINIMUM', price: 2, unit: 'session' },
      { type: 'FLAT', price: 0.5, unit: 'session' },
    ] } })
    expect(priceText(station)).toBe('—')
    expect(tariffNotes(station)).toEqual(['Minimum charge €2.00', 'Session fee €0.50'])
  })
})

describe('source detail inventory and links', () => {
  it('shows quantities only when published and retains decimal power and unknown connector labels', () => {
    expect(connectorInventoryText({ label: 'Type 2', count: 3, power_kw: 7.4 })).toBe('3 × Type 2 · 7.4 kW')
    expect(connectorInventoryText({ label: 'Unknown connector', count: null })).toBe('Unknown connector')
    expect(connectorInventoryText({ label: 'CCS2' })).toBe('CCS2')
    expect(connectorInventoryText({ label: 'Type 2', count: 1, power_kw: 22.1 })).toBe('1 × Type 2 · 22.1 kW')
  })

  it('keeps a different source-page inventory separate from the selected source', () => {
    const pageInventory = [
      { label: 'CCS2', count: 1, power_kw: 50 },
      { label: 'CHAdeMO', count: 1, power_kw: 50 },
      { label: 'Type 2', count: 1, power_kw: 43 },
      { label: 'Type 2', count: 6, power_kw: 22 },
    ]
    const station = mk({ lead: true, connectors: [{ label: 'Type 2', count: 2, power_kw: 22 }],
      source_details: { connectors: pageInventory, connector_count: 9, max_power_kw: 50,
        source_name: 'Canarias te recarga', source_url: 'https://canariasterecarga.com/es/p00246/' },
    })
    expect(sourcePageConnectors(station)).toEqual(pageInventory)
    const markup = renderToStaticMarkup(createElement(StationDetails, { st: station }))
    expect(markup).toContain('2 × Type 2 · 22 kW')
    expect(markup).toContain('Source-page connectors')
    expect(markup).toContain('Canarias te recarga')
    expect(markup).toMatch(/Published sockets: (?:<!-- -->)?9/)
    expect(markup).toContain('1 × CCS2 · 50 kW')
    expect(markup).toContain('1 × CHAdeMO · 50 kW')
    expect(markup).toContain('1 × Type 2 · 43 kW')
    expect(markup).toContain('6 × Type 2 · 22 kW')
    expect(station.connectors?.[0].count).toBe(2)
  })

  it('does not duplicate the same inventory because source IDs, URLs or row order differ', () => {
    const station = mk({ connectors: [{ label: 'Type 2', count: 2, power_kw: 22 }, { label: 'CCS2', power_kw: 50 }],
      source_details: { connectors: [
        { label: 'CCS2', power_kw: 50, source_url: 'https://canariasterecarga.com/es/p123/' },
        { label: 'Type 2', count: 2, power_kw: 22, connector_id: 'directory-row' },
      ] },
    })
    expect(sourcePageConnectors(station)).toEqual([])
    expect(renderToStaticMarkup(createElement(StationDetails, { st: station }))).not.toContain('Source-page connectors')
  })

  it('offers safe source photo links while keeping automatic image loading disabled', () => {
    const station = mk({ media: { photos: [
      { url: 'https://canariasterecarga.com/photo.jpg', source_url: 'https://canariasterecarga.com/es/p123/', caption: 'Charger entrance' },
      { url: 'file:///private/photo.jpg', source_url: 'javascript:alert(1)' },
    ] } })
    expect(stationPhoto(station)).toBeNull()
    expect(sourcePhotoLinks(station)).toEqual([{
      href: 'https://canariasterecarga.com/es/p123/', source: 'public source', caption: 'Charger entrance',
    }])
  })

  it('renders all source connectors, source facts and conditions without loading unapproved photos', () => {
    const station = mk({ lead: true,
      connectors: Array.from({ length: 8 }, (_, index) => ({ label: index === 7 ? 'Unknown connector' : 'Type 2', count: index === 0 ? 3 : null, power_kw: 7.4, source_url: 'https://canariasterecarga.com/es/p123/' })),
      source_details: { source_url: 'https://canariasterecarga.com/es/p123/', description: 'Parking level -1', opening_hours: '08:00–22:00', access: 'Customers only', contact: { phone: '123456789', website: 'javascript:alert(1)' } },
      tariff_details: { text: '0.35 €/kWh, minimum €2', minimum_eur: 2, conditions: ['Check onsite signs'] },
      media: { photos: [{ url: 'https://canariasterecarga.com/photo.jpg', caption: 'Entrance', source_url: 'https://canariasterecarga.com/es/p123/' }] },
    })
    const markup = renderToStaticMarkup(createElement(StationDetails, { st: station }))
    const inventory = markup.match(/aria-label="Connector inventory"[\s\S]*?<\/section>/)?.[0] || ''
    expect(inventory.match(/Open Canarias te recarga/g)).toHaveLength(8)
    expect(markup).toContain('3 × Type 2 · 7.4 kW')
    expect(markup).toContain('Unknown connector')
    expect(markup).toContain('08:00–22:00')
    expect(markup).toContain('Minimum charge €2.00')
    expect(markup).toContain('Check onsite signs')
    expect(markup).toContain('View photo at Canarias te recarga')
    expect(markup).not.toContain('<img')
    expect(markup).not.toContain('javascript:')
  })
})

describe('hasVerifiedPrice', () => {
  it('fails closed for a price-shaped source row without an explicit price contract', () => {
    expect(hasVerifiedPrice(mk({ is_free: true, price_min: 0 }))).toBe(false)
    expect(hasVerifiedPrice(mk({ price_min: 0.19, verification: { price_verified: true } }))).toBe(true)
  })
})

describe('powerText', () => {
  it('keeps real kW values and recovers OSM watts stored as kW', () => {
    expect(powerText(50)).toBe('50 kW')
    expect(powerText(2.3)).toBe('2.3 kW')
    expect(powerText(7400)).toBe('7.4 kW')
  })
})

describe('resultScopeLabel', () => {
  it('describes the active price gate honestly', () => {
    expect(resultScopeLabel(filters({ maxPrice: 0.4 }))).toBe('chargers ≤ €0.40/kWh')
    expect(resultScopeLabel(filters({ maxPrice: 0 }))).toBe('free chargers')
    expect(resultScopeLabel(filters({ maxPrice: 0.65 }))).toBe('chargers ≤ €0.65/kWh')
    expect(resultScopeLabel(filters({ maxPrice: null }))).toBe('public chargers')
  })
})

describe('statusTrustSummary', () => {
  it('treats an HTTP-200 live-source failure response as having no live authority', () => {
    const response = {
      stations: [],
      leads: [mk({ id: 'lead', lead: true })],
      cache: { hit: false, live_failed: true },
      meta: { coverage: { live_route_ready: 0 } },
    }
    expect(responseHasNoLiveAuthority(response)).toBe(true)
    expect(statusTrustSummary({
      overview: false,
      sourceBackedOnly: responseHasNoLiveAuthority(response),
      isFetching: false,
      stale: false,
      updated: 'just now',
      liveReady: 0,
      leadCount: 1,
    })).toEqual({
      label: 'Saved chargers · not live',
      badge: '1 leads · not live',
    })
  })

  it('never calls the national leads-only overview live', () => {
    expect(statusTrustSummary({ overview: true, sourceBackedOnly: false, isFetching: false, stale: false, updated: 'just now', liveReady: 0, leadCount: 913 })).toEqual({
      label: 'Saved chargers · not live',
      badge: '913 leads · not live',
    })
  })

  it('never calls a stale cached response live', () => {
    expect(statusTrustSummary({ overview: false, sourceBackedOnly: false, isFetching: false, stale: true, updated: '2 min ago', liveReady: 0, leadCount: 20 })).toEqual({
      label: 'Cached chargers · not live',
      badge: 'Not live',
    })
  })

  it('labels any fresh-plus-stale multi-source response as mixed and preserves live authority', () => {
    const response = {
      stations: [
        mk({
          id: 'fresh-gaia',
          lead: false,
          cache_stale: false,
          verification: { level: 'live', live_verified: true },
        }),
        mk({
          id: 'stale-mapareve',
          lead: false,
          cache_stale: true,
          verification: { level: 'live', live_verified: true },
        }),
      ],
      leads: [],
      cache: { hit: true, stale: false, fresh_live_authority: true },
    }
    expect(responseHasNoLiveAuthority(response)).toBe(false)
    expect(responseHasMixedLiveAuthority(response)).toBe(true)
    expect(statusTrustSummary({
      overview: false,
      sourceBackedOnly: false,
      isFetching: false,
      stale: true,
      mixedLive: true,
      updated: 'just now',
      liveReady: 1,
      leadCount: 3,
    })).toEqual({
      label: 'Live source + cached evidence · updated just now',
      badge: '1 live · cached fallback',
    })
    expect(statusTrustSummary({
      overview: false,
      sourceBackedOnly: false,
      isFetching: true,
      stale: true,
      mixedLive: true,
      updated: 'just now',
      liveReady: 1,
      leadCount: 3,
    })).toEqual({
      label: 'Refreshing live + cached evidence…',
      badge: '1 live · cached fallback',
    })

    const reconciledFreshOnly = {
      stations: [response.stations[0]],
      leads: [],
      cache: {
        hit: true,
        stale: true,
        live_failed: true,
        fresh_live_authority: true,
        mixed_live: false,
      },
    }
    expect(responseHasNoLiveAuthority(reconciledFreshOnly)).toBe(false)
    expect(responseHasMixedLiveAuthority(reconciledFreshOnly)).toBe(false)
  })

  it('keeps a stale cached response not live while a retry is in flight', () => {
    expect(statusTrustSummary({ overview: false, sourceBackedOnly: false, isFetching: true, stale: true, updated: '2 min ago', liveReady: 3, leadCount: 20 })).toEqual({
      label: 'Cached chargers · not live',
      badge: 'Not live',
    })
  })

  it('never calls instant leads live while a nearby live request is pending or has failed', () => {
    expect(statusTrustSummary({ overview: false, sourceBackedOnly: true, isFetching: true, stale: false, updated: '', liveReady: 4, leadCount: 20 })).toEqual({
      label: 'Saved chargers · not live',
      badge: '20 leads · not live',
    })
    expect(statusTrustSummary({ overview: false, sourceBackedOnly: true, isFetching: false, stale: false, updated: 'just now', liveReady: 4, leadCount: 20 })).toEqual({
      label: 'Saved chargers · not live',
      badge: '20 leads · not live',
    })
  })

  it('keeps live wording for a fresh nearby response', () => {
    expect(statusTrustSummary({ overview: false, sourceBackedOnly: false, isFetching: false, stale: false, updated: 'just now', liveReady: 2, leadCount: 20 })).toEqual({
      label: 'Live · updated just now',
      badge: '2 live · 20 leads',
    })
  })
})

describe('stationAgeText', () => {
  it('distinguishes lead evidence age from live freshness when minutes are supplied', () => {
    expect(stationAgeText(mk({ lead: true, freshness_minutes: 180 }))).toBe('As of 3 h ago')
    expect(stationAgeText(mk({ lead: false, freshness_minutes: 5 }))).toBe('Updated 5 min ago')
  })

  it('keeps old evidence readable instead of showing thousands of days', () => {
    expect(stationAgeText(mk({ lead: true, freshness_minutes: 60 * 24 * 60 }))).toBe(
      'As of 2 mo ago',
    )
    expect(stationAgeText(mk({ lead: true, freshness_minutes: 60 * 24 * 730 }))).toBe(
      'As of 2 y ago',
    )
  })

  it('distinguishes lead evidence age from live freshness when only a timestamp is supplied', () => {
    const realNow = Date.now
    Date.now = () => Date.parse('2026-07-13T06:00:00+00:00')
    try {
      expect(stationAgeText(mk({ lead: true, last_seen: '2026-07-13T05:00:00+00:00' }))).toBe(
        'As of 1 h ago',
      )
      expect(stationAgeText(mk({ lead: false, last_seen: '2026-07-13T05:45:00+00:00' }))).toBe(
        'Updated 15 min ago',
      )
    } finally {
      Date.now = realNow
    }
  })
})

describe('mapsUrl', () => {
  it('uses provider navigation URLs before coordinate fallback', () => {
    expect(mapsUrl(mk({ navigation: { google: 'https://maps.example/google' } }))).toBe('https://maps.example/google')
    expect(mapsUrl(mk({ navigation: { directions: 'https://maps.example/directions' } }))).toBe(
      'https://maps.example/directions',
    )
    expect(mapsUrl(mk({ lat: 39.47, lon: -0.377 }))).toBe(
      'https://www.google.com/maps/search/?api=1&query=39.47,-0.377',
    )
  })

  it('skips unsafe navigation URLs and falls back to a public web map', () => {
    expect(
      mapsUrl(
        mk({
          navigation: {
            google: 'javascript:alert(1)',
            directions: 'https://maps.example/safe',
          },
        }),
      ),
    ).toBe('https://maps.example/safe')
    expect(mapsUrl(mk({ navigation: { google: 'file:///tmp/map' }, lat: 1, lon: 2 }))).toBe(
      'https://www.google.com/maps/search/?api=1&query=1,2',
    )
  })
})

describe('googleMapsLink', () => {
  it('opens a known listing separately from coordinate navigation', () => {
    const href = 'https://maps.app.goo.gl/YXKoYi8GzoUeYwiF7'
    const station = mk({ navigation: { google_listing: href, google: 'https://www.google.com/maps/search/?api=1&query=1,2' } })
    expect(googleMapsLink(station)).toEqual({ href, listing: true })
    expect(mapsUrl(station)).toContain('query=1,2')
  })

  it('uses place identifiers already published in navigation or provenance', () => {
    for (const href of [
      'https://www.google.com/maps/search/?api=1&query=charger&query_place_id=ChIJ_example',
      'https://www.google.com/maps?cid=123456',
      'https://www.google.com/maps/place/Charger/data=!4m5!3m4!1s0xc:0xd',
    ]) {
      expect(googleMapsLink(mk({ navigation: { google: href } }))).toEqual({ href, listing: true })
      expect(googleMapsLink(mk({ source_url: href }))).toEqual({ href, listing: true })
    }
  })

  it('labels a missing match as a local search with the name and address', () => {
    const link = googleMapsLink(mk({ name: 'Buenavista', address: 'C/ La Cuesta, 519', city: 'Buenavista del Norte', lat: 28.362, lon: -16.848 }))
    expect(link.listing).toBe(false)
    const url = new URL(link.href)
    expect(url.origin).toBe('https://www.google.com')
    expect(url.searchParams.get('api')).toBe('1')
    expect(url.searchParams.get('query')).toContain('Buenavista C/ La Cuesta, 519 Buenavista del Norte near 28.362000,-16.848000')
  })

  it.each([
    'https://www.google.com/maps/search/?api=1&query=1,2',
    'https://www.google.com/maps/place/EV+charger',
    'https://www.google.com/maps/dir/?api=1&query_place_id=ChIJ_example',
    'https://google.com.evil.example/maps?cid=123456',
    'https://www.google.com:444/maps?cid=123456',
    'https://user:pass@www.google.com/maps?cid=123456',
    'https://maps.app.goo.gl/',
    'javascript:alert(1)',
  ])('never presents an unsafe or unidentified URL as a listing: %s', (href) => {
    const result = googleMapsLink(mk({ navigation: { google_listing: href, google_search: href } }))
    expect(result.listing).toBe(false)
    expect(result.href).toMatch(/^https:\/\/www.google.com\/maps\/search\//)
  })
})

describe('publicWebUrl', () => {
  it.each([
    'javascript:alert(1)',
    'file:///tmp/secret',
    'data:text/html,boom',
    'custom://provider/station',
    '/relative/path',
    'https://',
    'http://?station=1',
  ])('rejects non-public or hostless URL %s', (value) => {
    expect(publicWebUrl(value)).toBeNull()
  })

  it('accepts and normalizes HTTP(S), including independent paths that mention Feníe', () => {
    expect(publicWebUrl('HTTP://Example.COM/a b')).toBe('http://example.com/a%20b')
    expect(publicWebUrl('https://openchargemap.io/fenie-independent-evidence')).toBe(
      'https://openchargemap.io/fenie-independent-evidence',
    )
  })

  it.each([
    'http://localhost:8773/private',
    'http://station.local/private',
    'http://10.0.0.1/private',
    'https://127.0.0.1/private',
    'https://169.254.169.254/latest/meta-data',
    'https://172.16.4.2/private',
    'https://192.0.2.1/documentation-only',
    'https://192.88.99.1/deprecated-relay',
    'https://192.168.1.1/private',
    'https://198.51.100.2/documentation-only',
    'https://203.0.113.3/documentation-only',
    'https://224.0.0.1/multicast',
    'https://[::1]/private',
    'https://bad..example.com/private',
    'https://-bad.example/private',
    'https://bad-.example/private',
    'https://intranet/private',
  ])('rejects local, private, or link-local web URL %s', (value) => {
    expect(publicWebUrl(value)).toBeNull()
  })
})

type StationTruthCorpus = {
  route_ready: Array<{ id: string; station: Station; expected: boolean }>
  decision_fallback: Array<{ id: string; station: Station; expected: string }>
  public_web_urls: Array<{ value: string; valid: boolean }>
}

const stationTruthCorpus = JSON.parse(stationTruthCasesRaw) as StationTruthCorpus

describe('language-neutral station truth corpus', () => {
  it.each(stationTruthCorpus.route_ready)('$id route readiness', ({ station, expected }) => {
    expect(isRouteReady(station)).toBe(expected)
  })

  it.each(stationTruthCorpus.decision_fallback)('$id decision fallback', ({ station, expected }) => {
    expect(decisionLevel(station)).toBe(expected)
  })

  it.each(stationTruthCorpus.public_web_urls)('$value public URL boundary', ({ value, valid }) => {
    expect(publicWebUrl(value) !== null).toBe(valid)
  })
})

describe('remoteStartAction', () => {
  it('exposes a card handoff only for public start/check pages', () => {
    const action = remoteStartAction(
      mk({
        name: 'Gaia Valencia',
        primary_source: {
          name: 'Gaia',
          kind: 'start_page',
          quality: 'exact',
          url: 'https://www.gaiacharge.com/cpo/jump/abc/start-session/1',
        },
      }),
    )
    expect(action?.href).toBe('https://www.gaiacharge.com/cpo/jump/abc/start-session/1')
    expect(action?.label).toBe('Start/check Gaia')
    expect(action?.ariaLabel).toContain('Gaia Valencia')
  })

  it('exposes a Feníe public-page handoff (permitted source, 2026-07-18)', () => {
    const action = remoteStartAction(
      mk({
        name: 'Feníe Rafal',
        primary_source: {
          name: 'Feníe Energía public map (Gratuitos)',
          kind: 'provider_station_page',
          quality: 'exact',
          url: 'https://recarga.fenieenergia.es/punto-recarga/0421',
        },
      }),
    )
    expect(action?.href).toBe('https://recarga.fenieenergia.es/punto-recarga/0421')
    expect(action?.label).toBe('Start/check Feníe')
    expect(action?.ariaLabel).toContain('Feníe Rafal')
  })

  it('keeps directory, store, and live-map URLs out of the card CTA', () => {
    expect(
      remoteStartAction(
        mk({
          primary_source: { kind: 'station_page', quality: 'exact', url: 'https://map.electromaps.com/en/p/123' },
        }),
      ),
    ).toBeNull()
    expect(
      remoteStartAction(
        mk({
          primary_source: { kind: 'store_page', quality: 'exact', url: 'https://www.consum.es/tiendas/1' },
        }),
      ),
    ).toBeNull()
    expect(
      remoteStartAction(
        mk({
          primary_source: { kind: 'live_map', quality: 'official_data', url: 'https://www.mapareve.es/' },
        }),
      ),
    ).toBeNull()
  })

  it.each([
    'javascript:alert(1)',
    'file:///tmp/provider',
    'data:text/html,provider',
    'custom://provider/station',
    '/provider/station',
  ])('rejects unsafe provider CTA URL %s', (url) => {
    expect(
      remoteStartAction(
        mk({
          primary_source: { name: 'Safe provider', kind: 'start_page', quality: 'exact', url },
        }),
      ),
    ).toBeNull()
  })
})

describe('stationPhoto', () => {
  it('uses the first public media photo thumbnail with source provenance', () => {
    const photo = stationPhoto(
      mk({
        media: {
          photos: [
            {
              url: 'https://cdn.example/full.jpg',
              thumbnail_url: 'https://cdn.example/thumb.jpg',
              display_rights_approved: true,
              source: 'OpenChargeMap media',
              source_url: 'https://map.openchargemap.io/?mode=details&id=1',
              caption: 'Front bay',
            },
          ],
          photo_status: 'public_photo_available',
        },
      }),
    )
    expect(photo?.src).toBe('https://cdn.example/thumb.jpg')
    expect(photo?.href).toBe('https://cdn.example/full.jpg')
    expect(photo?.source).toBe('OpenChargeMap media')
    expect(photo?.caption).toBe('Front bay')
  })

  it('returns null when no public media is present', () => {
    expect(stationPhoto(mk({ media: { photo_status: 'no_public_photo' } }))).toBeNull()
  })

  it('rejects unsafe photo and source-photo URLs and selects the next safe photo', () => {
    const photo = stationPhoto(
      mk({
        media: {
          photos: [
            {
              url: 'javascript:alert(1)',
              thumbnail_url: 'file:///tmp/thumb.jpg',
              source_url: 'data:text/html,source',
            },
            {
              url: 'https://cdn.example/safe.jpg',
              source_url: 'https://openchargemap.io/fenie-independent-photo',
              source: 'Independent directory',
              display_rights_approved: true,
            },
          ],
        },
      }),
    )
    expect(photo?.src).toBe('https://cdn.example/safe.jpg')
    expect(photo?.href).toBe('https://cdn.example/safe.jpg')
    expect(photo?.sourceUrl).toBe('https://openchargemap.io/fenie-independent-photo')
    expect(
      stationPhoto(
        mk({ media: { photos: [{ url: 'custom://photo/1', source_url: 'https://example.com' }] } }),
      ),
    ).toBeNull()
  })

  it('withholds even a technically safe photo until display rights are explicitly approved', () => {
    expect(
      stationPhoto(
        mk({
          media: {
            photos: [{ url: 'https://cdn.example/unapproved.jpg', license: 'unknown' }],
          },
        }),
      ),
    ).toBeNull()
  })
})

describe('distanceText', () => {
  it('formats meters, km, and rounds', () => {
    expect(distanceText(0.5)).toBe('500 m')
    expect(distanceText(3.2)).toBe('3.2 km')
    expect(distanceText(24)).toBe('24 km')
    expect(distanceText(undefined)).toBe('')
  })
})

describe('decisionLevel', () => {
  it('uses the server decision, else lead/station default', () => {
    expect(decisionLevel(mk({ decision: { level: 'go', label: 'Go now', reason: '' } }))).toBe('go')
    expect(decisionLevel(mk({ lead: true }))).toBe('verify')
    expect(decisionLevel(mk({ lead: false }))).toBe('check')
  })
  it('passes through the backend lead/avoid levels (store-locator and off-cap rows)', () => {
    expect(
      decisionLevel(mk({ lead: true, decision: { level: 'lead', label: 'Price lead', reason: '' } })),
    ).toBe('lead')
    expect(
      decisionLevel(mk({ lead: true, decision: { level: 'avoid', label: 'Off cap', reason: '' } })),
    ).toBe('avoid')
  })
  it('unknown future backend levels fall back honestly instead of crashing the pill', () => {
    const unknown = { level: 'brand-new-level' as DecisionLevel, label: 'X', reason: '' }
    expect(decisionLevel(mk({ lead: true, decision: unknown }))).toBe('verify')
    expect(decisionLevel(mk({ lead: false, decision: unknown }))).toBe('check')
  })
  it('every decision level has complete pill metadata (label + style)', () => {
    const levels: DecisionLevel[] = ['go', 'check', 'verify', 'lead', 'avoid']
    for (const level of levels) {
      expect(DECISION_META[level].label).toBeTruthy()
      expect(DECISION_META[level].cls).toBeTruthy()
    }
  })
})

describe('sourceTier', () => {
  it('classifies live vs official vs directory vs community', () => {
    expect(sourceTier(mk({ lead: false }))).toBe('live')
    expect(sourceTier(mk({ lead: true, reliability: 'official-free' }))).toBe('official')
    expect(sourceTier(mk({ lead: true, reliability: 'directory-map-free' }))).toBe('directory')
    expect(sourceTier(mk({ lead: true, reliability: 'community-free' }))).toBe('community')
  })
})

describe('isMapLocationVerified', () => {
  it('keeps live rows on the map when the backend has not explicitly rejected the location', () => {
    expect(isMapLocationVerified(mk({ lead: false }))).toBe(true)
    expect(isMapLocationVerified(mk({ lead: false, verification: { location_verified: false } }))).toBe(false)
  })
  it('keeps official route-checked rows on the map', () => {
    const official = mk({
      lead: true,
      reliability: 'official-free',
      verification: { route_checked: true, location_verified: true, price_verified: true },
    })
    expect(isMapLocationVerified(official)).toBe(true)
  })
  it('keeps only official fallback rows when both location and price are verified', () => {
    const official = mk({
      lead: true,
      reliability: 'official-cheap',
      verification: { location_verified: true, price_verified: true },
    })
    expect(isMapLocationVerified(official)).toBe(true)
  })
  it('keeps exact directory station pages on the map as non-live leads', () => {
    expect(
      isMapLocationVerified(
        mk({
          lead: true,
          reliability: 'directory-map-free',
          verification: { location_verified: true, price_verified: false },
          primary_source: { kind: 'station_page', quality: 'exact', url: 'https://map.electromaps.com/en/p/123' },
        }),
      ),
    ).toBe(true)
    expect(
      isMapLocationVerified(
        mk({
          lead: true,
          reliability: 'official-free',
          verification: { location_verified: true, price_verified: true },
          primary_source: {
            name: 'Gaia',
            kind: 'start_page',
            quality: 'exact',
            url: 'https://www.gaiacharge.com/cpo/jump/abc/start-session/1',
          },
        }),
      ),
    ).toBe(true)
  })
  it('pins exact OSM node/way leads as Not live, never as live', () => {
    const osm = mk({
      lead: true,
      reliability: 'community-free',
      verification: { location_verified: false, price_verified: false, live_verified: false },
      source_url: 'https://www.openstreetmap.org/node/8906632362',
      primary_source: { kind: 'map_object', quality: 'map', url: 'https://www.openstreetmap.org/node/8906632362' },
    })
    expect(isMapLocationVerified(osm)).toBe(true)
    expect(isRouteReady(osm)).toBe(false)
  })
  it('pins official registry locations without a published price, showing no price', () => {
    const official = mk({
      lead: true,
      reliability: 'official-charger-location-price-unknown',
      price_min: null,
      verification: { location_verified: true, price_verified: false },
    })
    expect(isMapLocationVerified(official)).toBe(true)
    expect(hasVerifiedPrice(official)).toBe(false)
  })
  it('hides generic directory, unlocated community, and invalid-coordinate leads from the map', () => {
    expect(
      isMapLocationVerified(
        mk({
          lead: true,
          reliability: 'directory-map-free',
          verification: { location_verified: true, price_verified: true },
          primary_source: { kind: 'provider_homepage', url: 'https://example.com' },
        }),
      ),
    ).toBe(false)
    expect(
      isMapLocationVerified(
        mk({
          lead: true,
          reliability: 'community-free',
          verification: { location_verified: true, price_verified: true },
        }),
      ),
    ).toBe(false)
    expect(isMapLocationVerified(mk({ lat: Number.NaN, lead: false }))).toBe(false)
  })
})

describe('isParkingRisk', () => {
  it('flags paid/metered/time-limited parking only', () => {
    expect(isParkingRisk(mk({ parking: { kind: 'paid_or_metered' } }))).toBe(true)
    expect(isParkingRisk(mk({ parking: { paid: true } }))).toBe(true)
    expect(isParkingRisk(mk({ parking: { kind: 'street_or_municipal' } }))).toBe(false)
  })

  it('keeps a separate paid parking claim risky even when charging is cheap', () => {
    const endolla = mk({
      price_min: 0.22,
      is_cheap: true,
      parking: { kind: 'paid_or_metered', paid: true },
      parking_claim: {
        kind: 'paid_or_metered',
        source: 'Endolla/B:SM parking terms',
        cost_hint: 'Separate B:SM car-park tariff applies.',
      },
    })
    expect(isParkingRisk(endolla)).toBe(true)
  })
})

describe('privateAccess', () => {
  it('treats facility/customer parking as private-context access without calling it paid', () => {
    expect(privateAccess(mk({ parking: { kind: 'facility_or_customer_parking' } }))?.label).toBe(
      'Customer/visitor parking',
    )
    expect(isParkingRisk(mk({ parking: { kind: 'facility_or_customer_parking' } }))).toBe(false)
  })
})

describe('isRouteReady', () => {
  it('celebrates only live go rows with available connectors', () => {
    expect(
      isRouteReady(
        mk({
          verification: { live_verified: true, level: 'live' },
          lead: false,
          available: 1,
          decision: { level: 'go', label: 'Go now', reason: '' },
        }),
      ),
    ).toBe(true)
    expect(
      isRouteReady(
        mk({
          verification: { live_verified: true, level: 'live' },
          lead: false,
          connector_summary: { available: 2, total: 2 },
          decision: { level: 'go', label: 'Go now', reason: '' },
        }),
      ),
    ).toBe(true)
    expect(
      isRouteReady(
        mk({
          verification: { live_verified: true, level: 'live' },
          lead: false,
          status: ' available ',
          decision: { level: 'go', label: 'Go now', reason: '' },
        }),
      ),
    ).toBe(true)
  })

  it('never treats leads, check-first rows, or zero-availability rows as route-ready', () => {
    expect(
      isRouteReady(
        mk({
          verification: { live_verified: true, level: 'live' },
          lead: true,
          available: 1,
          decision: { level: 'go', label: 'Go now', reason: '' },
        }),
      ),
    ).toBe(false)
    expect(
      isRouteReady(
        mk({
          verification: { live_verified: true, level: 'live' },
          lead: false,
          available: 1,
          decision: { level: 'check', label: 'Check first', reason: '' },
        }),
      ),
    ).toBe(false)
    expect(
      isRouteReady(
        mk({
          verification: { live_verified: true, level: 'live' },
          lead: false,
          available: 0,
          status: 'AVAILABLE',
          decision: { level: 'go', label: 'Go now', reason: '' },
        }),
      ),
    ).toBe(false)
  })

  it('fails closed on explicit backend live-verification contradictions', () => {
    const otherwiseReady = {
      lead: false,
      available: 1,
      decision: { level: 'go' as const, label: 'Go now', reason: '' },
    }

    expect(
      isRouteReady(
        mk({
          ...otherwiseReady,
          verification: { live_verified: false, level: 'live' },
        }),
      ),
    ).toBe(false)
    expect(
      isRouteReady(
        mk({
          ...otherwiseReady,
          verification: { live_verified: true, level: 'official' },
        }),
      ),
    ).toBe(false)
    expect(
      isRouteReady(
        mk({
          ...otherwiseReady,
          verification: { live_verified: true, level: ' LIVE ' },
        }),
      ),
    ).toBe(false)
  })
})

describe('sortNearbyBySafetyAndDistance', () => {
  const decision = (level: DecisionLevel) => ({ level, label: level, reason: '' })

  it('keeps live Go-now first, then sorts each honesty tier by distance', () => {
    const rows = [
      mk({ id: 'lead-far', lat: 39.506, lead: true, reliability: 'directory-map-free', decision: decision('verify') }),
      mk({ id: 'check-far', lat: 39.533, lead: false, available: 0, decision: decision('check') }),
      mk({ id: 'lead-near', lat: 39.472, lead: true, reliability: 'official-free', decision: decision('verify') }),
      mk({ id: 'go-far', lat: 39.515, lead: false, available: 1, decision: decision('go'), verification: { live_verified: true } }),
      mk({ id: 'check-near', lat: 39.479, lead: false, available: 0, decision: decision('check') }),
    ]

    expect(sortNearbyBySafetyAndDistance(rows, { lat: 39.47, lon: -0.37 }).map((row) => row.id)).toEqual([
      'go-far',
      'check-near',
      'check-far',
      'lead-near',
      'lead-far',
    ])
    expect(rows.map((row) => row.id)).toEqual(['lead-far', 'check-far', 'lead-near', 'go-far', 'check-near'])
  })

  it('puts paid and unpriced chargers after farther free/cheap backups', () => {
    const rows = [
      mk({ id: 'paid-near', lat: 39.471, lead: true, reliability: 'directory-page', price_min: 0.45, decision: decision('lead') }),
      mk({ id: 'free-far', lat: 39.52, lead: true, reliability: 'official-free', price_min: 0, decision: decision('verify') }),
      mk({ id: 'unknown-near', lat: 39.472, lead: true, reliability: 'official-charger-location-price-unknown', decision: decision('lead') }),
    ]
    expect(sortNearbyBySafetyAndDistance(rows, { lat: 39.47, lon: -0.37 }).map((row) => row.id)).toEqual([
      'free-far',
      'paid-near',
      'unknown-near',
    ])
  })

  it('preserves API order before a home/location origin exists', () => {
    const rows = [
      mk({ id: 'far', distance_km: 8 }),
      mk({ id: 'near', distance_km: 0.1 }),
    ]
    expect(sortNearbyBySafetyAndDistance(rows, null).map((row) => row.id)).toEqual(['far', 'near'])
  })

  it('computes preview distance and ETA when instant rows do not have backend distance fields yet', () => {
    const rows = [
      mk({ id: 'far', lat: 39.57, lon: -0.37, lead: true, reliability: 'official-free', decision: decision('verify') }),
      mk({ id: 'near', lat: 39.471, lon: -0.37, lead: true, reliability: 'official-free', decision: decision('verify') }),
    ]

    const sorted = sortNearbyBySafetyAndDistance(rows, { lat: 39.47, lon: -0.37 })
    expect(sorted.map((row) => row.id)).toEqual(['near', 'far'])
    expect(sorted[0].distance_km).toBeLessThan(0.2)
    expect(sorted[0].drive_eta_min_est).toBe(2)
    expect(rows[0].distance_km).toBeUndefined()
  })
})

describe('availability (honesty-critical)', () => {
  it('live + free socket reads available (go)', () => {
    const a = availability(mk({ lead: false, connector_summary: { available: 2, total: 2 } }))
    expect(a.tone).toBe('go')
    expect(a.label).toBe('2/2 available')
  })
  it('live + none free reads busy, never available', () => {
    const a = availability(mk({ lead: false, connector_summary: { available: 0, total: 2, out_of_service: 0 } }))
    expect(a.tone).toBe('check')
    expect(a.available).toBe(0)
  })
  it('all out-of-service reads danger', () => {
    const a = availability(mk({ lead: false, connector_summary: { available: 0, total: 2, out_of_service: 2 } }))
    expect(a.tone).toBe('danger')
  })
  it('leads are never shown as live', () => {
    const a = availability(mk({ lead: true, total_evse: 2 }))
    expect(a.live).toBe(false)
    expect(a.label).toBe('Not live')
  })
  it('clamps available > total from noisy upstreams', () => {
    const a = availability(mk({ lead: false, connector_summary: { available: 2, total: 1 } }))
    expect(a.total).toBe(2)
    expect(a.label).toBe('2/2 available')
  })
  it('exposes free / in-use / out counts for the port breakdown', () => {
    const a = availability(
      mk({ lead: false, connector_summary: { available: 1, busy: 2, charging: 1, out_of_service: 1, total: 4 } }),
    )
    expect(a.available).toBe(1)
    expect(a.busy).toBe(2)
    expect(a.out).toBe(1)
    expect(a.total).toBe(4)
  })
  it('leads expose no live occupancy counts', () => {
    const a = availability(mk({ lead: true, total_evse: 2 }))
    expect(a.live).toBe(false)
    expect(a.busy).toBeNull()
  })
})

describe('passesFilters', () => {
  const freeLive = mk({ is_free: true, is_cheap: true, tesla_ok: true, lead: false })
  it('a free live station passes the defaults', () => {
    expect(passesFilters(freeLive, DEFAULT_FILTERS, '')).toBe(true)
  })
  it('off-cap station is visible until a cheap/free chip is on', () => {
    const pricey = mk({ is_free: false, is_cheap: false, tesla_ok: true, lead: false })
    expect(passesFilters(pricey, DEFAULT_FILTERS, '')).toBe(true)
    expect(passesFilters(pricey, filters({ maxPrice: 0.25 }), '')).toBe(false)
  })
  it('Tesla filter drops non-Tesla connectors only when Tesla is on', () => {
    const notTesla = mk({ is_free: true, is_cheap: true, tesla_ok: false, lead: false })
    expect(passesFilters(notTesla, DEFAULT_FILTERS, '')).toBe(true)
    expect(passesFilters(notTesla, filters({ tesla: true }), '')).toBe(false)
  })
  it('leads hidden when the Leads filter is off', () => {
    const lead = mk({ is_free: true, is_cheap: true, tesla_ok: true, lead: true, reliability: 'official-free' })
    expect(passesFilters(lead, filters({ leads: true }), '')).toBe(true)
    expect(passesFilters(lead, filters({ leads: false }), '')).toBe(false)
  })
  it('Open now excludes every lead (fail-closed) until availability is cleared', () => {
    const lead = mk({ is_free: true, is_cheap: true, tesla_ok: true, lead: true, reliability: 'official-free' })
    expect(passesFilters(lead, filters({ available: true, leads: true }), '')).toBe(false)
    // The empty-state "Show all leads" recovery must clear `available` too (D-081) —
    // leads may only render once Open now is off.
    expect(passesFilters(lead, filters({ available: false, verified: false, parking: false }), '')).toBe(true)
  })
  it('Open now excludes an available live row that is still check-first', () => {
    const checkFirst = mk({
      is_free: true,
      is_cheap: true,
      tesla_ok: true,
      lead: false,
      available: 2,
      decision: { level: 'check', label: 'Check first', reason: 'Status needs confirmation.' },
    })
    expect(passesFilters(checkFirst, filters({ available: true }), '')).toBe(false)
  })
  it('Open now accepts only route-ready rows across backend availability shapes', () => {
    const base = {
      verification: { live_verified: true },
      is_free: true,
      is_cheap: true,
      tesla_ok: true,
    }
    const openNow = filters({ available: true })

    expect(
      passesFilters(
        mk({
          ...base,
          lead: false,
          connector_summary: { available: 2, total: 2 },
          decision: { level: 'go', label: 'Go now', reason: 'Live and available.' },
        }),
        openNow,
        '',
      ),
    ).toBe(true)
    expect(
      passesFilters(
        mk({
          ...base,
          lead: false,
          available: 3,
          decision: { level: 'verify', label: 'Verify first', reason: 'Cached status is stale.' },
        }),
        openNow,
        '',
      ),
    ).toBe(false)
    expect(
      passesFilters(
        mk({
          ...base,
          lead: false,
          available: 0,
          decision: { level: 'go', label: 'Go now', reason: 'No connector is free.' },
        }),
        openNow,
        '',
      ),
    ).toBe(false)
    expect(
      passesFilters(
        mk({
          ...base,
          lead: true,
          available: 3,
          decision: { level: 'go', label: 'Go now', reason: 'A lead must never route.' },
        }),
        openNow,
        '',
      ),
    ).toBe(false)
  })
  it('broadening price drops the gate entirely; a free-only gate would narrow instead', () => {
    const paidCheap = mk({ is_free: false, is_cheap: true, tesla_ok: true, lead: false })
    const offCap = mk({ is_free: false, is_cheap: false, tesla_ok: true, lead: false })
    // A free-only cap excludes paid rows. Clearing the cap must restore them.
    expect(passesFilters(paidCheap, filters({ maxPrice: 0 }), '')).toBe(false)
    // "Broaden price" clears the numeric cap and restores unknown/paid tariffs.
    expect(passesFilters(paidCheap, filters({ maxPrice: null }), '')).toBe(true)
    expect(passesFilters(offCap, filters({ maxPrice: null }), '')).toBe(true)
  })
  it('search matches name/city', () => {
    const st = mk({ is_free: true, is_cheap: true, tesla_ok: true, lead: false, name: 'Gaia Alboraia' })
    expect(passesFilters(st, DEFAULT_FILTERS, 'gaia')).toBe(true)
    expect(passesFilters(st, DEFAULT_FILTERS, 'lidl')).toBe(false)
  })
  it('price chips OFF surface price-unknown official leads (the lead-crash repro path)', () => {
    const priceLead = mk({
      lead: true,
      is_free: false,
      is_cheap: false,
      tesla_ok: true,
      reliability: 'official-charger-location-price-unknown',
      decision: { level: 'lead', label: 'Price lead', reason: '' },
    })
    expect(passesFilters(priceLead, DEFAULT_FILTERS, '')).toBe(true)
    expect(passesFilters(priceLead, filters({ maxPrice: 0.25 }), '')).toBe(false)
  })
})

describe('numeric charging price filter', () => {
  it.each([0.18, 0.4, 0.65])('uses the chosen %s cap instead of a source cheap flag', (cap) => {
    const below = mk({ price_min: cap - 0.01, is_cheap: false })
    const boundary = mk({ price_min: cap, is_cheap: false })
    const above = mk({ price_min: cap + 0.01, is_cheap: true })
    expect(passesFilters(below, filters({ maxPrice: cap }), '')).toBe(true)
    expect(passesFilters(boundary, filters({ maxPrice: cap }), '')).toBe(true)
    expect(passesFilters(above, filters({ maxPrice: cap }), '')).toBe(false)
  })

  it.each([null, undefined, NaN, Infinity, -0.1])('rejects an unknown or invalid tariff %s under a chosen cap', (price_min) => {
    const station = mk({ price_min, is_cheap: true })
    expect(passesFilters(station, filters({ maxPrice: 0.4 }), '')).toBe(false)
    expect(passesFilters(station, DEFAULT_FILTERS, '')).toBe(true)
  })

  it('keeps explicit reported-free evidence but rejects contradictory paid tariffs at zero', () => {
    expect(passesFilters(mk({ is_free: true }), filters({ maxPrice: 0 }), '')).toBe(true)
    expect(passesFilters(mk({ is_free: true, price_min: 0.4 }), filters({ maxPrice: 0 }), '')).toBe(false)
    expect(passesFilters(mk({ price_min: 0 }), filters({ maxPrice: 0 }), '')).toBe(true)
  })

  it('changes neither live authority nor parking facts when increasing the cap', () => {
    const station = mk({ price_min: 0.4, lead: true, decision: { level: 'check', label: 'Verify first', reason: 'No live evidence' } })
    const before = JSON.stringify(station)
    expect(passesFilters(station, filters({ maxPrice: 0.65 }), '')).toBe(true)
    expect(passesFilters(station, filters({ maxPrice: 0.65, available: true }), '')).toBe(false)
    expect(JSON.stringify(station)).toBe(before)
  })
})

describe('inViewport', () => {
  const bbox = { south: 39.3, west: -0.55, north: 39.6, east: -0.2 }
  it('null bbox includes everything', () => {
    expect(inViewport(mk({ lat: 0, lon: 0 }), null)).toBe(true)
  })
  it('keeps points inside, drops points outside', () => {
    expect(inViewport(mk({ lat: 39.47, lon: -0.37 }), bbox)).toBe(true)
    expect(inViewport(mk({ lat: 40.42, lon: -3.7 }), bbox)).toBe(false)
  })
})

describe('corroboration (reliability UI)', () => {
  it('reads the backend block', () => {
    const c = corroboration(mk({ corroboration: { independent_sources: 3, tier: 'strongly_corroborated', families: ['mapareve', 'openchargemap', 'gaia'] } }))
    expect(c.count).toBe(3)
    expect(c.tier).toBe('strongly_corroborated')
  })
  it('falls back to source_count and derives a tier', () => {
    expect(corroboration(mk({ source_count: 2 })).tier).toBe('corroborated')
    expect(corroboration(mk({})).tier).toBe('single')
  })
})

describe('cityLabel', () => {
  it('title-cases ALL-CAPS source values, leaves normal ones', () => {
    expect(cityLabel('BARCELONA')).toBe('Barcelona')
    expect(cityLabel('Madrid')).toBe('Madrid')
    expect(cityLabel('')).toBe('')
  })
})

describe('chooseChargersData (intelligence-first render)', () => {
  const bbox = { south: 39.3, west: -0.55, north: 39.6, east: -0.2 }
  const live = { stations: [mk({ id: 'live1', lat: 39.47, lon: -0.37 })], leads: [] }
  const liveElsewhere = { stations: [mk({ id: 'far', lat: 43.26, lon: -2.93 })], leads: [] }
  const instant = { stations: [], leads: [mk({ id: 'lead1', lat: 39.5, lon: -0.4, lead: true })] }

  it('cold load in live mode paints instant leads while fetching', () => {
    const c = chooseChargersData({ overview: false, live: undefined, instant, isFetching: true, isPlaceholder: false, viewportBbox: bbox })
    expect(c.usingInstant).toBe(true)
    expect(c.data).toBe(instant)
  })
  it('placeholder rows all out of viewport -> instant leads during the fetch', () => {
    const c = chooseChargersData({ overview: false, live: liveElsewhere, instant, isFetching: true, isPlaceholder: true, viewportBbox: bbox })
    expect(c.usingInstant).toBe(true)
  })
  it('real live data for the area always wins', () => {
    const c = chooseChargersData({ overview: false, live, instant, isFetching: false, isPlaceholder: false, viewportBbox: bbox })
    expect(c.usingInstant).toBe(false)
    expect(c.data).toBe(live)
  })
  it('placeholder rows still in viewport are kept (no downgrade to leads)', () => {
    const c = chooseChargersData({ overview: false, live, instant, isFetching: true, isPlaceholder: true, viewportBbox: bbox })
    expect(c.usingInstant).toBe(false)
    expect(c.data).toBe(live)
  })
  it('cold overview does not claim a live check from the instant layer', () => {
    const c = chooseChargersData({ overview: true, live: undefined, instant, isFetching: true, isPlaceholder: false, viewportBbox: null })
    expect(c.usingInstant).toBe(false)
  })
  it('returning to overview cannot borrow live authority from placeholder rows', () => {
    const c = chooseChargersData({ overview: true, live, instant, isFetching: true, isPlaceholder: true, viewportBbox: bbox })
    expect(c.data).toBe(instant)
    expect(c.data?.stations).toEqual([])
    expect(c.usingInstant).toBe(false)
  })
  it('cold overview keeps the cached national inventory when the region request is offline', () => {
    const cached = { ...instant, cache: { stale: true }, meta: { offline: true } }
    const c = chooseChargersData({ overview: true, live: undefined, instant: cached, isFetching: false, isPlaceholder: false, isError: true, viewportBbox: bbox })
    expect(c.data).toBe(cached)
    expect(c.data?.cache?.stale).toBe(true)
    expect(c.data?.stations).toEqual([])
    expect(c.usingInstant).toBe(false)
    expect(c.liveFailed).toBe(false)
  })
  it('overview errors fall back without borrowing the previous query authority', () => {
    const c = chooseChargersData({ overview: true, live, instant, isFetching: false, isPlaceholder: false, isError: true, viewportBbox: bbox })
    expect(c.data).toBe(instant)
    expect(c.data?.stations).toEqual([])
  })
  it('a successful regional overview wins over the national fallback', () => {
    const regional = { stations: [], leads: [mk({ id: 'regional-only', lead: true })] }
    const c = chooseChargersData({ overview: true, live: regional, instant, isFetching: false, isPlaceholder: false, isError: false, viewportBbox: bbox })
    expect(c.data).toBe(regional)
  })
  it('returning to overview without inventory fails closed during the fetch', () => {
    const c = chooseChargersData({ overview: true, live, isFetching: true, isPlaceholder: true, viewportBbox: bbox })
    expect(c.data).toBeUndefined()
  })
  it('FAIL-CLOSED: live fetch error drops stale live rows and falls back to honest leads', () => {
    const c = chooseChargersData({ overview: false, live, instant, isFetching: false, isPlaceholder: false, isError: true, viewportBbox: bbox })
    expect(c.liveFailed).toBe(true)
    expect(c.data).toBe(instant) // leads-only, labeled "Not live" — never the stale live cards
  })
  it('an error during an active retry does not flap to the failure state', () => {
    const c = chooseChargersData({ overview: false, live, instant, isFetching: true, isPlaceholder: false, isError: true, viewportBbox: bbox })
    expect(c.liveFailed).toBe(false)
  })
})

describe('tariffWindow (time-of-use honesty)', () => {
  it('flat always-on price shows NO window chip', () => {
    const st = mk({ pricing: [{ price: 0.2, label: '€0.20/kWh' }] })
    expect(tariffWindow(st)).toBeNull()
  })
  it('time-bound window surfaces the backend label', () => {
    const st = mk({
      pricing: [{ price: 0, valid_from: '22:00', valid_until: '08:00', label: 'Free 22:00–08:00' }],
      price_window_status: { label: 'Now Free 22:00–08:00', is_currently_within_standard: true },
    })
    const w = tariffWindow(st)
    expect(w?.label).toBe('Now Free 22:00–08:00')
    expect(w?.currentlyCheap).toBe(true)
  })
  it('cheap-later window is flagged NOT currently cheap', () => {
    const st = mk({
      pricing: [{ price: 0.2, valid_from: '22:00', valid_until: '08:00', label: '€0.20 22:00–08:00' }],
      price_window_status: { label: 'Cheap window €0.20 22:00–08:00', is_currently_within_standard: false },
    })
    const w = tariffWindow(st)
    expect(w?.currentlyCheap).toBe(false)
  })
})

// An absent connector list is unknown; it must not contradict source-reported sockets.
describe('connector inventory fallback', () => {
  it('uses a source-reported connector count when the connector list is empty', () => {
    expect(availability(mk({ lead: true, connectors: [], connector_count: 2 })).total).toBe(2)
  })
  it('does not invent zero ports from an empty connector list', () => {
    expect(availability(mk({ lead: true, connectors: [] })).total).toBeNull()
  })
  it('preserves an explicitly reported zero', () => {
    expect(availability(mk({ lead: true, connectors: [], total_evse: 0 })).total).toBe(0)
  })
})
