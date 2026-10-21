import { useCallback, useEffect, useRef, useState } from 'react'
import type { Coords } from '../types'
import { validCoordinates } from '../lib/geo'

type GeoStatus = 'idle' | 'locating' | 'granted' | 'denied' | 'unavailable'

export function useGeolocation() {
  const [coords, setCoords] = useState<Coords | null>(null)
  const [status, setStatus] = useState<GeoStatus>('idle')
  const requestId = useRef(0)

  // getCurrentPosition cannot be aborted. Invalidate callbacks when a newer
  // request or explicit search-area choice takes ownership instead.
  const cancel = useCallback(() => {
    requestId.current += 1
    setStatus(current => current === 'locating' ? 'idle' : current)
  }, [])

  useEffect(() => () => { requestId.current += 1 }, [])

  const request = useCallback((onLocated?: (coords: Coords) => void) => {
    const id = ++requestId.current
    if (!('geolocation' in navigator)) {
      setStatus('unavailable')
      return
    }
    setStatus('locating')
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (id !== requestId.current) return
        const nextCoords = { lat: pos.coords.latitude, lon: pos.coords.longitude }
        if (!validCoordinates(nextCoords)) {
          setStatus('unavailable')
          return
        }
        setCoords(nextCoords)
        setStatus('granted')
        onLocated?.(nextCoords)
      },
      (error) => {
        if (id === requestId.current) setStatus(error.code === 1 ? 'denied' : 'unavailable')
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    )
  }, [])

  return { coords, status, request, cancel }
}
