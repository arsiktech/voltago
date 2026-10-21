import type { Station } from '../types'
import { displayName } from './format'

/** Source-reported operator text, NOT verified CPO identity. Disagreement fails closed.
 * Keep exact source fields/IDs untouched; returned field/raw preserve attribution.
 * Never infer an operator from compatibility, source label or power. */
export function providerIdentity(st: Station) {
  const explicit = [['operator', st.operator], ['raw.operator', st.raw?.operator]] as const
  const normalized = explicit.map(([, value]) => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim().toLowerCase() : '').filter(Boolean)
  if (new Set(normalized).size > 1) return null
  const candidates: ReadonlyArray<readonly [string, unknown]> = normalized.length
    ? explicit
    : [['owner.name', st.owner?.name]]
  for (const [field, raw] of candidates) {
    if (typeof raw !== 'string' || !raw.trim()) continue
    let name = raw.replace(/\s+/g, ' ').trim()
    if (/\btesla\b/i.test(name)) {
      const superOperator = /supercharg/i.test(name)
      const destinationOperator = /destination/i.test(name)
      if ((superOperator && /destination|compatible/i.test(st.name + ' ' + name)) ||
          (destinationOperator && /supercharg/i.test(st.name))) return null
    }
    if (/^bp pulse$/i.test(name)) name = 'bp pulse'
    if (/^iberdrola$/i.test(name)) name = 'Iberdrola'
    if (/^tesla$/i.test(name)) {
      name = /\bsupercharger\b/i.test(st.name) && !/destination|compatible/i.test(st.name)
        ? 'Tesla Supercharger' : 'Tesla'
    }
    return { name, field, raw }
  }
  return null
}

export function stationTitle(st: Station): string {
  const provider = providerIdentity(st)
  // Identifier-like source names must retain their exact casing and punctuation.
  const place = /\d/.test(st.name) && !/\s/.test(st.name) ? st.name : displayName(st.name)
  if (!provider || place.toLowerCase().startsWith(provider.name.toLowerCase())) return place
  return `${provider.name} · ${place}`
}

/** Pixel budget uses the actual canvas font; also caps pre-measure/cache input. */
export function boundedLabel(text: string, measure: (text: string) => number, maxWidth: number): string {
  if (measure(text) <= maxWidth) return text
  if (measure('…') > maxWidth) return ''
  let prefix = Array.from(text).slice(0, 80)
  while (prefix.length && measure(prefix.join('') + '…') > maxWidth) prefix.pop()
  return prefix.join('') + '…'
}
