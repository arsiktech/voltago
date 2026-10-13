import { memo, useEffect, useRef, useState } from 'react'
import { stationTitle, providerIdentity, providerHandoff, boundedLabel } from '../lib/provider'
import { guardBasemapShieldFilters } from '../lib/basemap'
import * as maplibregl from 'maplibre-gl'
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { FeatureCollection, Point } from 'geojson'
import type { Bbox, Coords, Station, Viewport } from '../types'
import {
  decisionLevel,
  hasVerifiedPrice,
  isPrivateAccess,
  mapsUrl,
  powerText,
  priceText,
  priceUnit,
  privateAccess,
  publicWebUrl,
  remoteStartAction,
  sanePowerKw,
} from '../lib/format'

// v6 is ESM-only. Vite must bundle the worker and its shared imports together;
// a plain asset URL works in development but omits those imports in production.
maplibregl.setWorkerUrl(mapWorkerUrl)

const COLORS = {
  light: { go: '#11a554', check: '#c07c00', verify: '#4f74c7' },
  dark: { go: '#21d277', check: '#f0b429', verify: '#7aa2f7' },
}

// The canvas occupies its own grid area beside the results. Camera bounds therefore
// describe visible space, with no duplicated sidebar-width or hidden-map offsets.
const MAP_PADDING = { top: 64, right: 24, bottom: 32, left: 24 } as const

const prefersReducedMotion = (): boolean =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true

/* ---------- Price pins painted onto the map ---------- */

/** Legal-form tails ("S.L.", "S.A.U") eat the pill's pixel budget without telling a
 * driver anything, and they were what pushed the hardware facts past the ellipsis. */
const shortOperator = (name: string, max = 14): string => {
  const trimmed = name
    .replace(/[,.]?\s*\b(?:s\.?l\.?u?|s\.?a\.?u?|sociedad\s+\w+|limited|ltd)\b\.?$/i, '')
    .replace(/[,.]\s*$/, '')
    .replace(/\s+/g, ' ')
    .trim()
  const chars = Array.from(trimmed)
  return chars.length > max ? chars.slice(0, max).join('') + '…' : trimmed
}

// A compact, honest price for the map bubble: "Free", "€0.25", or null when unknown
// (leads without a verified price stay a plain dot — we never invent a number).
function pinPreview(st: Station): { price: string; detail: string } | null {
  // An exact OSM/directory point can still be shown as a Not-live location, but its
  // price is not a map claim until the price contract is explicitly verified.
  if (!hasVerifiedPrice(st)) return null
  const price =
    st.is_free || st.price_min === 0 ? 'Free' : st.price_min != null ? `€${st.price_min.toFixed(2)}` : null
  const maxKw = sanePowerKw(st.connector_summary?.max_power_kw ?? st.max_power_kw ?? null)
  const plugs = (st.connector_labels && st.connector_labels.length
    ? st.connector_labels
    : (st.connectors || []).map((c) => c.label || c.type).filter(Boolean)
  ).slice(0, 2)
  const total = st.connector_summary?.total ?? st.total_evse ?? st.connector_count ?? (st.connectors?.length || null)
  const operator = providerIdentity(st)?.name
  const detail = [
    operator ? shortOperator(operator) : '',
    plugs.length ? plugs.join('/') : '',
    maxKw != null ? powerText(maxKw) : '',
    total != null ? `${total}×` : '',
  ]
    .filter(Boolean)
    .join(' · ')
  if (!price && !detail) return null
  return { price: price || '—', detail: boundedLabel(detail, text => text.length, 64) }
}

function priceIconId(label: string, level: string, theme: 'light' | 'dark', priv: boolean, detail = ''): string {
  return `price:${theme}:${level}:${priv ? 'p' : 'o'}:${label}:${detail}`
}

// Lower sorts first → wins MapLibre's collision placement. Live + cheaper + go-now bubble up.
function priceSort(st: Station, label: string): number {
  const base = st.lead ? 1000 : 0
  const price = label === 'Free' ? 0 : (st.price_min ?? 1)
  return Math.round(base + price * 100)
}

const PILL_DPR = 2
const PILL_MARGIN = 6 // canvas breathing room for the drop shadow + tail tip
const PILL_FONT =
  '700 12.5px "Inter Variable", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
const PILL_SUB_FONT =
  '650 10px "Inter Variable", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'

function roundRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

/** Tiny padlock glyph — marks private / customers-only access (not free public). */
function drawLock(ctx: CanvasRenderingContext2D, cx: number, cy: number, color: string): void {
  const bw = 7
  const bh = 6
  const by = cy - bh / 2 + 1.4 // body top; leaves room for the shackle above
  ctx.strokeStyle = color
  ctx.lineWidth = 1.3
  ctx.beginPath()
  ctx.arc(cx, by, 2.1, Math.PI, 0, true) // shackle: top semicircle
  ctx.stroke()
  ctx.fillStyle = color
  roundRectPath(ctx, cx - bw / 2, by, bw, bh, 1.4)
  ctx.fill()
}

