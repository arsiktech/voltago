import type { Station } from '../types'

export type VehicleMode = 'live' | 'simulation' | 'manual' | 'preview'
export type VehicleConnector = 'CCS2' | 'Type 2' | 'CHAdeMO' | 'NACS' | 'CCS1' | 'Type 1'

export interface Vehicle {
  id: string
  make: string | null
  model: string | null
  year: number | null
  batteryPercent: number | null
  rangeKm: number | null
  chargingState: string | null
  chargePowerKw: number | null
  batteryCapacityKwh: number | null
  usableEnergyKwh: number | null
  maxAcKw: number | null
  maxDcKw: number | null
  connectors: string[] | null
  source: { provider: string; mode: VehicleMode }
  observedAt: string | null
  fetchedAt?: string | null
  freshUntil?: string | null
  fieldFreshUntil?: Record<string, string | null>
  capacityIsManual?: boolean
  isStale?: boolean
  error?: string | null
}

export interface VehicleConnection {
  provider: string
  configured: boolean
  connected: boolean
  mode: 'live' | 'simulation' | null
  connectionStatus: 'unavailable' | 'disconnected' | 'connected' | 'reconnect'
  reason: string | null
  refreshIntervalSeconds: number
  vehicles: Vehicle[]
}

export interface VehicleSpecs {
  batteryCapacityKwh: number | null
  maxAcKw: number | null
  maxDcKw: number | null
  connectors: VehicleConnector[]
}

export const VEHICLE_CONNECTORS: VehicleConnector[] = ['CCS2', 'Type 2', 'CHAdeMO', 'NACS', 'CCS1', 'Type 1']
export const MANUAL_VEHICLE_KEY = 'volta.vehicle.manual.v1'
export const VEHICLE_SPECS_KEY = 'volta.vehicle.specs.v1'
export const VEHICLE_TARGET_KEY = 'volta.vehicle.target.v1'

export function vehicleName(vehicle: Vehicle): string {
  return [vehicle.make, vehicle.model].filter(Boolean).join(' ') || 'My EV'
}

export function finiteIn(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max
}

export function readManualVehicle(raw: string | null): Vehicle | null {
  try {
    const value = JSON.parse(raw || 'null') as Vehicle | null
    if (!value || value.id !== 'manual' || value.source?.mode !== 'manual'
      || !finiteIn(value.batteryPercent, 0, 100)
      || (value.batteryCapacityKwh !== null && !finiteIn(value.batteryCapacityKwh, 1, 300))
      || (value.rangeKm !== null && !finiteIn(value.rangeKm, 0, 1500))
      || (value.maxAcKw !== null && !finiteIn(value.maxAcKw, 0.1, 100))
      || (value.maxDcKw !== null && !finiteIn(value.maxDcKw, 0.1, 1000))
      || typeof value.model !== 'string' || value.model.length > 80
      || (value.make !== null && typeof value.make !== 'string')
      || !Array.isArray(value.connectors) || !value.connectors.every((item) => VEHICLE_CONNECTORS.includes(item as VehicleConnector))
      || !value.observedAt || !Number.isFinite(Date.parse(value.observedAt))) return null
    return { ...value, usableEnergyKwh: value.batteryCapacityKwh === null ? null : value.batteryCapacityKwh * value.batteryPercent / 100 }
  } catch { return null }
}

export function readSpecs(raw: string | null): Record<string, VehicleSpecs> {
  try {
    const data = JSON.parse(raw || '{}') as Record<string, VehicleSpecs>
    if (!data || typeof data !== 'object' || Array.isArray(data)) return {}
    return Object.fromEntries(Object.entries(data).filter(([id, spec]) => id.length <= 100 && spec
      && (spec.batteryCapacityKwh === null || finiteIn(spec.batteryCapacityKwh, 1, 300))
      && (spec.maxAcKw === null || finiteIn(spec.maxAcKw, 0.1, 100))
      && (spec.maxDcKw === null || finiteIn(spec.maxDcKw, 0.1, 1000))
      && Array.isArray(spec.connectors) && spec.connectors.every((item) => VEHICLE_CONNECTORS.includes(item))))
  } catch { return {} }
}

export function withVehicleSpecs(vehicle: Vehicle, specs?: VehicleSpecs): Vehicle {
  if (!specs) return vehicle
  return {
    ...vehicle,
    // Owner-entered capacity never upgrades a provider's energy observation.
    batteryCapacityKwh: vehicle.batteryCapacityKwh ?? specs.batteryCapacityKwh,
    capacityIsManual: vehicle.batteryCapacityKwh === null && specs.batteryCapacityKwh !== null,
    maxAcKw: vehicle.maxAcKw ?? specs.maxAcKw,
    maxDcKw: vehicle.maxDcKw ?? specs.maxDcKw,
    connectors: vehicle.connectors?.length ? vehicle.connectors : specs.connectors,
  }
}

export function vehicleIsStale(vehicle: Vehicle, now = Date.now()): boolean {
  if (vehicle.source.mode === 'manual' || vehicle.source.mode === 'preview') return false
  const until = Date.parse(vehicle.freshUntil || '')
  return Boolean(vehicle.isStale || vehicle.error || !Number.isFinite(until) || until <= now)
}

export function energyToTarget(vehicle: Vehicle, target: number, now = Date.now()): number | null {
  if ((vehicle.source.mode === 'live' || vehicle.source.mode === 'simulation') && vehicle.fieldFreshUntil && !vehicle.capacityIsManual) {
    const deadline = Date.parse(vehicle.fieldFreshUntil.batteryCapacityKwh || '')
    if (!Number.isFinite(deadline) || deadline <= now) return null
  }
  if (!finiteIn(vehicle.batteryPercent, 0, 100) || !finiteIn(vehicle.batteryCapacityKwh, 1, 300)
    || !finiteIn(target, 0, 100)) return null
  return vehicle.batteryCapacityKwh * Math.max(0, target - vehicle.batteryPercent) / 100
}

