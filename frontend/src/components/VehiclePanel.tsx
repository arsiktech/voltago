import { useEffect, useRef, useState, type ChangeEvent, type CSSProperties } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { ArrowUpRight, BatteryCharging, CarFront, ChevronDown, ImagePlus, Pencil, Plug, RefreshCw, ShieldCheck, X, Zap } from 'lucide-react'
import { useVehicleGarage } from '../hooks/useVehicleGarage'
import { energyToTarget, vehicleIsStale, vehicleName, type Vehicle } from '../lib/vehicles'
import { liveMetricSwap, quickEase, smoothSpring } from '../lib/motion'
import { VehicleEditor } from './VehicleEditor'

function Metric({ value, unit }: { value: string; unit?: string }) {
  const reduce = Boolean(useReducedMotion())
  return <span className="ev-metric-number"><motion.span key={value} variants={liveMetricSwap(reduce)} initial="hidden" animate="show">{value}</motion.span>{unit && <span className="ev-metric-unit">{unit}</span>}</span>
}

function readingAge(date: string | null, now: number): string {
  const seconds = (now - Date.parse(date || '')) / 1000
  if (!Number.isFinite(seconds) || seconds < -60) return 'Reading time unknown'
  if (seconds < 60) return 'Updated just now'
  if (seconds < 3600) return `Updated ${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `Updated ${Math.floor(seconds / 3600)} h ago`
  return `Updated ${Math.floor(seconds / 86400)} d ago`
}

function VehiclePortrait({ vehicle }: { vehicle: Vehicle | null }) {
  const key = `volta.vehicle.photo.${vehicle?.id || 'none'}`
  const [photo, setPhoto] = useState<string | null>(() => {
    try {
      const value = localStorage.getItem(key)
      return value && /^data:image\/(jpeg|png|webp);base64,/.test(value) ? value : null
    } catch { return null }
  })
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 1500000) {
      setError('Choose a JPG, PNG or WebP photo smaller than 1.5 MB.'); return
    }
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result !== 'string') return
      setPhoto(reader.result)
      try { localStorage.setItem(key, reader.result); setError(null) }
      catch { setError('Your photo is shown for this visit. Browser storage is full.') }
    }
    reader.onerror = () => setError('Could not open that photo. Try another image.')
    reader.readAsDataURL(file)
  }
  const editable = vehicle && vehicle.source.mode !== 'preview'
  return <>
    <figure className={`ev-portrait ${photo ? 'ev-owner-photo' : ''}`}>
      <img src={photo || '/images/ev-silver.webp'} alt={photo ? `Your photo of ${vehicle ? vehicleName(vehicle) : 'your EV'}` : 'Silver electric sedan illustration'} />
      <figcaption>{photo ? 'Your photo · on this device' : 'Vehicle illustration'}</figcaption>
      {editable && <>
        <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" tabIndex={-1} onChange={upload} aria-label="Choose your car photo" />
        <button type="button" className="ev-photo-button ev-icon-button" onClick={() => input.current?.click()} aria-label="Change car photo" title="Your photo stays on this device"><ImagePlus size={17} /></button>
        {photo && <button type="button" className="ev-photo-remove ev-icon-button" aria-label="Remove car photo" onClick={() => { setPhoto(null); try { localStorage.removeItem(key) } catch { /* Current-visit removal still works. */ } }}><X size={14} /></button>}
      </>}
    </figure>
    {error && <p role="status" className="ev-field-help">{error}</p>}
  </>
}

function VehicleReadings({ vehicle, onEdit }: { vehicle: Vehicle; onEdit: () => void }) {
  const garage = useVehicleGarage()
  const reduce = Boolean(useReducedMotion())
  const mode = vehicle.source.mode
  const stale = vehicleIsStale(vehicle, garage.now) || (mode === 'live' && garage.readingsUnavailable)
  const modeLabel = mode === 'preview' ? 'Example readings' : mode === 'manual' ? 'Manual readings'
    : mode === 'simulation' ? 'Simulated readings' : stale ? 'Reading needs updating' : 'Connected via Smartcar'
  const energy = energyToTarget(vehicle, garage.target, garage.now)
  const available = vehicle.usableEnergyKwh
  const state = vehicle.chargingState?.toLowerCase()
  const status = state === 'charging' ? 'Charging' : state === 'fully_charged' ? 'Full'
    : state === 'plugged_in' ? 'Plugged in' : state === 'unplugged' ? 'Unplugged'
    : state === 'not_charging' ? 'Not charging' : 'Unknown'
  return <>
    {garage.vehicles.length > 1 && <label className="ev-vehicle-select"><CarFront size={15} />
      <select aria-label="Selected vehicle" value={vehicle.id} onChange={(event) => garage.selectVehicle(event.target.value)}>
        {garage.vehicles.map((item) => <option key={item.id} value={item.id}>{vehicleName(item)}{item.source.mode === 'preview' ? ' · example' : item.source.mode === 'manual' ? ' · manual' : ''}</option>)}
      </select><ChevronDown size={14} aria-hidden="true" />
    </label>}
    <VehiclePortrait key={vehicle.id} vehicle={vehicle} />
    <div className="ev-car-title"><div><h3>{vehicleName(vehicle)}</h3><p className="ev-muted">{vehicle.year ? `${vehicle.year} · ` : ''}{modeLabel}</p></div>
      <button className="ev-icon-button" onClick={onEdit} aria-label={mode === 'manual' ? 'Edit car readings' : 'Edit car specifications'}><Pencil size={16} /></button></div>
    {mode === 'preview' && <p className="ev-preview-note">A preview of the experience. This isn’t a connected car.</p>}
    <div className="ev-battery-hero">
      <div><Metric value={vehicle.batteryPercent === null ? '—' : `${Math.round(vehicle.batteryPercent)}`} unit="%" /><span className="ev-hero-label">Battery</span></div>
      <div className="ev-range"><Metric value={vehicle.rangeKm === null ? '—' : `${Math.round(vehicle.rangeKm)}`} unit="km" /><span className="ev-hero-label">Range</span></div>
    </div>
    <div className="ev-battery-track" role="meter" aria-label="Battery state of charge" aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={vehicle.batteryPercent ?? undefined} aria-valuetext={vehicle.batteryPercent === null ? 'Unknown' : `${vehicle.batteryPercent}%`}>
      <motion.span initial={false} animate={{ width: `${vehicle.batteryPercent || 0}%` }} transition={reduce ? { duration: 0 } : quickEase} />
    </div>
    <dl className="ev-stats">
      <div><dt>Available{available !== null ? ' ≈' : ''}</dt><dd>{available === null ? 'Unknown' : available.toFixed(1)}{available !== null && <span> kWh</span>}</dd></div>
      <div><dt>Full battery</dt><dd>{vehicle.batteryCapacityKwh ?? 'Unknown'}{vehicle.batteryCapacityKwh !== null && <span> kWh</span>}</dd></div>
      <div><dt>Status</dt><dd>{status}</dd></div>
    </dl>
    {state === 'charging' && vehicle.chargePowerKw !== null && <p className="ev-charge-power"><BatteryCharging size={15} />Charging at {vehicle.chargePowerKw.toFixed(1)} kW</p>}
    <div className="ev-charge-target"><label htmlFor="vehicle-charge-target">Charge target</label><output htmlFor="vehicle-charge-target">{garage.target}%</output></div>
    <div className="ev-target-slider"><span>50%</span><input id="vehicle-charge-target" aria-label="Charge target" type="range" min="50" max="100" step="5"
      style={{ '--target-progress': `${(garage.target - 50) * 2}%` } as CSSProperties}
      value={garage.target} onChange={(event) => garage.setTarget(Number(event.target.value))} aria-valuetext={`${garage.target}%`} /><span>100%</span></div>
    <div className="ev-energy-to-target"><Zap size={17} /><span>{energy === null ? 'Add battery capacity to estimate your charge.'
      : energy === 0 ? 'Your battery has reached this target.' : <><strong>+{energy.toFixed(1)} kWh</strong> to your target.</>}</span></div>
    <div className="ev-next-stop"><h4>Made for your next stop</h4><p>Open a charger’s Details to see the energy, time and charging cost for your car.</p><ArrowUpRight size={18} aria-hidden="true" /></div>
    {(stale || garage.connection.data?.connectionStatus === 'reconnect') && <p className="ev-notice">{garage.connection.data?.connectionStatus === 'reconnect' ? 'Reconnect to update your car’s readings.' : 'These readings may be out of date. Refresh before planning a stop.'}</p>}
    <div className="ev-reading-source"><span>{mode === 'preview' ? 'Example only · no vehicle connection' : `${mode === 'manual' ? 'Manual · ' : ''}${readingAge(vehicle.observedAt, garage.now)}`}</span>
      {(mode === 'live' || mode === 'simulation') && <button type="button" className="ev-icon-button" aria-label="Refresh car readings" disabled={garage.pending !== null} onClick={() => void garage.refresh()}><RefreshCw size={16} className={garage.pending === 'refresh' ? 'animate-spin' : ''} /></button>}
    </div>
    {mode === 'manual' && <p className="ev-field-help">Update when your battery changes. Your charge target is for planning; it doesn’t change the car’s settings.</p>}
    {mode !== 'manual' && mode !== 'preview' && <p className="ev-field-help">Updates about once a minute while open. Your car may report less often. Specifications you enter stay on this device.</p>}
    {mode === 'manual' && <button type="button" className="ev-remove ev-button" onClick={() => garage.removeLocalVehicle()}>Remove this EV</button>}
    {(mode === 'live' || mode === 'simulation') && <button type="button" className={`ev-button ev-connect-another ${garage.connection.data?.connectionStatus === 'reconnect' ? 'ev-primary' : ''}`} disabled={garage.pending !== null} onClick={() => void garage.connect()}>{garage.connection.data?.connectionStatus === 'reconnect' ? 'Reconnect' : 'Connect another EV'} <Plug size={15} /></button>}
  </>
}

function VehicleFooter({ vehicle, onEdit }: { vehicle: Vehicle; onEdit: () => void }) {
  const garage = useVehicleGarage()
  const mode = vehicle.source.mode
  return <footer className="ev-panel-footer">
    <div className="ev-footer-actions">
      <button type="button" className="ev-button" onClick={onEdit}>{mode === 'manual' ? 'Edit readings' : mode === 'preview' ? 'Add your EV' : 'Car settings'}</button>
      {mode === 'preview' ? <button type="button" className="ev-button ev-primary" onClick={() => garage.removeLocalVehicle()}>Close preview</button>
        : mode === 'manual' ? <button type="button" className="ev-button ev-primary" disabled={!garage.connection.data?.configured || garage.pending !== null} onClick={() => void garage.connect()}>{garage.pending === 'connect' ? 'Connecting…' : 'Connect live'}</button>
        : <button type="button" className="ev-button" disabled={garage.pending !== null} onClick={() => void garage.disconnect()}>{garage.pending === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}</button>}
    </div>
    {mode === 'manual' && !garage.connection.data?.configured && <p className="ev-field-help">Live connection isn’t available here yet. Your manual readings are ready to use.</p>}
  </footer>
}

export default function VehiclePanel({ onClose, callbackError }: { onClose: () => void; callbackError: boolean }) {
  const garage = useVehicleGarage()
  const reduce = Boolean(useReducedMotion())
  const [editing, setEditing] = useState(false)
  const close = useRef<HTMLButtonElement>(null)
  useEffect(() => { close.current?.focus() }, [])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); onClose() } }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [onClose])
  return <motion.aside id="vehicle-panel" className="vehicle-panel" role="dialog" aria-modal="false" aria-labelledby="vehicle-panel-title"
    initial={reduce ? { opacity: 0 } : { opacity: 0, x: 16, scale: 0.985 }}
    animate={{ opacity: 1, x: 0, scale: 1 }} exit={reduce ? { opacity: 0 } : { opacity: 0, x: 12, scale: 0.985 }}
    transition={reduce ? quickEase : smoothSpring}>
    <header className="ev-panel-header"><h2 id="vehicle-panel-title">Your EV</h2><button ref={close} type="button" className="ev-icon-button" onClick={onClose} aria-label="Close your EV"><X size={20} /></button></header>
    <div className="ev-panel-content">
      {callbackError && <p className="ev-notice" role="alert">Your car wasn’t connected. Try again, or enter your battery manually.</p>}
      {garage.actionError && <p className="ev-notice" role="status">{garage.actionError}</p>}
      {editing ? <VehicleEditor vehicle={garage.vehicle} onDone={() => setEditing(false)} /> : garage.vehicle ? <VehicleReadings vehicle={garage.vehicle} onEdit={() => setEditing(true)} /> : <>
        <VehiclePortrait vehicle={null} />
        <div className="ev-empty-intro"><h3>Your car, in the loop.</h3><p>Know what’s in your battery.<br />Find a stop that fits your charge.</p></div>
        <div className="ev-benefit"><BatteryCharging size={20} /><div><strong>Your battery at a glance</strong><p>State of charge, available energy and range.</p></div></div>
        <div className="ev-benefit"><Zap size={20} /><div><strong>A plan for every stop</strong><p>Choose a target. Estimate charging time and cost in station details.</p></div></div>
        <div className="ev-connect-entry">
          {garage.connection.isPending ? <p role="status" className="ev-muted">Checking live connection…</p>
            : garage.connection.data?.configured ? <>
              <button type="button" className="ev-button ev-primary" disabled={garage.pending !== null} onClick={() => void garage.connect()}><Plug size={17} />{garage.pending === 'connect' ? 'Connecting…' : 'Connect your car'}</button>
              <p className="ev-field-help">Secure sign-in with your car brand, via Smartcar. Support and update frequency vary by vehicle.</p>
            </> : <p className="ev-notice">Live connection isn’t available here yet. Add your dashboard readings to start planning.</p>}
          {garage.connection.isError && <button className="ev-button" onClick={() => void garage.connection.refetch()}>Retry connection</button>}
          <button type="button" className={`ev-button ${!garage.connection.data?.configured ? 'ev-primary' : ''}`} onClick={() => setEditing(true)}><Pencil size={16} />Enter manually</button>
          <button type="button" className="ev-button ev-preview-action" onClick={garage.startPreview}>Explore an example <ArrowUpRight size={15} /></button>
        </div>
        <div className="ev-privacy"><ShieldCheck size={17} /><p>Read-only access. Your car stays in your control. Disconnect from this browser any time.</p></div>
      </>}
    </div>
    {!editing && garage.vehicle && <VehicleFooter vehicle={garage.vehicle} onEdit={() => setEditing(true)} />}
  </motion.aside>
}
