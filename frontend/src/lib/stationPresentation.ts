import type { Connector, Station } from '../types'
import { availability, hasVerifiedPrice, isParkingRisk, powerText, priceText, priceUnit, privateAccess, sanePowerKw, tariffNotes, tariffWindow } from './format'

export interface CardCaution {
  label: string
  detail: string
  tone: 'warn' | 'danger'
}

/** Presentation only. Never changes the server's decision, price or ranking. */
export function cardCautions(st: Station): CardCaution[] {
  const cautions: CardCaution[] = []
  const a = availability(st)
  if (st.source_reported_unavailable ||
      (st.decision?.level === 'avoid' && /unavailable|out of service/i.test(st.decision.label)) ||
      /^(OUT_OF_SERVICE|OUTOFORDER|UNAVAILABLE|INOPERATIVE|FAULTED)$/i.test(st.status || '') ||
      (a.live && a.total != null && a.total > 0 && (a.out ?? 0) >= a.total)) {
    cautions.push({ label: 'Out of service', detail: st.source_reported_unavailable?.summary || 'Source reports this charger out of service. Check before driving.', tone: 'danger' })
  }
  const access = privateAccess(st)
  if (access) cautions.push({ label: access.short, detail: st.access?.note || access.label, tone: 'warn' })
  if (st.claim_conflict?.summary) cautions.push({ label: 'Sources disagree', detail: st.claim_conflict.summary, tone: 'danger' })
  if (isParkingRisk(st)) cautions.push({ label: st.parking?.paid ? 'Paid parking' : 'Limited parking', detail: st.parking?.note || st.parking?.label || 'Check parking terms.', tone: 'warn' })
  const window = tariffWindow(st)
  if (window) cautions.push({ label: 'Time-limited price', detail: window.label, tone: 'warn' })
  return cautions
}

/** Short hardware labels are not a claim about current per-port occupancy. */
export function groupedConnectors(connectors: Connector[]): string[] {
  const groups = new Map<string, { label: string; power: string; count: number; known: boolean }>()
  for (const c of connectors) {
    const raw = c.label?.trim() || c.type?.trim() || 'Type not published'
    const label = raw.replace(/\s*\(connector unspecified\)/i, ', type not published')
    const kw = sanePowerKw(c.power_kw)
    const power = kw == null ? '' : powerText(kw)
    // Group by actual reported power, not its rounded display value.
    const key = JSON.stringify([label, kw])
    const explicit = c.count != null && Number.isInteger(c.count) && c.count > 0
    const known = explicit
    const quantity = explicit ? c.count! : 0
    const group = groups.get(key)
    if (group) { group.count += quantity; group.known &&= known }
    else groups.set(key, { label, power, count: quantity, known })
  }
  return [...groups.values()].map(g => `${g.known && g.count > 0 ? `${g.count} × ` : ''}${g.label}${g.power ? ` · ${g.power}` : ''}${g.known ? '' : ' · quantity not published'}`)
}

/** Map pins contain a verified price and at most a short power hint. */
export function mapPinPreview(st: Station): { price: string; detail: string } | null {
  if (!hasVerifiedPrice(st)) return null
  const price = st.is_free || st.price_min === 0 ? 'Free'
    : st.price_min != null && Number.isFinite(st.price_min) && st.price_min >= 0 ? `€${st.price_min.toFixed(2)}` : null
  if (!price) return null
  return { price, detail: powerText(st.connector_summary?.max_power_kw ?? st.max_power_kw) }
}

/** Source contact and booking instructions are not a published tariff. */
function publishedTariffText(st: Station): string | null {
  const text = st.tariff_details?.text?.trim()
  if (!text) return null
  const freeStatement = /^(?:(?:tariff|tarifa|charging|charge|energy|rate)\s*[:\-]?\s*)?(?:free|gratis|gratuit[oa]s?)\b|^first\s+(?:hour|\d+\s*(?:minutes?|hours?))\s+free\b/i.test(text)
  const explicitRate = /(?:€\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*(?:€|eur(?:os?)?))\s*(?:\/|per\s+)\s*(?:kwh|hours?|horas?|h|minutes?|minutos?|min|sessions?|sesión)\b/i.test(text)
  return freeStatement || explicitRate ? text : null
}

/** A popup can explain a published tariff without granting it verified map-pin authority. */
export function stationPrice(st: Station): { label: string; value: string; notes: string[] } {
  const price = priceText(st)
  const value = price === '—'
    ? publishedTariffText(st) || 'Unknown'
    : [price, priceUnit(st)].filter(Boolean).join(' ')
  return {
    label: value !== 'Unknown' && !hasVerifiedPrice(st) ? 'Source price' : 'Charging price',
    value,
    notes: tariffNotes(st),
  }
}