/** Bake a rounded pin (price + speed/plugs/count, optional lock, soft shadow, tail). */
function makePriceImage(
  label: string,
  level: string,
  theme: 'light' | 'dark',
  priv: boolean,
  detail = '',
): { image: ImageData; pixelRatio: number } {
  const colors = COLORS[theme]
  const dotColor = level === 'go' ? colors.go : level === 'check' ? colors.check : colors.verify
  const dark = theme === 'dark'
  const bg = dark ? '#1b221f' : '#ffffff'
  const fg = dark ? '#edf1ee' : '#0e1512'
  const muted = dark ? '#9aa59f' : '#5a6560'
  const border = dark ? 'rgba(255,255,255,0.14)' : 'rgba(14,21,18,0.12)'
  const shadow = dark ? 'rgba(0,0,0,0.55)' : 'rgba(14,21,18,0.24)'
  const freeColor = dark ? colors.go : '#0d7d40'
  const lockColor = colors.check

  const padX = 9
  const gap = 6
  const dotR = 3.5
  const twoLine = Boolean(detail)
  const h = twoLine ? 34 : 23
  const radius = twoLine ? 12 : 11
  const tail = 5
  const lockW = 8

  const scratch = document.createElement('canvas').getContext('2d')!
  scratch.font = PILL_FONT
  const textW = Math.ceil(scratch.measureText(label).width)
  scratch.font = PILL_SUB_FONT
  detail = boundedLabel(detail, text => scratch.measureText(text).width, 162)
  const detailW = detail ? Math.ceil(scratch.measureText(detail).width) : 0
  const lockSpace = priv ? gap + lockW : 0
  const w = Math.ceil(Math.max(dotR * 2 + gap + textW + lockSpace, detailW) + padX * 2)
  const W = w + PILL_MARGIN * 2
  const H = h + tail + PILL_MARGIN * 2

  const canvas = document.createElement('canvas')
  canvas.width = W * PILL_DPR
  canvas.height = H * PILL_DPR
  const ctx = canvas.getContext('2d')!
  ctx.scale(PILL_DPR, PILL_DPR)

  const x = PILL_MARGIN
  const y = PILL_MARGIN
  const cx = W / 2

  ctx.save()
  ctx.shadowColor = shadow
  ctx.shadowBlur = 6
  ctx.shadowOffsetY = 2
  ctx.fillStyle = bg
  roundRectPath(ctx, x, y, w, h, radius)
  ctx.fill()
  ctx.beginPath()
  ctx.moveTo(cx - 5, y + h - 0.5)
  ctx.lineTo(cx + 5, y + h - 0.5)
  ctx.lineTo(cx, y + h + tail)
  ctx.closePath()
  ctx.fill()
  ctx.restore()

  ctx.lineWidth = 1
  ctx.strokeStyle = border
  roundRectPath(ctx, x + 0.5, y + 0.5, w - 1, h - 1, radius - 0.5)
  ctx.stroke()

  const priceY = twoLine ? y + 12 : y + h / 2
  ctx.beginPath()
  ctx.fillStyle = dotColor
  ctx.arc(x + padX + dotR, priceY, dotR, 0, Math.PI * 2)
  ctx.fill()

  const textX = x + padX + dotR * 2 + gap
  ctx.font = PILL_FONT
  ctx.textBaseline = 'middle'
  ctx.fillStyle = level === 'go' && label === 'Free' ? freeColor : fg
  ctx.fillText(label, textX, priceY + 0.5)

  if (priv) drawLock(ctx, textX + textW + gap + lockW / 2, priceY, lockColor)

  if (detail) {
    ctx.font = PILL_SUB_FONT
    ctx.fillStyle = muted
    ctx.textBaseline = 'middle'
    ctx.fillText(detail, x + padX, y + 25)
  }

  return { image: ctx.getImageData(0, 0, canvas.width, canvas.height), pixelRatio: PILL_DPR }
}

/* ---------- Cluster bubbles painted onto the map ---------- */
type ClusterTone = 'go' | 'free' | 'neutral'

