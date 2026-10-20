import type { PriceEvidence as PriceEvidenceItem, PriceEvidenceRate } from '../types'
import { evidenceEuro, publicWebUrl } from '../lib/format'

/** Keep the source's calendar date, without replacing it with a download or local date. */
function evidenceDate(value: string | null): string {
  const day = typeof value === 'string' ? /^(\d{4}-\d{2}-\d{2})(?:T| |$)/.exec(value)?.[1] : null
  if (day && !day.startsWith('0000')) {
    const date = new Date(`${day}T00:00:00Z`)
    if (Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === day) return day
  }
  return 'Date unknown'
}

function evidenceLabel(item: PriceEvidenceItem): string | null {
  switch (item.kind) {
    case 'repsol_network_tariff': return 'Repsol published tariff · station may differ'
    case 'electromaps_community': return `Electromaps community price · ${evidenceDate(item.source_updated_at)}`
    case 'chargemap_pass': return 'Price with Chargemap Pass'
    default: return null
  }
}

const speedLabels = { normal: 'Normal', fast: 'Fast', ultrafast: 'Ultra-fast', motorcycle: 'Motorcycle', all: '' }

function validRate(rate: PriceEvidenceRate): boolean {
  return rate != null && Number.isFinite(rate.min) && rate.min >= 0 && typeof rate.from === 'boolean' &&
    (rate.max == null || (Number.isFinite(rate.max) && rate.max >= rate.min)) &&
    (rate.label == null || Object.hasOwn(speedLabels, rate.label)) &&
    (rate.after_minutes == null || (Number.isFinite(rate.after_minutes) && rate.after_minutes >= 0)) &&
    ((rate.kind === 'energy' && ['kWh', 'started_kWh'].includes(rate.unit) && rate.after_minutes == null) ||
      (rate.kind === 'idle' && rate.unit === 'minute' && rate.label == null && rate.max == null && !rate.from))
}

/** Structured annotations only. Raw text stays in the payload, never on the card. */
export function evidenceRateText(item: PriceEvidenceItem): string {
  const rates = item.rates
  if (!Array.isArray(rates) || !rates.length || !rates.every(validRate)) return 'See source for prices'
  const allKwh = rates.every(rate => rate.kind === 'energy' && rate.unit === 'kWh')
  const parts = rates.map(rate => {
    const amount = evidenceEuro(rate.min) + (rate.max == null ? '' : `–${evidenceEuro(rate.max).slice(1)}`)
    if (rate.kind === 'idle') return `idle ${amount}/min${rate.after_minutes == null ? '' : ` after ${rate.after_minutes} min`}`
    const label = rate.label == null ? '' : speedLabels[rate.label]
    const prefix = label ? `${label} ${rate.from ? 'from ' : ''}` : rate.from ? 'From ' : ''
    return `${prefix}${amount}${allKwh ? '' : rate.unit === 'started_kWh' ? ' per started kWh' : ' per kWh'}`
  })
  return parts.join(' · ') + (allKwh ? ' per kWh' : '')
}

export function PriceEvidence({ items }: { items?: PriceEvidenceItem[] }) {
  const claims = (items ?? []).flatMap(item => {
    const label = evidenceLabel(item)
    return label ? [{ item, label }] : []
  })
  if (!claims.length) return null

  return <section aria-label="Additional price evidence" className="mt-3 border-t border-border pt-3 text-xs leading-relaxed text-muted [overflow-wrap:anywhere]">
    <ul className="space-y-2">
      {claims.map(({ item, label }) => {
        const href = publicWebUrl(item.source_url)
        const id = JSON.stringify([item.source_url, item.kind, item.source_updated_at, item.fetched_at])
        return <li key={id} data-price-evidence={item.kind}>
          <p className="font-medium">{label}</p>
          <p className="tabular-nums">{evidenceRateText(item)}</p>
          {item.parking_price && <p className="whitespace-pre-wrap">Parking: {item.parking_price}</p>}
          {item.kind !== 'electromaps_community' && <p>Checked: {evidenceDate(item.fetched_at)}</p>}
          {href && <a href={href} target="_blank" rel="noopener noreferrer" aria-label={`View price source: ${label}`}
            className="inline-flex min-h-11 items-center font-medium text-brand-strong underline">View price source</a>}
        </li>
      })}
    </ul>
  </section>
}
