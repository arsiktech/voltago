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

/** Public operator client pages, each probed reachable (public GET, 2026-09-16).
 * `app` marks a network whose driver client is an app/landing Volta can hand off to;
 * shops and community maps hand off to their page instead. Never guess a scheme. */
const PROVIDER_CLIENTS: ReadonlyArray<{
  readonly provider: string
  readonly needles: readonly string[]
  readonly url: string
  readonly app: boolean
}> = [
  { provider: 'Endesa X Way', needles: ['ENDESA'], url: 'https://www.endesaxway.com/', app: true },
  { provider: 'Iberdrola | bp pulse', needles: ['IBERDROLA', 'BP PULSE', 'CHARGING TOGETHER'], url: 'https://iberdrola-bppulse.es/', app: true },
  { provider: 'Repsol', needles: ['REPSOL', 'WAYLET'], url: 'https://www.repsol.es/es/particulares/movilidad/', app: true },
  { provider: 'Wenea', needles: ['WENEA'], url: 'https://wenea.com/', app: true },
  { provider: 'Zunder', needles: ['ZUNDER'], url: 'https://www.zunder.com/', app: true },
  { provider: 'Feníe Energía', needles: ['FENIE'], url: 'https://recarga.fenieenergia.es/', app: true },
  { provider: 'ACCIONA', needles: ['ACCIONA'], url: 'https://recarga.acciona.com/', app: true },
  { provider: 'GiC (eCoVe)', needles: ['GESTION INTELIGENTE', 'ECO VE'], url: 'https://recargagic.com/', app: true },
  { provider: 'MELIB', needles: ['EMAYA', 'MELIB'], url: 'https://www.institutbalearenergia.com/es/red-melib/', app: true },
  { provider: 'Tesla', needles: ['TESLA'], url: 'https://www.tesla.com/es_es/findus', app: true },
  { provider: 'EVcharge', needles: ['ETECNIC', 'EVCHARGE'], url: 'https://etecnic.es/', app: true },
  { provider: 'Gaia', needles: ['GAIA'], url: 'https://gaiacharge.com/', app: true },
  { provider: 'E-Mobility', needles: ['E-MOBILITY'], url: 'https://e-mobility.es/', app: true },
  { provider: 'Lidl', needles: ['LIDL'], url: 'https://empresa.lidl.es/sostenibilidad/bueno-para-el-planeta/proteger-el-clima/construccion-y-logistica-sostenible/puntos-de-recarga', app: false },
  { provider: 'Canarias te recarga', needles: ['CANARIAS TE RECARGA', 'TERECARGA'], url: 'https://canariasterecarga.com/es/puntos-de-recarga/', app: false },
]

const stripAccents = (text: string): string =>
  text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toUpperCase()

/** Whole-token test: "GIC" must not match "LOGISTIC", "ENDESA" must match "ENDESA X WAY, S.L." */
const mentions = (haystack: string, needle: string): boolean =>
  new RegExp(`(^|[^A-Z0-9])${needle}([^A-Z0-9]|$)`).test(haystack)

/** "Lidl — Puerto de la Cruz…" / "Wenea — Calle X": the brand the source row leads with. */
const brandPrefix = (name: string): string => (name.split(/\s+[—–]\s+/)[0] || '').trim()

/** The operator's public client page for this station, or null when no source-backed
 * operator is known (fail closed: a wrong handoff is worse than none). */
export function providerAppUrl(st: Station): { provider: string; url: string; app: boolean } | null {
  const identity = providerIdentity(st)
  const explicitOperators = [st.operator, st.raw?.operator]
    .filter((value): value is string => typeof value === 'string' && value.trim() !== '')
    .map((value) => value.replace(/\s+/g, ' ').trim().toLowerCase())
  // Operator evidence that disagrees with itself is a conflict, never a guess.
  if (!identity && new Set(explicitOperators).size > 1) return null
  for (const candidate of identity ? [identity.name] : [brandPrefix(st.name)]) {
    if (!candidate || candidate.length > 40) continue
    const haystack = stripAccents(candidate)
    for (const client of PROVIDER_CLIENTS) {
      if (client.needles.some((needle) => mentions(haystack, needle))) {
        return { provider: client.provider, url: client.url, app: client.app }
      }
    }
  }
  return null
}

export interface ProviderHandoff {
  provider: string
  href: string
  app: boolean
  label: 'Open provider app' | 'Open provider site'
  ariaLabel: string
  title: string
}

/** Click-only handoff to the operator's own client. Volta never opens it by itself and
 * never starts or pays for a session — the driver stays the actor. */
export function providerHandoff(st: Station): ProviderHandoff | null {
  const client = providerAppUrl(st)
  if (!client) return null
  const label = client.app ? 'Open provider app' : 'Open provider site'
  return {
    provider: client.provider,
    href: client.url,
    app: client.app,
    label,
    ariaLabel: `${label}: ${client.provider} for ${stationTitle(st)}`,
    title: `${client.provider} public charging page (app or web). Opens in a new tab; Volta does not start or pay for a session.`,
  }
}

/** Pixel budget uses the actual canvas font; also caps pre-measure/cache input. */
export function boundedLabel(text: string, measure: (text: string) => number, maxWidth: number): string {
  if (measure(text) <= maxWidth) return text
  if (measure('…') > maxWidth) return ''
  let prefix = Array.from(text).slice(0, 80)
  while (prefix.length && measure(prefix.join('') + '…') > maxWidth) prefix.pop()
  return prefix.join('') + '…'
}