/** Bake a circular cluster badge (count + tone ring) so we need no external glyph server. */
function makeClusterImage(
  text: string,
  tone: ClusterTone,
  theme: 'light' | 'dark',
): { image: ImageData; pixelRatio: number } {
  const dark = theme === 'dark'
  // Only a genuinely route-ready cluster earns the green accent.
  const ring = tone === 'go' ? (dark ? '#21d277' : '#0d7d40') : (dark ? '#5d6862' : '#c5cbc7')
  const bg = dark ? '#141a17' : '#ffffff'
  const fg = dark ? '#edf1ee' : '#0e1512'
  const DPR = 2
  const len = text.length
  const r = 15 + Math.min(len, 5) * 2.4 // grows with the number of digits
  const pad = 6
  const size = (r + pad) * 2

  const canvas = document.createElement('canvas')
  canvas.width = size * DPR
  canvas.height = size * DPR
  const ctx = canvas.getContext('2d')!
  ctx.scale(DPR, DPR)
  const cx = size / 2
  const cy = size / 2

  // Body with a single soft drop shadow.
  ctx.save()
  ctx.shadowColor = dark ? 'rgba(0,0,0,0.5)' : 'rgba(8,30,20,0.22)'
  ctx.shadowBlur = 7
  ctx.shadowOffsetY = 2
  ctx.beginPath()
  ctx.arc(cx, cy, r, 0, Math.PI * 2)
  ctx.fillStyle = bg
  ctx.fill()
  ctx.restore()

  // Tone ring.
  ctx.lineWidth = 1.6
  ctx.strokeStyle = ring
  ctx.beginPath()
  ctx.arc(cx, cy, r - 1.1, 0, Math.PI * 2)
  ctx.stroke()

  // Count.
  ctx.fillStyle = fg
  ctx.font =
    '700 13.5px "Inter Variable", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(text, cx, cy + 0.5)

  return { image: ctx.getImageData(0, 0, canvas.width, canvas.height), pixelRatio: DPR }
}

/** Add any price-pill images referenced by the current stations that aren't registered yet. */
function ensurePriceIcons(map: maplibregl.Map, stations: Station[], theme: 'light' | 'dark'): void {
  for (const s of stations) {
    const preview = pinPreview(s)
    if (!preview) continue
    const priv = isPrivateAccess(s)
    const id = priceIconId(preview.price, decisionLevel(s), theme, priv, preview.detail)
    if (map.hasImage(id)) continue
    const { image, pixelRatio } = makePriceImage(preview.price, decisionLevel(s), theme, priv, preview.detail)
    map.addImage(id, image, { pixelRatio })
  }
}

/** Register fresh icons, then push the data — keeps the symbol layer warning-free. */
function paintStations(map: maplibregl.Map, stations: Station[], theme: 'light' | 'dark'): void {
  ensurePriceIcons(map, stations, theme)
  const src = map.getSource('stations') as maplibregl.GeoJSONSource | undefined
  src?.setData(toGeoJSON(stations, theme))
  // User-timing mark for QA: when the first charger pins reach the map.
  if (src && stations.length && !performance.getEntriesByName('volta:map-pins').length) performance.mark('volta:map-pins')
}

// Public keyless styles; provider attribution is included in the style's sources.
function unfocusLoadedMap(map: maplibregl.Map): void {
  map.getCanvas().setAttribute('tabindex', '-1')
  for (const el of Array.from(map.getContainer().querySelectorAll<HTMLElement>('button, summary, a[href], [tabindex]'))) {
    el.setAttribute('tabindex', '-1')
  }
}

function basemapStyle(theme: 'light' | 'dark'): string {
  return `https://tiles.openfreemap.org/styles/${theme === 'dark' ? 'dark' : 'positron'}`
}

function styleBasemap(map: maplibregl.Map, theme: 'light' | 'dark'): void {
  if (theme === 'dark') return
  const tokens = getComputedStyle(document.documentElement)
  for (const [id, property, token] of [
    ['water', 'fill-color', '--map-water'],
    ['waterway', 'line-color', '--map-water'],
    ['park', 'fill-color', '--map-park'],
    ['landcover_wood', 'fill-color', '--map-park'],
  ] as const) {
    const value = tokens.getPropertyValue(token).trim()
    // An offline test style or future provider style may not contain this layer.
    if (value && map.getLayer(id)) map.setPaintProperty(id, property, value)
  }
}

function toGeoJSON(stations: Station[], theme: 'light' | 'dark'): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: stations
      .filter((s) => typeof s.lat === 'number' && typeof s.lon === 'number')
      .map((s) => {
        const level = decisionLevel(s)
        const preview = pinPreview(s)
        const free = hasVerifiedPrice(s) && (s.is_free || s.price_min === 0) ? 1 : 0
        const properties: Record<string, unknown> = {
          id: s.id,
          level,
          lead: s.lead ? 1 : 0,
          free,
          priceVal: free ? 0 : (s.price_min ?? 999),
        }
        if (preview) {
          properties.icon = priceIconId(preview.price, level, theme, isPrivateAccess(s), preview.detail)
          properties.prio = priceSort(s, preview.price)
        }
        return {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
          properties,
        }
      }),
  } as FeatureCollection
}

