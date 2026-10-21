import type { SourceRef, Station } from '../types'
import { publicWebUrl, remoteStartAction } from './format'
import { stationTitle } from './provider'

const exactKinds = new Set(['station_page', 'start_page', 'provider_station_page', 'map_object'])
const genericPaths = new Set(['map', 'mapa', 'maps', 'locations', 'stations', 'charging-stations', 'chargers', 'points', 'index', 'index.html', 'home', 'contact', 'contacto', 'login', 'about', 'tariffs', 'pricing', 'locator', 'search', 'puntos-de-recarga', 'findus'])
const sourceNames: ReadonlyArray<readonly [string, string]> = [
  ['canariasterecarga.com', 'Canarias te recarga'], ['gaiacharge.com', 'Gaia'],
  ['openchargemap.org', 'OpenChargeMap'], ['openchargemap.io', 'OpenChargeMap'],
  ['openstreetmap.org', 'OpenStreetMap'], ['electromaps.com', 'Electromaps'],
  ['chargemap.com', 'Chargemap'], ['mapareve.es', 'MapaREVE'],
  ['fenieenergia.es', 'Feníe Energía'], ['endollabarcelona.cat', 'Endolla'],
  ['tesla.com', 'Tesla'], ['wenea.com', 'Wenea'], ['zunder.com', 'Zunder'],
  ['repsol.es', 'Repsol'], ['lidl.es', 'Lidl'],
  ['telpark.com', 'Telpark'], ['interparking.es', 'Interparking'],
]

/** Name the linked source, never infer its identity from the charger's operator. */
export function sourceName(url?: string | null, suggested?: string | null): string {
  const href = publicWebUrl(url)
  const host = href ? new URL(href).hostname.toLowerCase().replace(/^www\./, '') : ''
  const known = sourceNames.find(([domain]) => host === domain || host.endsWith(`.${domain}`))
  if (known) return known[1]
  const name = suggested?.replace(/\s+/g, ' ').trim()
  return name && name.length <= 80 && !/^(?:source|public source|directory(?: page)?|official data|live data|community|provider)$/i.test(name)
    ? name : host || 'Source'
}

function exactSource(candidate: SourceRef): { name: string; url: string; kind: string } | null {
  if (typeof candidate.url !== 'string' || candidate.url.length > 4096 || [...candidate.url.trim()].some(c => c === '\\' || c.charCodeAt(0) <= 32 || c.charCodeAt(0) === 127)) return null
  const href = publicWebUrl(candidate.url)
  if (!href) return null
  const url = new URL(href)
  if (url.port && !['80', '443'].includes(url.port)) return null
  const host = url.hostname.toLowerCase().replace(/^www\./, '')
  const path = url.pathname
  let kind = candidate.kind || ''
  if (host === 'canariasterecarga.com') {
    if (url.search || !/^\/(?:es|en)\/(?:(?:mapa\/)?p\d{1,10}|punto-de-recarga-[a-z0-9-]+)\/?$/.test(path)) return null
    kind = 'station_page'
  } else if (host === 'gaiacharge.com' || host === 'partner-next.gaiacharge.com') {
    if (!/^\/cpo\/jump\/[^/]+\/(?:[^/]+\/)?[^/]+\/?$/.test(path)) return null
    kind = 'start_page'
  } else if (host === 'openstreetmap.org') {
    if (!/^\/(?:node|way|relation)\/[1-9]\d*\/?$/.test(path)) return null
    kind = 'map_object'
  } else if (host === 'openchargemap.org') {
    const id = /^\/site\/poi\/details\/([1-9]\d*)\/?$/i.exec(path)?.[1]
    if (!id) return null
    url.href = `https://map.openchargemap.io/?mode=details&id=${id}`
    kind = 'station_page'
  } else if (host === 'map.openchargemap.io') {
    const identifiers = url.searchParams.has('id') ? url.searchParams.getAll('id') : url.searchParams.getAll('poi')
    const ids = url.searchParams.getAll('id'), pois = url.searchParams.getAll('poi')
    if (path !== '/' || url.searchParams.getAll('mode').join() !== 'details' || identifiers.length !== 1 ||
      (ids.length && pois.length && ids.join() !== pois.join()) || !/^[1-9]\d*$/.test(identifiers[0])) return null
    kind = 'station_page'
  } else if (host === 'electromaps.com' || host === 'map.electromaps.com') {
    if (!/^\/(?:en|es|fr|pt)\/p\/[1-9]\d*(?:\/[^/]+)*\/?$/.test(path)) return null
    kind = 'station_page'
  } else if (host === 'chargemap.com') {
    if (!(/^\/(?!(?:index|map|contact|about|terms|privacy|pricing|tariffs)\.html$)[a-z0-9][a-z0-9-]*\.html$/i.test(path) || /^\/[a-z]{2}(?:-[a-z]{2})?\/charging-stations\/[^/]+\/?$/i.test(path))) return null
    kind = 'station_page'
  } else if (host === 'recarga.fenieenergia.es') {
    if (!/^\/punto-recarga\/(?!todos\/?$)[a-zA-Z0-9_-]+\/?$/.test(path)) return null
    kind = 'provider_station_page'
  } else if (host === 'tesla.com') {
    if (!/^\/(?:[a-z]{2}_[a-z]{2}\/)?findus\/location\/(?:supercharger|destinationcharger)\/[^/]+\/?$/i.test(path)) return null
    kind = 'station_page'
  } else if (host === 'telpark.com') {
    if (!/^\/[a-z]{2}\/ciudades\/[a-z0-9-]+\/[a-z0-9-]+\/?$/.test(path)) return null
    kind = 'station_page'
  } else if (host === 'interparking.es') {
    if (!/^\/es\/aparcamientos\/[a-z0-9/_-]+\/?$/.test(path)) return null
    kind = 'station_page'
  } else {
    const segments = path.toLowerCase().split('/').filter(Boolean)
    if (!exactKinds.has(kind) || !segments.length || genericPaths.has(segments[segments.length - 1]) || /^[a-z]{2}(?:-[a-z]{2})?$/.test(segments.join('/')) || /\.(?:json|geojson|csv|xml|pdf|zip|gz)$/i.test(path)) return null
  }
  return { name: sourceName(url.href, candidate.name), url: url.href, kind }
}

