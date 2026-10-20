import type { PriceEvidence as PriceEvidenceItem } from '../types'
import { publicWebUrl } from '../lib/format'

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
          <p className="whitespace-pre-wrap">{item.text}</p>
          {item.parking_price && <p className="whitespace-pre-wrap">Parking: {item.parking_price}</p>}
          {item.kind !== 'electromaps_community' && <p>Checked: {evidenceDate(item.fetched_at)}</p>}
          {href && <a href={href} target="_blank" rel="noopener noreferrer" aria-label={`View price source: ${label}`}
            className="inline-flex min-h-11 items-center font-medium text-brand-strong underline">View price source</a>}
        </li>
      })}
    </ul>
  </section>
}
