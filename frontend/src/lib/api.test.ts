import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { DEFAULT_FILTERS } from './format'
import {
  chargersQueryKey,
  chargersQueryOptions,
  geocodeQueryOptions,
  chargersUrl,
  getJSON,
  publicTransportErrorMessage,
  TransportError,
} from './api'
import type { Bbox } from '../types'
import indexHtml from '../../index.html?raw'

const first: Bbox = { south: 39.401, west: -0.401, north: 39.481, east: -0.321 }
const neighbor: Bbox = { south: 39.409, west: -0.409, north: 39.489, east: -0.329 }

describe('chargers request identity', () => {
  it('preloads exactly the first overview request from index.html', () => {
    // A drifted href would download the initial area twice or fetch a global catalog.
    const href = indexHtml.match(/<link rel="preload" href="([^"]+)" as="fetch"/)?.[1]?.replaceAll('&amp;', '&')
    expect(href).toBe(chargersUrl({ filters: DEFAULT_FILTERS, overview: true, compact: true,
      bbox: { south: 35.8, west: -9.6, north: 43.9, east: 3.4 } }))
  })

  it('keeps the exact bbox in the client cache key', () => {
    const a = chargersQueryKey({ filters: DEFAULT_FILTERS, bbox: first, overview: false })
    const b = chargersQueryKey({ filters: DEFAULT_FILTERS, bbox: neighbor, overview: false })

    expect(a).not.toEqual(b)
    expect(a).toContain('39.401,-0.401,39.481,-0.321')
    expect(b).toContain('39.409,-0.409,39.489,-0.329')
  })

  it('requests the compact card contract for overview without a cheap-only cap', () => {
    const url = new URL(
      chargersUrl({ filters: DEFAULT_FILTERS, bbox: first, overview: true, compact: true }),
      'http://volta.local',
    )

    expect(url.searchParams.get('overview')).toBe('true')
    expect(url.searchParams.get('compact')).toBe('true')
    expect(url.searchParams.get('max_price')).toBe('5.0')
    expect(url.searchParams.get('bbox')).toBe('39.401,-0.401,39.481,-0.321')
  })
})

describe('selected charging price', () => {
  it.each([0, 0.18, 0.4, 0.65, 1])('sends cap %s and keeps its cache independent', (cap) => {
    const query = { filters: { ...DEFAULT_FILTERS, maxPrice: cap }, overview: true }
    const url = new URL(chargersUrl(query), 'http://volta.local')
    expect(url.searchParams.get('max_price')).toBe(cap.toFixed(2))
    expect(chargersQueryKey(query)).not.toEqual(chargersQueryKey({ filters: DEFAULT_FILTERS, overview: true }))
    expect(chargersQueryKey(query)).toContain(cap)
  })

  it('does not reuse another numeric cap response', () => {
    const key = (maxPrice: number) => chargersQueryKey({ filters: { ...DEFAULT_FILTERS, maxPrice } })
    expect(key(0.18)).not.toEqual(key(0.4))
    expect(key(0.4)).not.toEqual(key(0.65))
  })
})

describe('transport error privacy', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  async function capturedFailure(request: Promise<unknown>): Promise<TransportError> {
    try {
      await request
      throw new Error('request unexpectedly succeeded')
    } catch (error) {
      expect(error).toBeInstanceOf(TransportError)
      return error as TransportError
    }
  }

  it('does not expose a charger request URL or coordinates on HTTP failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503 })))
    const privateUrl = '/api/chargers?bbox=39.401%2C-0.401%2C39.481%2C-0.321&lat=39.47&lon=-0.38'

    const failure = await capturedFailure(getJSON(privateUrl))

    expect(failure.kind).toBe('http')
    expect(failure.status).toBe(503)
    expect(String(failure)).not.toContain('/api/chargers')
    expect(String(failure)).not.toContain('39.47')
    expect(String(failure)).not.toContain('-0.38')
    expect(publicTransportErrorMessage(failure)).toBe(
      'Live data is temporarily unavailable. Check your connection and try again.',
    )
  })

  it('sanitizes a rejected network request without retaining its exception text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('connect failed for /api/geocode?q=exact-home-address')
      }),
    )

    const failure = await capturedFailure(getJSON('/api/geocode?q=exact-home-address'))

    expect(failure.kind).toBe('network')
    expect(String(failure)).not.toContain('exact-home-address')
    expect(publicTransportErrorMessage(failure)).toBe(
      'Live data is temporarily unavailable. Check your connection and try again.',
    )
  })

  it('classifies an aborted private request as a bounded timeout', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }),
      ),
    )

    const request = capturedFailure(getJSON('/api/chargers?lat=39.47&lon=-0.38', 20))
    await vi.advanceTimersByTimeAsync(20)
    const failure = await request

    expect(failure.kind).toBe('timeout')
    expect(String(failure)).not.toContain('39.47')
    expect(String(failure)).not.toContain('-0.38')
  })

  it('sanitizes malformed JSON without retaining parser or request detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError('unexpected token near exact-home-address')
        },
      })),
    )

    const failure = await capturedFailure(getJSON('/api/geocode?q=exact-home-address'))

    expect(failure.kind).toBe('invalid-response')
    expect(failure.status).toBe(200)
    expect(String(failure)).not.toContain('exact-home-address')
  })

  it('sanitizes arbitrary network exception text before rendering', () => {
    const upstream = new Error('Failed GET /api/geocode?q=exact-home-address')
    const message = publicTransportErrorMessage(upstream)

    expect(message).toBe('Live data is temporarily unavailable. Check your connection and try again.')
    expect(message).not.toContain('exact-home-address')
  })
})

