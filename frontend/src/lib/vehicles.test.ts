import { describe, expect, it, vi, afterEach } from 'vitest'
import type { Station } from '../types'
import {
  chargingEstimate, energyToTarget, exampleVehicle, readManualVehicle, readSpecs,
  vehicleIsStale, vehicleRequest, withVehicleSpecs,
} from './vehicles'

const now = Date.parse('2026-10-19T19:00:00Z')
const car = exampleVehicle(new Date(now).toISOString())
const station: Station = {
  id: 'charger', name: 'Charger', lat: 40, lon: -3, price_min: 0.2,
  verification: { price_verified: true },
  connectors: [{ type: 'CCS2', power_kw: 250 }, { type: 'Type 2', power_kw: 22 }],
}

describe('owner car readings', () => {
  it('keeps zero charge and zero range rather than treating them as unknown', () => {
    const manual = { ...car, id: 'manual', batteryPercent: 0, rangeKm: 0, source: { provider: 'You', mode: 'manual' } }
    expect(readManualVehicle(JSON.stringify(manual))?.batteryPercent).toBe(0)
    expect(energyToTarget({ ...car, batteryPercent: 0 }, 80)).toBe(60)
  })
  it('does not infer capacity, fabricate missing readings, or charge past a reached target', () => {
    expect(energyToTarget({ ...car, batteryCapacityKwh: null }, 80)).toBeNull()
    expect(energyToTarget({ ...car, batteryPercent: null }, 80)).toBeNull()
    expect(energyToTarget(car, 60)).toBe(0)
    expect(energyToTarget(car, 100)).toBe(28.5)
  })
  it('rejects corrupt or obsolete local readings and out-of-range specifications', () => {
    expect(readManualVehicle('{')).toBeNull()
    expect(readManualVehicle(JSON.stringify(car))).toBeNull()
    expect(readManualVehicle(JSON.stringify({ ...car, id: 'manual', source: { mode: 'manual' }, batteryPercent: 101 }))).toBeNull()
    expect(readSpecs(JSON.stringify({ car: { batteryCapacityKwh: -1, maxAcKw: 11, maxDcKw: 100, connectors: [] } }))).toEqual({})
  })
  it('never turns locally supplied capacity into provider-observed available energy', () => {
    const result = withVehicleSpecs({ ...car, batteryCapacityKwh: null, usableEnergyKwh: null }, {
      batteryCapacityKwh: 75, maxAcKw: 11, maxDcKw: 100, connectors: ['CCS2'],
    })
    expect(result.batteryCapacityKwh).toBe(75)
    expect(result.usableEnergyKwh).toBeNull()
  })
  it('expires provider readings by observation deadline, including after fetch errors', () => {
    const live = { ...car, source: { provider: 'Smartcar', mode: 'live' as const }, freshUntil: new Date(now + 1000).toISOString() }
    expect(vehicleIsStale(live, now)).toBe(false)
    expect(vehicleIsStale(live, now + 1000)).toBe(true)
    expect(vehicleIsStale({ ...live, error: 'unavailable' }, now)).toBe(true)
    expect(vehicleIsStale({ ...live, freshUntil: null }, now)).toBe(true)
  })
  it('does not combine fresh state of charge with an expired provider capacity observation', () => {
    const live = { ...car, source: { provider: 'Smartcar', mode: 'live' as const },
      fieldFreshUntil: { batteryCapacityKwh: new Date(now - 1).toISOString() } }
    expect(energyToTarget(live, 80, now)).toBeNull()
    const ownerSpecified = withVehicleSpecs({ ...live, batteryCapacityKwh: null }, {
      batteryCapacityKwh: 75, maxAcKw: 11, maxDcKw: 170, connectors: ['CCS2'],
    })
    expect(energyToTarget(ownerSpecified, 80, now)).toBe(13.5)
  })
})

describe('station charging plans', () => {
  it('uses the matching socket and car limit instead of the station’s largest rating', () => {
    const plan = chargingEstimate(car, 80, station, now)
    expect(plan.energyKwh).toBe(13.5)
    expect(plan.gridEnergyKwh).toBe(15)
    expect(plan.cost).toBe(3)
    expect(plan.minimumMinutes).toBe(5)
    expect(chargingEstimate({ ...car, connectors: ['Type 2'] }, 80, station, now).minimumMinutes).toBe(74)
    expect(chargingEstimate(car, 80, { ...station, connectors: [{ type: 'Type 2', power_kw: 11 }, { type: 'CHAdeMO', power_kw: 350 }] }, now).minimumMinutes).toBe(74)
  })
  it('leaves unknown compatibility, car limits, and aggregate-only power uncertain', () => {
    expect(chargingEstimate({ ...car, connectors: null }, 80, station, now).minimumMinutes).toBeNull()
    expect(chargingEstimate({ ...car, maxAcKw: null, maxDcKw: null }, 80, station, now).minimumMinutes).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, connectors: [], connector_labels: ['CCS2'], max_power_kw: 350 }, now).minimumMinutes).toBeNull()
    expect(chargingEstimate({ ...car, connectors: ['NACS'] }, 80, { ...station, connectors: [{ type: 'NACS', power_kw: 22 }] }, now).minimumMinutes).toBeNull()
  })
  it('does not recommend an incompatible port or claim an ambiguous type is incompatible', () => {
    const incompatible = chargingEstimate(car, 80, { ...station, connectors: [{ type: 'CHAdeMO', power_kw: 100 }] }, now)
    expect(incompatible.compatibility).toBe('incompatible')
    expect(incompatible.minimumMinutes).toBeNull()
    expect(incompatible.cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, connectors: [{ type: 'CHAdeMO' }, { type: 'Unknown connector' }] }, now).compatibility).toBe('unknown')
  })
  it('keeps unverified, stale, non-euro and time-window prices unknown', () => {
    expect(chargingEstimate(car, 80, { ...station, verification: {} }, now).cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, cache_stale: true }, now).cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, price_currency: 'USD' }, now).cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, price_native_unit: 'minute' }, now).cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, price_min: undefined, is_free: true, verification: {} }, now).cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, pricing: [{ currency: 'USD', price: 0.2, is_currently_valid: true }] }, now).cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, pricing: [{ currency: 'EUR', price: 0.2, is_currently_valid: false }] }, now).cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, pricing: [{ currency: 'EUR', unit: 'minute', price: 0.2, is_currently_valid: true }] }, now).cost).toBeNull()
    expect(chargingEstimate(car, 80, { ...station, pricing: [
      { currency: 'USD', price: 0.1, is_currently_valid: true },
      { currency: 'EUR', price: 0.45, is_currently_valid: true },
    ] }, now).cost).toBe(6.75)
  })
  it('withholds time and cost based on stale vehicle readings', () => {
    const live = { ...car, source: { provider: 'Smartcar', mode: 'live' as const }, freshUntil: new Date(now - 1).toISOString() }
    expect(chargingEstimate(live, 80, station, now)).toMatchObject({ stale: true, minimumMinutes: null, cost: null })
  })
})

describe('private vehicle transport', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('uses same-origin credentials and no-store for readings and mutations', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ vehicles: [] }) })
    vi.stubGlobal('fetch', fetcher)
    await vehicleRequest('')
    await vehicleRequest('/refresh', {})
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store' })
    expect(fetcher.mock.calls[1][1]).toMatchObject({ method: 'POST', credentials: 'same-origin', cache: 'no-store', body: '{}' })
  })
})