/** Legacy rows use only existing exact links; no invented station IDs or homepage fallback. */
function sourceCandidates(st: Station): SourceRef[] {
  return [
    st.station_source || {}, st.primary_source || {},
    { name: st.source_details?.source_name, url: st.source_details?.source_url },
    { name: st.charge_claim?.source, url: st.tariff_details?.source_url },
    { name: st.charge_claim?.source, url: st.charge_claim?.url || undefined },
    { name: st.source, url: st.source_url }, ...(st.sources || []),
    ...(st.connectors || []).map(c => ({ name: st.source, url: c.source_url || undefined })),
  ]
}

export function stationSource(st: Station): { name: string; url: string; kind: string } | null {
  if (st.station_source !== undefined) return st.station_source ? exactSource(st.station_source) : null
  for (const candidate of sourceCandidates(st)) {
    const source = exactSource(candidate)
    if (source) return source
  }
  return null
}

/** Keep each fact's source identity while upgrading a generic reference to its existing charger page. */
export function stationSourceReference(st: Station, url?: string | null, name?: string | null) {
  const href = publicWebUrl(url)
  if (!href) return null
  const exact = exactSource({ url: href, name: name || undefined })
  if (exact) return { ...exact, exact: true }
  const host = new URL(href).hostname.replace(/^www\./, '')
  const segments = new URL(href).pathname.toLowerCase().split('/').filter(Boolean)
  const overview = !segments.length || genericPaths.has(segments[segments.length - 1]) || /^[a-z]{2}$/.test(segments.join('/'))
  for (const candidate of sourceCandidates(st)) {
    const source = exactSource(candidate)
    if (source && (source.url === href || (overview && new URL(source.url).hostname.replace(/^www\./, '') === host))) return { ...source, exact: true }
  }
  return { name: sourceName(href, name), url: href, exact: false }
}

export function stationSourceAction(st: Station) {
  const source = stationSource(st)
  if (!source) return null
  const start = remoteStartAction({ ...st, primary_source: source })
  const label = `${start ? 'Start/check' : 'Open'} ${source.name}`
  return {
    ...source, href: source.url, label,
    ariaLabel: `${label} for ${stationTitle(st)}`,
    title: start?.title || `View this charging point on ${source.name}. Opens in a new tab.`,
  }
}
