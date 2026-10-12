const CACHE_PREFIX = 'volta-offline-'
const CACHE_VERSION = `${CACHE_PREFIX}v4`
const SHELL_CACHE = `${CACHE_VERSION}-shell`
const OVERVIEW_CACHE = `${CACHE_VERSION}-overview`
const BUILD_MANIFEST_URL = '/.vite/manifest.json'
// Keep the offline snapshot keyed to the actual broad overview request. The UI's
// default has no price chip selected, so api.ts sends max_price=5.0; caching the old
// ≤ €0.25 URL left a controlled offline reload with no matching fallback at all.
const DEFAULT_OVERVIEW_URL = '/api/chargers?include_leads=true&max_price=5.0&overview=true&compact=true'
const SHELL_URLS = [
  '/',
  '/manifest.webmanifest',
  '/volta-icon.svg',
  '/brand/volta-icon.png',
]
const OFFLINE_WARNING = 'Offline — showing previously cached source-backed leads. Nothing is live.'

async function putBestEffort(cache, key, response) {
  try {
    await cache.put(key, response)
  } catch {
    // Storage quota or private-mode restrictions must never turn a successful online
    // response into a failed product request. Offline support simply remains unavailable.
  }
}

async function openCacheBestEffort(name) {
  try {
    return await caches.open(name)
  } catch {
    // An already-active worker must stay transparent when Cache Storage is unavailable
    // or corrupt. Healthy online requests continue network-only in that environment.
    return null
  }
}

function buildAssetUrls(manifest) {
  const urls = new Set()
  for (const entry of Object.values(manifest || {})) {
    if (!entry || typeof entry !== 'object') continue
    for (const value of [entry.file, ...(entry.css || []), ...(entry.assets || [])]) {
      if (typeof value === 'string' && value) urls.add(value.startsWith('/') ? value : `/${value}`)
    }
  }
  return [...urls]
}

async function fetchIntoCache(cache, url) {
  try {
    const response = await fetch(url, { cache: 'no-store' })
    if (response.ok) await putBestEffort(cache, url, response.clone())
  } catch {
    // Installation remains non-blocking when one optional shell asset is unavailable.
  }
}

async function primeShell() {
  const cache = await caches.open(SHELL_CACHE)
  const urls = new Set(SHELL_URLS)
  try {
    const response = await fetch(BUILD_MANIFEST_URL, { cache: 'no-store' })
    if (response.ok) {
      await putBestEffort(cache, BUILD_MANIFEST_URL, response.clone())
      for (const url of buildAssetUrls(await response.json())) urls.add(url)
    }
  } catch {
    // The fixed shell still installs; a later controlled online request can fill assets.
  }
  await Promise.all([...urls].map((url) => fetchIntoCache(cache, url)))
}

async function primeDefaultOverview() {
  const cache = await caches.open(OVERVIEW_CACHE)
  const request = new Request(DEFAULT_OVERVIEW_URL, { headers: { Accept: 'application/json' } })
  try {
    const response = await fetch(request)
    if (response.ok) await putBestEffort(cache, overviewCacheKey(request), response.clone())
  } catch {
    // The app remains online-capable even if the optional offline lead snapshot cannot prime.
  }
}

async function installOfflineBundle() {
  await Promise.all([primeShell(), primeDefaultOverview()])
  await self.skipWaiting()
}

self.addEventListener('install', (event) => {
  event.waitUntil(installOfflineBundle())
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith(CACHE_PREFIX) && !key.startsWith(CACHE_VERSION))
          .map((key) => caches.delete(key)),
      ),
    ),
  )
  event.waitUntil(self.clients.claim())
})