const escapeHtml = (s: string): string =>
  s.replace(
    /[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string,
  )

/** Click card: this charger only, anchored to the pin — not a side-list highlight. */
function popupHTML(st: Station): string {
  const verifiedPrice = hasVerifiedPrice(st)
  const price = verifiedPrice ? priceText(st) : 'Unverified'
  const unit = verifiedPrice ? priceUnit(st) : ''
  const cs = st.connector_summary
  const total = cs?.total ?? st.total_evse ?? st.connector_count ?? (st.connectors?.length || null)
  let avail: string
  const stale = st.cache_stale === true
  const notLive = st.lead || stale
  if (!notLive && cs && cs.available != null) {
    const parts = [`${cs.available} free`]
    if ((cs.busy ?? 0) > 0) parts.push(`${cs.busy} in use`)
    if ((cs.out_of_service ?? 0) > 0) parts.push(`${cs.out_of_service} out`)
    avail = parts.join(' · ')
  } else if (!notLive && st.available != null && total != null) avail = `${st.available}/${total} free`
  else if (notLive) avail = 'Not live'
  else avail = String(st.status || 'Unknown')

  const points = total != null ? `${total} charger${total === 1 ? '' : 's'}` : 'Unknown'
  const maxKw = sanePowerKw(cs?.max_power_kw ?? st.max_power_kw ?? null)
  const speed = maxKw != null ? powerText(maxKw) + (st.fast || maxKw >= 43 ? ' · Fast' : '') : st.fast ? 'Fast' : 'Unknown'
  const connectors =
    st.connector_labels && st.connector_labels.length
      ? st.connector_labels.join(', ')
      : (st.connectors || [])
          .map((c) => c.label || c.type)
          .filter(Boolean)
          .join(', ') || 'Unknown'
  const accessNote = privateAccess(st)?.label
  const maps = notLive ? null : mapsUrl(st)
  const start = notLive ? null : remoteStartAction(st)
  const handoff = notLive || start ? null : providerHandoff(st)
  const source = publicWebUrl(st.primary_source?.url || st.source_url)
  const provenance = (st.primary_source?.name || st.source_label || st.source || '').trim()
  const kicker = stale ? 'Cached · not live' : st.lead ? 'Not live' : avail
  const kickerColor = notLive ? 'var(--muted)' : 'var(--brand-strong)'
  const city = [st.address, st.city].filter(Boolean).join(' · ')
  const access = notLive ? 'Verify on site' : accessNote || 'Public'
  const row = (label: string, value: string) =>
    `<div style="display:grid;grid-template-columns:72px 1fr;gap:6px 8px;font-size:12px;line-height:1.4;padding:4px 0"><span style="color:var(--muted)">${escapeHtml(label)}</span><span style="font-weight:700;color:var(--fg);overflow-wrap:anywhere">${escapeHtml(value)}</span></div>`

  const btn = (href: string, label: string, primary: boolean) =>
    `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer" style="display:inline-flex;align-items:center;justify-content:center;flex:1;min-height:40px;padding:8px 10px;border-radius:10px;font-size:12px;font-weight:650;text-align:center;text-decoration:none;${
      primary
        ? 'background:var(--brand);color:var(--brand-contrast)'
        : 'border:1px solid var(--border);color:var(--fg);background:var(--surface-2)'
    }">${escapeHtml(label)}</a>`

  const actionMarkup = notLive
    ? source ? btn(source, 'Source', true) : ''
    : (maps ? btn(maps, 'Open in Maps', true) : '') +
      (start
        ? btn(start.href, start.label, false)
        : handoff
        ? btn(handoff.href, handoff.label, false)
        : source
        ? btn(source, 'Source', false)
        : '')

  return (
    `<div class="volta-pin-card" data-station-id="${escapeHtml(st.id)}" style="min-width:0;width:min(268px,calc(100vw - 72px));overflow-wrap:anywhere">` +
    `<div style="display:flex;align-items:center;gap:6px;text-transform:uppercase;letter-spacing:.08em;font-size:10px;font-weight:800;color:${kickerColor};margin-bottom:6px">${escapeHtml(kicker)}</div>` +
    `<div style="font-weight:750;font-size:16px;line-height:1.2;letter-spacing:-.02em;margin-bottom:4px">${escapeHtml(stationTitle(st))}</div>` +
    (city ? `<div style="font-size:11px;color:var(--muted);margin-bottom:8px">${escapeHtml(city)}</div>` : '') +
    `<div style="border-top:1px solid var(--border);border-bottom:1px solid var(--border);padding:8px 0;margin-bottom:8px">` +
    row('Price', unit ? `${price} ${unit}` : price) +
    row('Speed', speed) +
    row('Plugs', connectors) +
    row('Chargers', points) +
    row('Access', access) +
    (provenance ? row('Source', provenance) : '') +
    `</div>` +
    `<p style="font-size:11px;line-height:1.4;color:var(--muted);margin:0 0 10px">${
      stale
        ? 'Cached evidence — not live. Refresh or check source before driving.'
        : st.lead
        ? 'Source-backed lead. Confirm price and access before driving.'
        : 'Live public data. Provider app and onsite signs still win.'
    }</p>` +
    (actionMarkup ? `<div style="display:flex;gap:6px">${actionMarkup}</div>` : '') +
    `</div>`
  )
}

export interface FlyTarget {
  lat: number
  lon: number
  bbox?: Bbox | null
  zoom?: number
  nonce?: number
}

export const MapView = memo(function MapView({
  stations,
  selectedId,
  onSelect,
  theme,
  userCoords,
  onViewportChange,
  onUserViewportIntent,
  flyTarget,
  initialView,
}: {
  stations: Station[]
  selectedId?: string | null
  onSelect: (id: string | null) => void
  theme: 'light' | 'dark'
  userCoords?: Coords | null
  onViewportChange?: (vp: Viewport) => void
  onUserViewportIntent?: () => void
  flyTarget?: FlyTarget | null
  initialView?: FlyTarget | null
}) {
  const [mapUnavailable, setMapUnavailable] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const userMarkerRef = useRef<maplibregl.Marker | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const synchronizingPopupRef = useRef(false)
  const selectedIdRef = useRef(selectedId)
  selectedIdRef.current = selectedId
  const flyTargetRef = useRef(flyTarget)
  flyTargetRef.current = flyTarget
  const readyRef = useRef(false)
  const boundRef = useRef(false)
  // Keep latest values reachable from once-bound map event handlers.
  const stationsRef = useRef(stations)
  const stationsByIdRef = useRef(new Map(stations.map((station) => [station.id, station])))
  const themeRef = useRef(theme)
  const appliedThemeRef = useRef(theme)
  const onViewportRef = useRef(onViewportChange)
  const onUserViewportIntentRef = useRef(onUserViewportIntent)
  onUserViewportIntentRef.current = onUserViewportIntent
  const onSelectRef = useRef(onSelect)
  const userViewportIntentRef = useRef<number | null>(null)
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])
  useEffect(() => {
    stationsRef.current = stations
    stationsByIdRef.current = new Map(stations.map((station) => [station.id, station]))
  }, [stations])
  useEffect(() => {
    themeRef.current = theme
  }, [theme])
  useEffect(() => {
    onViewportRef.current = onViewportChange
  }, [onViewportChange])

  const reconcileSelection = (map: maplibregl.Map): void => {
    const id = selectedIdRef.current
    const st = id ? stationsByIdRef.current.get(id) : undefined
    const popup = popupRef.current

    // Cleanup is state reconciliation, not map painting: it must run while a
    // replacement style is still loading.
    if (!st) {
      // A controlled update may be resolving an alias or showing an unavailable
      // reference. Removing its old popup is not a user dismissal of that reference.
      synchronizingPopupRef.current = true
      try { popup?.remove() } finally { synchronizingPopupRef.current = false }
      if (readyRef.current && map.getLayer('station-selected')) {
        map.setFilter('station-selected', ['all', ['!', ['has', 'point_count']], ['==', ['get', 'id'], '__none__']] as unknown as maplibregl.FilterSpecification)
      }
      return
    }
    if (!readyRef.current) return
    if (map.getLayer('station-selected')) {
      map.setFilter('station-selected', ['all', ['!', ['has', 'point_count']], ['==', ['get', 'id'], id]] as unknown as maplibregl.FilterSpecification)
    }
    if (popup) {
      popup.setLngLat([st.lon, st.lat]).setHTML(popupHTML(st))
      if (!popup.isOpen()) {
        synchronizingPopupRef.current = true
        try { popup.addTo(map) } finally { synchronizingPopupRef.current = false }
      }
    }
  }

  const addStationLayers = (map: maplibregl.Map, theme: 'light' | 'dark') => {
    if (map.getSource('stations')) return
    map.addSource('stations', {
      type: 'geojson',
      data: toGeoJSON([], theme),
      cluster: true,
      clusterRadius: 56,
      clusterMaxZoom: 13,
      clusterProperties: {
        go: ['max', ['case', ['==', ['get', 'level'], 'go'], 1, 0]],
        free: ['max', ['get', 'free']],
        minPrice: ['min', ['get', 'priceVal']],
      },
    })
    const c = COLORS[theme]
    const colorExpr: maplibregl.DataDrivenPropertyValueSpecification<string> = [
      'match',
      ['get', 'level'],
      'go',
      c.go,
      'check',
      c.check,
      'verify',
      c.verify,
      '#8b948f',
    ] as unknown as maplibregl.DataDrivenPropertyValueSpecification<string>

    const notCluster = ['!', ['has', 'point_count']] as unknown as maplibregl.FilterSpecification

    // A count badge is enough; an unverified free tariff must not earn a green halo.
    map.addLayer({
      id: 'clusters',
      type: 'symbol',
      source: 'stations',
      filter: ['has', 'point_count'],
      layout: {
        'icon-image': [
          'concat',
          'cluster:',
          theme,
          ':',
          ['case', ['>', ['get', 'go'], 0], 'go', ['>', ['get', 'free'], 0], 'free', 'neutral'],
          ':',
          ['get', 'point_count_abbreviated'],
        ] as unknown as maplibregl.DataDrivenPropertyValueSpecification<string>,
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
      },
    })

    map.addLayer({
      id: 'station-glow',
      type: 'circle',
      source: 'stations',
      filter: notCluster,
      paint: {
        'circle-color': colorExpr,
        'circle-opacity': 0.16,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 9, 13, 16, 16, 24],
      },
    })
    map.addLayer({
      id: 'station-dot',
      type: 'circle',
      source: 'stations',
      filter: notCluster,
      paint: {
        'circle-color': colorExpr,
        'circle-radius': ['case', ['==', ['get', 'lead'], 1], 4.5, 6],
        'circle-stroke-width': 2,
        'circle-stroke-color': theme === 'dark' ? '#0b0f0d' : '#ffffff',
      },
    })
    map.addLayer({
      id: 'station-selected',
      type: 'circle',
      source: 'stations',
      filter: ['all', notCluster, ['==', ['get', 'id'], selectedIdRef.current || '__none__']] as unknown as maplibregl.FilterSpecification,
      paint: {
        'circle-color': 'transparent',
        'circle-radius': 11,
        'circle-stroke-width': 3,
        // Selection is an interface state, so it uses ink rather than the Go-now green.
        'circle-stroke-color': theme === 'dark' ? '#edf1ee' : '#0e1512',
      },
    })
    // Price bubbles painted on top of the dots. Collision detection auto-declutters
    // (cheaper/live/go-now win via symbol-sort-key); more reveal as you zoom in.
    map.addLayer({
      id: 'station-price',
      type: 'symbol',
      source: 'stations',
      filter: ['all', notCluster, ['has', 'icon']] as unknown as maplibregl.FilterSpecification,
      minzoom: 8.5,
      layout: {
        'icon-image': ['get', 'icon'],
        'icon-anchor': 'bottom',
        'icon-offset': [0, PILL_MARGIN],
        'icon-allow-overlap': false,
        'icon-ignore-placement': false,
        'icon-padding': 2,
        'symbol-sort-key': ['get', 'prio'],
      },
    })
  }

  // Bind delegated interaction handlers ONCE; they survive basemap (style) swaps.
  const bindInteractions = (map: maplibregl.Map) => {
    if (boundRef.current) return
    boundRef.current = true
    const popup = new maplibregl.Popup({
      closeButton: true,
      closeOnClick: false,
      offset: 22,
      maxWidth: '320px',
      className: 'volta-popup',
      // No fixed anchor: MapLibre then picks the side from the padding below, so a popup
      // near the docked panel shifts clear of it instead of sitting centred over the edge.
      padding: MAP_PADDING,
    })
    popupRef.current = popup
    popup.on('close', () => {
      if (synchronizingPopupRef.current) return
      if (popupRef.current === popup) onSelectRef.current(null)
    })

    const openStation = (id: string | undefined, lngLat?: maplibregl.LngLatLike) => {
      if (!id) return
      const st = stationsByIdRef.current.get(id)
      if (!st) {
        popup.remove()
        onSelectRef.current(null)
        return
      }
      popup.setLngLat(lngLat ?? [st.lon, st.lat]).setHTML(popupHTML(st))
      if (!popup.isOpen()) {
        synchronizingPopupRef.current = true
        try { popup.addTo(map) } finally { synchronizingPopupRef.current = false }
      }
      onSelectRef.current(id)
    }

    for (const layer of ['station-dot', 'station-price']) {
      map.on('click', layer, (e) => {
        e.originalEvent.stopPropagation()
        const id = e.features?.[0]?.properties?.id as string | undefined
        openStation(id, e.lngLat)
      })
      map.on('mouseenter', layer, () => {
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', layer, () => {
        map.getCanvas().style.cursor = ''
      })
    }
    map.on('click', (e) => {

      const hits = map.queryRenderedFeatures(e.point, { layers: ['station-dot', 'station-price'] })
      if (hits.length) return
      popup.remove()
      onSelectRef.current(null)
    })

    // Cluster click → zoom to expand.
    map.on('click', 'clusters', (e) => {
      const f = e.features?.[0]
      const clusterId = f?.properties?.cluster_id
      const src = map.getSource('stations') as maplibregl.GeoJSONSource | undefined
      if (clusterId == null || !src) return
      userViewportIntentRef.current = performance.now()
      onUserViewportIntentRef.current?.()
      src
        .getClusterExpansionZoom(clusterId)
        .then((zoom) => {
          const coords = (f!.geometry as Point).coordinates as [number, number]
          map.easeTo({
            center: coords,
            zoom: (zoom ?? map.getZoom()) + 0.25,
            duration: prefersReducedMotion() ? 0 : 600,
          })
        })
        .catch(() => {})
    })
    map.on('mouseenter', 'clusters', () => {
      map.getCanvas().style.cursor = 'pointer'
    })
    map.on('mouseleave', 'clusters', () => {
      map.getCanvas().style.cursor = ''
    })
  }

  // Init once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const start = initialView
    let map: maplibregl.Map
    try {
      map = new maplibregl.Map({
        container: containerRef.current,
        center: start ? [start.lon, start.lat] : [-3.7038, 40.4168],
        zoom: start?.zoom ?? 5.2,
        attributionControl: { compact: true },
        dragRotate: false,
      })
    } catch (error) {
      // v6 requires WebGL2. Unsupported devices keep the full list/search path;
      // other initialization errors still reach the existing map error boundary.
      if (!(error instanceof maplibregl.GPUInitializationError)) throw error
      setMapUnavailable(true)
      return
    }
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')

    map.setPadding(MAP_PADDING)
    const observer = new ResizeObserver(() => map.resize())
    observer.observe(containerRef.current)

    // The map wrapper is an aria-hidden backdrop enhancement; its internals must never enter
    // the keyboard tab order (focusable-inside-aria-hidden is an a11y defect, and the canvas +
    // zoom/attribution buttons stole tab stops 1-4 before "Use my location" — qa_focus_walk).
    // Run synchronously after construction AND again on load (late-added controls).
    unfocusLoadedMap(map)
    // Vector-source attribution can insert links after style.load. Keep those late
    // additions out of the aria-hidden map's keyboard order as well.
    const focusObserver = new MutationObserver(() => unfocusLoadedMap(map))
    focusObserver.observe(map.getContainer(), { childList: true, subtree: true })

    // Bake cluster badges on demand (keyless — no glyph server needed).
    map.setMissingStyleImageResolver((id) => {
      if (!id || !id.startsWith('cluster:') || map.hasImage(id)) return
      const parts = id.split(':') // cluster:theme:tone:abbrev
      const { image, pixelRatio } = makeClusterImage(
        parts[3] || '',
        (parts[2] as ClusterTone) || 'neutral',
        (parts[1] as 'light' | 'dark') || 'light',
      )
      if (!map.hasImage(id)) map.addImage(id, image, { pixelRatio })
    })

    // Debounced viewport emit so the list/fetch can follow the map.
    let moveTimer: ReturnType<typeof setTimeout> | undefined
    const emitViewport = () => {
      const b = map.getBounds()
      const ctr = map.getCenter()
      onViewportRef.current?.({
        bbox: { south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() },
        zoom: map.getZoom(),
        center: { lat: ctr.lat, lon: ctr.lng },
        userInitiated: userViewportIntentRef.current !== null,
        userInitiatedAt: userViewportIntentRef.current ?? undefined,
      })
      userViewportIntentRef.current = null
    }
    const markUserViewportIntent = (event: unknown) => {
      if ((event as { originalEvent?: unknown })?.originalEvent) {
        // A report still pending from an earlier camera change would fire mid-gesture,
        // spending this gesture's intent on an in-between viewport. The gesture's own
        // moveend reports the settled view.
        clearTimeout(moveTimer)
        userViewportIntentRef.current = performance.now()
        // GPS can resolve before the debounced viewport report. Yield ownership
        // as soon as the driver starts a newer gesture.
        onUserViewportIntentRef.current?.()
      }
    }
    map.on('dragstart', markUserViewportIntent)
    map.on('zoomstart', markUserViewportIntent)
    map.on('rotatestart', markUserViewportIntent)
    map.on('pitchstart', markUserViewportIntent)
    map.on('moveend', () => {
      clearTimeout(moveTimer)
      moveTimer = setTimeout(emitViewport, 250)
    })

    map.on('style.load', () => {
      readyRef.current = true
      styleBasemap(map, themeRef.current)
      unfocusLoadedMap(map)
      addStationLayers(map, themeRef.current)
      bindInteractions(map)
      paintStations(map, stationsRef.current, themeRef.current)
      // The selection effect may have run while ready=false. Reconcile from
      // live refs so style completion cannot resurrect an obsolete card.
      reconcileSelection(map)
    })
    map.on('load', () => {
      // Region/search choices made before the lazy map loads own the first camera.
      const target = flyTargetRef.current ?? start
      if (target?.bbox) {
        map.fitBounds(
          [
            [target.bbox.west, target.bbox.south],
            [target.bbox.east, target.bbox.north],
          ],
          { padding: MAP_PADDING, maxZoom: 13, duration: 0 },
        )
      }
      if (target && !target.bbox) {
        map.jumpTo({ center: [target.lon, target.lat], zoom: target.zoom ?? 13 })
      }
      // Use the live ref, not the first-render closure: data may have already arrived.
      paintStations(map, stationsRef.current, themeRef.current)
      emitViewport()
    })
    map.setStyle(basemapStyle(theme), { transformStyle: guardBasemapShieldFilters })
    return () => {
      clearTimeout(moveTimer)
      focusObserver.disconnect()
      observer.disconnect()
      const closingPopup = popupRef.current
      popupRef.current = null
      closingPopup?.remove()
      map.remove()
      mapRef.current = null
      readyRef.current = false
      boundRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A new theme may arrive before the previous remote style finishes loading.
  // MapLibre owns cancellation; one style.load listener restores the current data.
  useEffect(() => {
    const map = mapRef.current
    if (!map || appliedThemeRef.current === theme) return
    appliedThemeRef.current = theme
    // A transformed swap otherwise waits for the previous style.load, which may
    // never arrive after a failed initial request. The newer theme owns recovery.
    if (!readyRef.current) map.setStyle(null)
    readyRef.current = false
    map.setStyle(basemapStyle(theme), { transformStyle: guardBasemapShieldFilters })
  }, [theme])

  // Data change. Theme transitions are handled by the theme effect (which rebuilds the
  // style first), so theme is intentionally excluded here to avoid painting mid-swap.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    paintStations(map, stations, theme)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stations])

  // Selection change -> highlight filter. Kept on [selectedId, stations]: cheap, and correctly
  // wants to re-assert the highlight against whatever paintStations most recently painted.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    reconcileSelection(map)
  }, [selectedId, stations])

  // Selection change -> fly camera. Deliberately depends ONLY on selectedId, NOT stations: the
  // 60s live poll churns the `stations` array reference (new array, same values) without the
  // selection changing, and that must never re-center/re-zoom the camera over the driver's own
  // pan/zoom (CORR-08). Reads the target station from the ref (always current) rather than from
  // `stations` itself, so this effect never needs that dependency at all.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !readyRef.current) return
    const st = selectedId ? stationsByIdRef.current.get(selectedId) : undefined
    const c = map.getCenter()
    if (st) {
      const alreadyClose = Math.abs(c.lat - st.lat) < 0.002 && Math.abs(c.lng - st.lon) < 0.002
      if (!alreadyClose || map.getZoom() < 13.2) {
        map.flyTo({
          center: [st.lon, st.lat],
          zoom: Math.max(map.getZoom(), 14),
          speed: 0.8,
          ...(prefersReducedMotion() ? { duration: 0 } : null),
        })
      }
    }
  }, [selectedId])

  // Place-search / overview jump.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !flyTarget) return
    userViewportIntentRef.current = null
    const reduce = prefersReducedMotion()
    if (flyTarget.bbox) {
      map.fitBounds(
        [
          [flyTarget.bbox.west, flyTarget.bbox.south],
          [flyTarget.bbox.east, flyTarget.bbox.north],
        ],
        { padding: MAP_PADDING, maxZoom: 14, duration: reduce ? 0 : 800 },
      )
    } else {
      // Same constraint as the selection fly: only spread `duration` in when reducing motion.
      map.flyTo({
        center: [flyTarget.lon, flyTarget.lat],
        zoom: flyTarget.zoom ?? 13,
        speed: 0.9,
        ...(reduce ? { duration: 0 } : null),
      })
    }
  }, [flyTarget])

  // User location marker only. The camera move belongs to App's location-success handler
  // via flyTarget — one camera owner per user intent, so nothing here fights that animation.
  // (The historical crash on this path was the duration:undefined coercion documented at the
  // flyTo call sites above, not the marker.)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !userCoords) return
    if (!userMarkerRef.current) {
      const el = document.createElement('div')
      el.className = 'user-location-marker'
      userMarkerRef.current = new maplibregl.Marker({ element: el })
    }
    userMarkerRef.current.setLngLat([userCoords.lon, userCoords.lat]).addTo(map)
  }, [userCoords])

  if (mapUnavailable) {
    return (
      <div className="discovery-map flex items-center justify-center bg-surface-2 px-6" role="status">
        <p className="max-w-xs text-center text-sm text-muted">Map unavailable on this device. Search and the charger list still work.</p>
      </div>
    )
  }

  return (
    <div className="discovery-map bg-surface-2" aria-hidden="true">
      <div ref={containerRef} className="h-full w-full" />
    </div>
  )
})