function normalizeConnector(label: string): VehicleConnector | null {
  const key = label.toLowerCase().replace(/[^a-z0-9]/g, '')
  if (['ccs2', 'ccscombo2', 'combo2', 'iec621963', 'iec62196t3combo'].includes(key)) return 'CCS2'
  if (['type2', 'mennekes', 'iec621962', 'iec62196t2'].includes(key)) return 'Type 2'
  if (['chademo'].includes(key)) return 'CHAdeMO'
  if (['nacs', 'saej3400'].includes(key)) return 'NACS'
  if (['ccs1', 'ccscombo1', 'combo1', 'iec62196t1combo'].includes(key)) return 'CCS1'
  if (['type1', 'j1772', 'saej1772', 'iec62196t1'].includes(key)) return 'Type 1'
  return null
}

export interface ChargingEstimate {
  energyKwh: number | null
  gridEnergyKwh: number | null
  minimumMinutes: number | null
  cost: number | null
  compatibility: 'compatible' | 'incompatible' | 'unknown'
  stale: boolean
}

/** A planning estimate, never a route-readiness or charger-availability decision. */
export function chargingEstimate(vehicle: Vehicle, target: number, station: Station, now = Date.now()): ChargingEstimate {
  const energyKwh = energyToTarget(vehicle, target, now)
  // Explicit planning assumption: 90% of energy at the plug reaches the battery.
  const gridEnergyKwh = energyKwh === null ? null : energyKwh / 0.9
  const owned = vehicle.connectors?.map(normalizeConnector).filter((item) => item !== null) || []
  const ports = station.connectors?.map((port) => ({
    type: normalizeConnector(port.type || port.label || ''), power: port.power_kw, fast: port.fast,
  })) || []
  const reportedTypes = [...ports.map((port) => port.type), ...(station.connector_labels || []).map(normalizeConnector)]
    .filter((type) => type !== null)
  const matches = reportedTypes.filter((type) => owned.includes(type))
  // An unrecognized connector can still be compatible. Do not claim incompatibility.
  const hasUnknownType = (station.connectors || []).some((port) => !normalizeConnector(port.type || port.label || ''))
    || (station.connector_labels || []).some((label) => !normalizeConnector(label))
  const compatibility = !owned.length || !reportedTypes.length ? 'unknown'
    : matches.length ? 'compatible' : hasUnknownType ? 'unknown' : 'incompatible'
  const usablePowers = ports.flatMap((port) => {
    if (!port.type || !owned.includes(port.type) || !finiteIn(port.power, 0.1, 1000)) return []
    if (port.type === 'NACS' && port.fast === undefined) return []
    const isAc = ['Type 2', 'Type 1'].includes(port.type) || (port.type === 'NACS' && port.fast === false)
    const limit = isAc ? vehicle.maxAcKw : vehicle.maxDcKw
    return finiteIn(limit, 0.1, 1000) ? [Math.min(port.power, limit)] : []
  })
  // Never combine an aggregate station rating with an unrelated compatible socket.
  const power = usablePowers.length ? Math.max(...usablePowers) : null
  const stale = vehicleIsStale(vehicle, now)
  const minimumMinutes = stale || compatibility !== 'compatible' || power === null || energyKwh === null
    ? null : Math.ceil(energyKwh / power * 60)
  const windows = station.price_window_status
  const currentWindows = windows?.current_windows
  const priceRows = currentWindows?.length ? currentWindows : station.pricing
  const prices = priceRows?.flatMap((item) => {
    if ((item.currency && item.currency !== 'EUR') || (item.unit && item.unit.toLowerCase() !== 'kwh')
      || (!currentWindows?.length && item.is_currently_valid !== true)) return []
    const value = item.price ?? (item.is_free ? 0 : null)
    return finiteIn(value, 0, 10) ? [value] : []
  }) || []
  const tariff = priceRows?.length ? (prices.length ? Math.min(...prices) : null)
    : windows ? windows.current_price_min : station.current_price_min ?? station.price_min
  const validTariff = !station.cache_stale && station.verification?.price_verified === true
    && (!windows || windows.is_currently_within_standard === true)
    && (!station.price_currency || station.price_currency.toUpperCase() === 'EUR')
    && (!station.price_native_unit || station.price_native_unit.toLowerCase() === 'kwh')
  const cost = !stale && compatibility !== 'incompatible' && validTariff && finiteIn(tariff, 0, 10) && gridEnergyKwh !== null
    ? gridEnergyKwh * tariff : null
  return { energyKwh, gridEnergyKwh, minimumMinutes, cost, compatibility, stale }
}

export function exampleVehicle(now = new Date().toISOString()): Vehicle {
  return {
    id: 'preview', make: null, model: 'Example EV', year: null,
    batteryPercent: 62, rangeKm: 278, chargingState: 'not_charging', chargePowerKw: null,
    batteryCapacityKwh: 75, usableEnergyKwh: 46.5, maxAcKw: 11, maxDcKw: 170,
    connectors: ['CCS2', 'Type 2'], source: { provider: 'Example', mode: 'preview' }, observedAt: now,
  }
}

/** Vehicle requests bypass all station/offline caches and never persist credentials. */
export async function vehicleRequest<T>(path: string, body?: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/vehicles${path}`, {
    method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
    headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000),
  })
  if (!response.ok) throw new Error('Your car could not be updated. Please try again.')
  return response.json() as Promise<T>
}
