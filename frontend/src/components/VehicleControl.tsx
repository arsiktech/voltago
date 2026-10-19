import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence } from 'framer-motion'
import { CarFront } from 'lucide-react'
import { useVehicleGarage } from '../hooks/useVehicleGarage'
import { vehicleIsStale } from '../lib/vehicles'

const VehiclePanel = lazy(() => import('./VehiclePanel'))

class VehicleBoundary extends Component<{ children: ReactNode; onClose: () => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? <aside id="vehicle-panel" className="vehicle-panel" role="dialog" aria-label="Your EV">
      <header className="ev-panel-header"><h2>Your EV</h2><button className="ev-button" onClick={this.props.onClose}>Close</button></header>
      <div className="ev-panel-content"><p className="ev-notice" role="status">Your EV could not load. Reload the page to try again.</p>
        <button className="ev-button" onClick={() => window.location.reload()}>Reload</button></div>
    </aside> : this.props.children
  }
}

function readCallback(): string | null {
  const result = new URL(window.location.href).searchParams.get('vehicle_connection')
  return result === 'connected' || result === 'error' ? result : null
}

export function VehicleControl() {
  const garage = useVehicleGarage()
  const [callbackResult, setCallbackResult] = useState(readCallback)
  const [open, setOpen] = useState(Boolean(callbackResult))
  const opener = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const url = new URL(window.location.href)
    const result = url.searchParams.get('vehicle_connection')
    if (!['connected', 'error'].includes(result || '')) return
    url.searchParams.delete('vehicle_connection')
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
  }, [])
  const close = () => { setOpen(false); setCallbackResult(null); opener.current?.focus() }
  const vehicle = garage.vehicle
  const stale = vehicle && (vehicleIsStale(vehicle, garage.now) || (garage.readingsUnavailable && vehicle.source.mode === 'live'))
  const summary = vehicle?.source.mode === 'preview' ? 'Example' : stale ? 'Update'
    : vehicle?.batteryPercent === null || !vehicle ? null : `${Math.round(vehicle.batteryPercent)}%`
  return <>
    <button ref={opener} type="button" className="ev-header-control" aria-expanded={open}
      aria-controls="vehicle-panel" aria-haspopup="dialog" onClick={() => setOpen((value) => !value)}>
      <CarFront size={18} aria-hidden="true" /><span>Your EV</span>
      {summary && <span className="ev-header-percent" title={vehicle?.source.mode === 'manual' ? 'Manual battery reading' : undefined}>{summary}</span>}
    </button>
    <AnimatePresence>
      {open && <VehicleBoundary onClose={close}><Suspense fallback={<aside id="vehicle-panel" className="vehicle-panel" role="dialog" aria-label="Your EV">
        <div className="ev-panel-header"><h2>Your EV</h2><button className="ev-button" onClick={close}>Close</button></div>
        <p role="status" className="ev-muted">Loading your EV…</p>
      </aside>}><VehiclePanel onClose={close} callbackError={callbackResult === 'error'} /></Suspense></VehicleBoundary>}
    </AnimatePresence>
  </>
}
