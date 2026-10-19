import { BatteryCharging, Plug, Timer, Wallet } from 'lucide-react'
import type { Station } from '../types'
import { useVehicleGarage } from '../hooks/useVehicleGarage'
import { chargingEstimate } from '../lib/vehicles'

export function VehicleEstimate({ st }: { st: Station }) {
  const { vehicle, target, now, readingsUnavailable } = useVehicleGarage()
  if (!vehicle) return null
  const estimate = chargingEstimate(vehicle, target, st, now)
  const unavailable = estimate.stale || (readingsUnavailable && vehicle.source.mode === 'live')
  return <section className="ev-station-estimate" aria-label="Charging estimate for your EV">
    <div className="ev-estimate-heading"><BatteryCharging size={17} /><strong>Your EV · to {target}%</strong>
      <span>{vehicle.source.mode === 'preview' ? 'Example' : vehicle.source.mode === 'manual' ? 'Manual' : 'Estimate'}</span></div>
    {unavailable ? <p>Car readings need an update. Refresh Your EV before planning this stop.</p>
      : estimate.compatibility === 'incompatible' ? <p className="ev-notice"><Plug size={14} /> No matching connector reported for your car. Check the provider before driving.</p>
      : <>
        <div className="ev-estimate-values">
          <div><span>Battery energy</span><strong>{estimate.energyKwh === null ? 'Unknown' : `+${estimate.energyKwh.toFixed(1)} kWh`}</strong></div>
          <div><span><Timer size={12} /> Charging time</span><strong>{estimate.minimumMinutes === null ? 'Unknown' : estimate.minimumMinutes === 0 ? 'Target reached' : `At least ${estimate.minimumMinutes} min`}</strong></div>
          <div><span><Wallet size={12} /> Charging cost</span><strong>{estimate.cost === null ? 'Unknown' : `≈ €${estimate.cost.toFixed(2)}`}</strong></div>
        </div>
        <p>{estimate.energyKwh === null ? 'Add usable battery capacity in Your EV to estimate this stop.'
          : 'Assumes 10% charging losses. Time uses peak power; actual charging takes longer. Parking and session fees excluded.'}</p>
        {estimate.compatibility === 'unknown' && <p>Connector compatibility needs checking.</p>}
      </>}
  </section>
}