function isDefaultOverviewRequest(request) {
  if (request.method !== 'GET') return false
  const url = new URL(request.url)
  if (url.origin !== self.location.origin || url.pathname !== '/api/chargers') return false
  if (
    url.searchParams.get('overview') !== 'true' ||
    url.searchParams.get('compact') !== 'true' ||
    url.searchParams.get('include_leads') !== 'true' ||
    url.searchParams.get('max_price') !== '5.0' ||
    url.searchParams.has('bbox')
  ) {
    return false
  }
  const allowed = new Set(['overview', 'compact', 'include_leads', 'max_price', 'force'])
  return [...url.searchParams.keys()].every((key) => allowed.has(key))
}

function overviewCacheKey(request) {
  const url = new URL(request.url)
  url.searchParams.delete('force')
  return new Request(url.toString(), { method: 'GET', headers: { Accept: 'application/json' } })
}

async function overviewResponse(request) {
  const cache = await openCacheBestEffort(OVERVIEW_CACHE)
  const cacheKey = overviewCacheKey(request)
  try {
    const response = await fetch(request)
    if (!response.ok) throw new Error(`overview response ${response.status}`)
    if (cache) await putBestEffort(cache, cacheKey, response.clone())
    return response
  } catch (error) {
    if (!cache) throw error
    const cached = await cache.match(cacheKey)
    if (!cached) throw error
    const payload = await cached.json()
    const cachedDate = Date.parse(cached.headers.get('date') || '')
    const ageSeconds = Number.isFinite(cachedDate) ? Math.max(0, (Date.now() - cachedDate) / 1000) : undefined
    const coverage = payload.meta?.coverage || {}
    const offlinePayload = {
      ...payload,
      stations: [],
      warning: OFFLINE_WARNING,
      cache: {
        ...(payload.cache || {}),
        hit: true,
        stale: true,
        offline: true,
        ...(ageSeconds === undefined ? {} : { age_seconds: Math.round(ageSeconds) }),
      },
      meta: {
        ...(payload.meta || {}),
        mode: 'overview',
        offline: true,
        coverage: {
          ...coverage,
          mode: 'overview',
          is_degraded: true,
          live_route_ready: 0,
          warning: OFFLINE_WARNING,
        },
      },
    }
    return new Response(JSON.stringify(offlinePayload), {
      status: 200,
      headers: {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Volta-Offline': '1',
      },
    })
  }
}

async function navigationResponse(request) {
  const cache = await openCacheBestEffort(SHELL_CACHE)
  try {
    const response = await fetch(request)
    if (isCacheableShellNavigation(request, response) && cache) {
      const navigationKey = new Request('/', { method: 'GET' })
      await putBestEffort(cache, navigationKey, response.clone())
    }
    return response
  } catch (error) {
    if (!cache) throw error
    const cached = await cache.match('/')
    if (cached) return cached
    throw error
  }
}

function isCacheableShellNavigation(request, response) {
  const url = new URL(request.url)
  const contentType = (response.headers.get('content-type') || '').toLowerCase()
  return (
    request.mode === 'navigate' &&
    url.origin === self.location.origin &&
    !url.pathname.startsWith('/api/') &&
    response.ok &&
    contentType.includes('text/html')
  )
}

async function staticResponse(request) {
  const cache = await openCacheBestEffort(SHELL_CACHE)
  if (cache) {
    const cached = await cache.match(request)
    if (cached) return cached
  }
  const response = await fetch(request)
  if (response.ok && cache) {
    const staticKey = request
    await putBestEffort(cache, staticKey, response.clone())
  }
  return response
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)

  if (url.origin === self.location.origin && url.pathname === '/api/chargers') {
    if (isDefaultOverviewRequest(request)) event.respondWith(overviewResponse(request))
    return
  }

  // API routes are always network-owned. In particular, a browser navigation to
  // /api/health must never receive the cached SPA or overwrite the '/' shell entry.
  if (url.origin === self.location.origin && url.pathname.startsWith('/api/')) return

  if (request.mode === 'navigate') {
    event.respondWith(navigationResponse(request))
    return
  }

  if (
    url.origin === self.location.origin &&
    ['script', 'worker', 'style', 'image', 'font', 'manifest'].includes(request.destination)
  ) {
    event.respondWith(staticResponse(request))
  }
})