describe('transport cancellation lifecycle', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('does not start a request for an already cancelled caller', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const caller = new AbortController()
    caller.abort(new Error('private-home-address'))

    await expect(getJSON('/api/geocode?q=private-home-address', 20, caller.signal))
      .rejects.toMatchObject({ kind: 'cancelled' })
    expect(fetch).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['success', 'http', 'network', 'invalid-response'] as const)(
    'releases its timer and caller listener after %s', async (outcome) => {
      vi.useFakeTimers()
      const caller = new AbortController()
      const remove = vi.spyOn(caller.signal, 'removeEventListener')
      vi.stubGlobal('fetch', vi.fn(async () => {
        if (outcome === 'network') throw new Error('private URL')
        return {
          ok: outcome !== 'http', status: outcome === 'http' ? 503 : 200,
          json: async () => {
            if (outcome === 'invalid-response') throw new SyntaxError('private body')
            return { results: [] }
          },
        }
      }))
      const request = getJSON('/api/geocode?q=private', 20, caller.signal)
      if (outcome === 'success') await expect(request).resolves.toEqual({ results: [] })
      else await expect(request).rejects.toMatchObject({ kind: outcome })
      expect(remove).toHaveBeenCalledWith('abort', expect.any(Function))
      expect(vi.getTimerCount()).toBe(0)
    },
  )

  it.each(['fetch', 'body'] as const)('cancels during %s without retaining a caller reason', async (phase) => {
    vi.useFakeTimers()
    const caller = new AbortController()
    const remove = vi.spyOn(caller.signal, 'removeEventListener')
    let transportSignal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => {
      transportSignal = init.signal as AbortSignal
      const pending = () => new Promise((_resolve, reject) => {
        transportSignal!.addEventListener('abort', () => reject(transportSignal!.reason), { once: true })
      })
      return phase === 'fetch' ? pending() : Promise.resolve({ ok: true, status: 200, json: pending })
    }))
    const request = getJSON('/api/geocode?q=private-home-address', 20, caller.signal)
    const failure = request.catch(error => error as TransportError)
    await Promise.resolve()
    caller.abort(new Error('private-home-address'))
    expect(await failure).toMatchObject({ kind: 'cancelled', status: undefined })
    expect(await failure).not.toHaveProperty('cause')
    expect(String(await failure)).not.toContain('private-home-address')
    expect(String(transportSignal!.reason)).not.toContain('private-home-address')
    expect(transportSignal!.aborted).toBe(true)
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function))
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps the deadline active while decoding the response body', async () => {
    vi.useFakeTimers()
    const caller = new AbortController()
    const remove = vi.spyOn(caller.signal, 'removeEventListener')
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => ({
      ok: true, status: 200,
      json: () => new Promise((_resolve, reject) => {
        init.signal!.addEventListener('abort', () => reject(new DOMException('private body', 'AbortError')))
      }),
    })))
    const failure = getJSON('/api/chargers?lat=39.47', 20, caller.signal).catch(error => error)
    await vi.advanceTimersByTimeAsync(20)
    caller.abort('private late cancellation')
    expect(await failure).toMatchObject({ kind: 'timeout', status: undefined })
    expect(String(await failure)).not.toContain('private')
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function))
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('query observer cancellation', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it.each(['chargers', 'geocode'] as const)('aborts an unused %s query without errors or retries', async (kind) => {
    vi.useFakeTimers()
    const client = new QueryClient({ defaultOptions: { queries: { retry: 2, retryDelay: 5, gcTime: Infinity } } })
    let requestSignal: AbortSignal | undefined
    const fetch = vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      requestSignal = init.signal as AbortSignal
      requestSignal.addEventListener('abort', () => reject(requestSignal!.reason))
    }))
    vi.stubGlobal('fetch', fetch)
    const options = kind === 'chargers'
      ? chargersQueryOptions({ filters: DEFAULT_FILTERS })
      : geocodeQueryOptions('Valencia')
    // Both hooks use these exact options; the observer exercises Query's real lifecycle.
    const observer = new QueryObserver(client, options as ReturnType<typeof chargersQueryOptions>)
    const states: string[] = []
    const unsubscribe = observer.subscribe(result => states.push(result.status))
    expect(fetch).toHaveBeenCalledTimes(1)
    unsubscribe()
    expect(requestSignal!.aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(100)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(states).not.toContain('error')
    expect(client.getQueryState(options.queryKey)).toMatchObject({
      fetchStatus: 'idle', error: null, fetchFailureCount: 0,
    })
    expect(vi.getTimerCount()).toBe(0)
    client.clear()
  })

  it('aborts the previous city on a key switch and only publishes the current city', async () => {
    vi.useFakeTimers()
    const client = new QueryClient({ defaultOptions: { queries: { retry: 2, retryDelay: 5, gcTime: Infinity } } })
    const pending: { signal: AbortSignal; resolve: (value: unknown) => void }[] = []
    const fetch = vi.fn((_url: string, init: RequestInit) => new Promise((resolve, reject) => {
      const signal = init.signal as AbortSignal
      pending.push({ signal, resolve })
      signal.addEventListener('abort', () => reject(signal.reason))
    }))
    vi.stubGlobal('fetch', fetch)
    const oldOptions = geocodeQueryOptions('Valencia')
    const observer = new QueryObserver(client, oldOptions)
    const states: string[] = []
    const unsubscribe = observer.subscribe(result => states.push(result.status))
    observer.setOptions(geocodeQueryOptions('Madrid'))
    expect(pending[0].signal.aborted).toBe(true)
    expect(pending[1].signal.aborted).toBe(false)
    expect(observer.getCurrentResult().data).toBeUndefined()
    pending[1].resolve({ ok: true, status: 200, json: async () => ({ query: 'Madrid', results: [] }) })
    await vi.advanceTimersByTimeAsync(100)
    expect(observer.getCurrentResult()).toMatchObject({ status: 'success', data: { query: 'Madrid' } })
    expect(client.getQueryState(oldOptions.queryKey)).toMatchObject({ error: null, fetchFailureCount: 0, fetchStatus: 'idle' })
    expect(states).not.toContain('error')
    expect(fetch).toHaveBeenCalledTimes(2)
    unsubscribe()
    client.clear()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('starts a new live charger bbox request without waiting for the previous city', async () => {
    vi.useFakeTimers()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
    const started: string[] = []
    const pending: { url: string; signal: AbortSignal; resolve: (value: unknown) => void }[] = []
    const fetch = vi.fn((url: string, init: RequestInit) => new Promise((resolve, reject) => {
      const signal = init.signal as AbortSignal
      started.push(String(url))
      pending.push({ url: String(url), signal, resolve })
      signal.addEventListener('abort', () => reject(signal.reason))
    }))
    vi.stubGlobal('fetch', fetch)
    const oldOptions = chargersQueryOptions({ filters: DEFAULT_FILTERS, bbox: first, overview: false })
    const newOptions = chargersQueryOptions({ filters: DEFAULT_FILTERS, bbox: neighbor, overview: false })
    const observer = new QueryObserver(client, oldOptions)
    const t0 = performance.now()
    observer.subscribe(() => {})
    expect(fetch).toHaveBeenCalledTimes(1)
    observer.setOptions(newOptions)
    const switchMs = performance.now() - t0
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(pending[0].signal.aborted).toBe(true)
    expect(pending[1].signal.aborted).toBe(false)
    expect(decodeURIComponent(started[1])).toContain('39.409,-0.409,39.489,-0.329')
    expect(started[1]).not.toEqual(started[0])
    expect(switchMs).toBeLessThan(50)
    pending[1].resolve({
      ok: true,
      status: 200,
      json: async () => ({ stations: [{ id: 'qa:neighbor', name: 'Neighbor' }], leads: [] }),
    })
    await vi.advanceTimersByTimeAsync(20)
    expect(observer.getCurrentResult()).toMatchObject({
      status: 'success',
      data: { stations: [{ id: 'qa:neighbor' }] },
    })
    expect(client.getQueryState(oldOptions.queryKey)?.fetchStatus).toBe('idle')
    client.clear()
  })
})
