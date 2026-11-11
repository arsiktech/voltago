import { useState, type FormEvent } from 'react'
import { ArrowLeft, Check } from 'lucide-react'
import { useVehicleGarage } from '../hooks/useVehicleGarage'
import { VEHICLE_CONNECTORS, type Vehicle, type VehicleConnector, type VehicleSpecs } from '../lib/vehicles'

export function VehicleEditor({ vehicle, onDone }: { vehicle: Vehicle | null; onDone: () => void }) {
  const garage = useVehicleGarage()
  const live = vehicle?.source.mode === 'live' || vehicle?.source.mode === 'simulation'
  const initial = vehicle?.source.mode === 'preview' ? null : vehicle
  const [connectors, setConnectors] = useState<string[]>(initial?.connectors || [])
  const [error, setError] = useState<string | null>(null)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const number = (key: string) => form.get(key)?.toString().trim() ? Number(form.get(key)) : null
    const capacity = number('capacity')
    const batteryPercent = number('battery')
    const spec: VehicleSpecs = {
      batteryCapacityKwh: capacity, maxAcKw: number('ac'), maxDcKw: number('dc'),
      connectors: connectors as VehicleConnector[],
    }
    if (!live && batteryPercent === null) { setError('Enter your current battery percentage.'); return }
    if (live && vehicle) garage.saveSpecs(vehicle.id, spec)
    else garage.saveManual({
      id: 'manual', make: null, model: String(form.get('name') || '').trim() || 'My EV', year: null,
      batteryPercent, rangeKm: number('range'), batteryCapacityKwh: capacity,
      usableEnergyKwh: capacity !== null && batteryPercent !== null ? capacity * batteryPercent / 100 : null,
      chargePowerKw: null, chargingState: null,
      maxAcKw: spec.maxAcKw, maxDcKw: spec.maxDcKw, connectors,
      source: { provider: 'You', mode: 'manual' }, observedAt: new Date().toISOString(),
    })
    onDone()
  }

  return <form className="vehicle-editor" onSubmit={submit}>
    <button type="button" className="ev-button ev-back" onClick={onDone}><ArrowLeft size={16} /> Back</button>
    <h3>{live ? 'Your car’s specifications' : 'Add your EV'}</h3>
    <p className="ev-muted">{live ? 'Add any details your car doesn’t share. These stay in this browser.' : 'Use the readings on your dashboard. You can update them any time.'}</p>
    {!live && <>
      <label>Car name<input name="name" maxLength={80} defaultValue={initial?.model || ''} placeholder="e.g. My Ioniq 5" autoComplete="off" /></label>
      <div className="ev-form-row">
        <label>Battery (%)<input name="battery" type="number" inputMode="decimal" required min="0" max="100" step="0.1" defaultValue={initial?.batteryPercent ?? ''} placeholder="62" /></label>
        <label>Range (km)<input name="range" type="number" inputMode="decimal" min="0" max="1500" step="1" defaultValue={initial?.rangeKm ?? ''} placeholder="Optional" /></label>
      </div>
    </>}
    <label>Usable battery capacity (kWh)<input name="capacity" type="number" inputMode="decimal" min="1" max="300" step="0.1" defaultValue={initial?.batteryCapacityKwh ?? ''} placeholder="Optional, e.g. 75" /></label>
    <p className="ev-field-help">Use the usable capacity in your car’s specifications. This lets us estimate energy and charging cost.</p>
    <div className="ev-form-row">
      <label>Max AC (kW)<input name="ac" type="number" inputMode="decimal" min="0.1" max="100" step="0.1" defaultValue={initial?.maxAcKw ?? ''} placeholder="Optional" /></label>
      <label>Max DC (kW)<input name="dc" type="number" inputMode="decimal" min="0.1" max="1000" step="0.1" defaultValue={initial?.maxDcKw ?? ''} placeholder="Optional" /></label>
    </div>
    <fieldset>
      <legend>Charging connectors</legend>
      <div className="ev-connectors">{VEHICLE_CONNECTORS.map((type) => <button key={type} type="button"
        aria-pressed={connectors.includes(type)} className="ev-button"
        onClick={() => setConnectors((current) => current.includes(type) ? current.filter((item) => item !== type) : [...current, type])}>
        <Check size={13} aria-hidden="true" style={{ visibility: connectors.includes(type) ? 'visible' : 'hidden' }} />{type}
      </button>)}</div>
    </fieldset>
    <p className="ev-field-help">Choose only connectors your car supports. We’ll leave charging time unknown until we know its power limits.</p>
    {error && <p role="alert" className="ev-notice">{error}</p>}
    <button type="submit" className="ev-button ev-primary">{live ? 'Save specifications' : 'Save readings'}</button>
    <p className="ev-field-help">Saved on this device. Manual readings never appear as live.</p>
  </form>
}
