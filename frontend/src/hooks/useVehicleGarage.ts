import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  exampleVehicle, MANUAL_VEHICLE_KEY, readManualVehicle, readSpecs, VEHICLE_SPECS_KEY,
  VEHICLE_TARGET_KEY, vehicleRequest, withVehicleSpecs,
  type Vehicle, type VehicleConnection, type VehicleSpecs,
} from '../lib/vehicles'

function saved(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}

function persist(key: string, value: unknown): boolean {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch { return false }
}

export function useGarageState() {
  const client = useQueryClient()
  const connection = useQuery({
    queryKey: ['vehicles'], queryFn: ({ signal }) => vehicleRequest<VehicleConnection>('', undefined, signal),
    retry: false, staleTime: 30000,
  })
  const [manual, setManual] = useState(() => readManualVehicle(saved(MANUAL_VEHICLE_KEY)))
  const [specs, setSpecs] = useState(() => readSpecs(saved(VEHICLE_SPECS_KEY)))
  const [preview, setPreview] = useState<Vehicle | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [target, setTargetState] = useState(() => {
    const value = Number(saved(VEHICLE_TARGET_KEY) || 80)
    return Number.isFinite(value) && value >= 50 && value <= 100 ? value : 80
  })
  const [now, setNow] = useState(Date.now)
  const [pending, setPending] = useState<'connect' | 'refresh' | 'disconnect' | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const refreshing = useRef(false)
  const vehicles = useMemo(() => [
    ...(connection.data?.vehicles || []).map((vehicle) => withVehicleSpecs(vehicle, specs[vehicle.id])),
    ...(manual ? [manual] : []), ...(preview ? [preview] : []),
  ], [connection.data?.vehicles, specs, manual, preview])
  const vehicle = vehicles.find((item) => item.id === selectedId) || vehicles[0] || null

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(timer)
  }, [])

  const refresh = useCallback(async () => {
    if (refreshing.current) return
    refreshing.current = true
    setPending('refresh')
    setActionError(null)
    try {
      const data = await vehicleRequest<VehicleConnection>('/refresh', {})
      client.setQueryData(['vehicles'], data)
      setRefreshFailed(false)
      setNow(Date.now())
    } catch { setRefreshFailed(true); setActionError('Your car could not be updated. Check your connection and try again.') }
    finally { refreshing.current = false; setPending(null) }
  }, [client])

  useEffect(() => {
    if (!connection.data?.connected) return
    const poll = () => { if (document.visibilityState === 'visible' && navigator.onLine) void refresh() }
    const timer = setInterval(poll, Math.max(60, connection.data.refreshIntervalSeconds || 60) * 1000)
    document.addEventListener('visibilitychange', poll)
    window.addEventListener('online', poll)
    poll()
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', poll)
      window.removeEventListener('online', poll)
    }
  }, [connection.data?.connected, connection.data?.refreshIntervalSeconds, refresh])

  const connect = async () => {
    setPending('connect')
    setActionError(null)
    try {
      const data = await vehicleRequest<{ authorization_url: string }>('/connect', {})
      const url = new URL(data.authorization_url)
      if (url.origin !== 'https://connect.smartcar.com' || url.pathname !== '/oauth/authorize') throw new Error()
      window.location.assign(url.href)
    } catch {
      setActionError('Live connection is unavailable right now. You can enter your battery manually.')
      setPending(null)
    }
  }

  const disconnect = async () => {
    setPending('disconnect')
    setActionError(null)
    try {
      const data = await vehicleRequest<VehicleConnection>('/disconnect', {})
      client.setQueryData(['vehicles'], data)
      setSelectedId(null)
    } catch { setActionError('Could not disconnect. Please try again.') }
    finally { setPending(null) }
  }

  const saveManual = (value: Vehicle) => {
    setManual(value)
    setPreview(null)
    setSelectedId('manual')
    const stored = persist(MANUAL_VEHICLE_KEY, value)
    setActionError(stored ? null : 'Your readings are available for this visit. Browser storage is unavailable.')
  }
  const saveSpecs = (id: string, value: VehicleSpecs) => {
    const next = { ...specs, [id]: value }
    setSpecs(next)
    const stored = persist(VEHICLE_SPECS_KEY, next)
    setActionError(stored ? null : 'Your car settings are available for this visit. Browser storage is unavailable.')
  }
  const setTarget = (value: number) => {
    setTargetState(value)
    persist(VEHICLE_TARGET_KEY, value)
  }
  const startPreview = () => { setPreview(exampleVehicle()); setSelectedId('preview'); setActionError(null) }
  const removeLocalVehicle = () => {
    if (vehicle?.source.mode === 'preview') setPreview(null)
    else {
      setManual(null)
      persist(MANUAL_VEHICLE_KEY, null)
      try { localStorage.removeItem('volta.vehicle.photo.manual') } catch { /* Memory removal still works. */ }
    }
    setSelectedId(null)
  }
  return {
    connection, vehicle, vehicles, target, setTarget, now, pending, actionError,
    clearError: () => setActionError(null), connect, disconnect, refresh, saveManual, saveSpecs,
    startPreview, removeLocalVehicle, selectVehicle: setSelectedId,
    readingsUnavailable: connection.isError || refreshFailed,
  }
}

type Garage = ReturnType<typeof useGarageState>
export const VehicleContext = createContext<Garage | null>(null)

export function useVehicleGarage() {
  const garage = useContext(VehicleContext)
  if (!garage) throw new Error('VehicleGarageProvider is required')
  return garage
}
