import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { FeedbackButton } from '../components/FeedbackButton'
import { sendFeedback } from './feedback-client'
import clientSource from './feedback-client.ts?raw'
import { feedbackButtonEnabled, feedbackEndpoint, feedbackEndpointFrom, feedbackSubmission } from './feedback'

const CLIENT_SHA256 = '285b274f2a7002d14776aa025b32bde7dd7a80cc7dc2ce44e9448d1096e025c5'

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

describe('feedback gate', () => {
  it('vendors the shared client unchanged', async () => {
    expect(await sha256(clientSource)).toBe(CLIENT_SHA256)
  })

  it('stays off unless url, app, and key are all set', () => {
    expect(feedbackEndpointFrom({})).toBeNull()
    expect(feedbackEndpointFrom({ VITE_FEEDBACK_URL: 'http://example.invalid', VITE_FEEDBACK_APP: 'volta' })).toBeNull()
    expect(feedbackEndpointFrom({ VITE_FEEDBACK_URL: 'http://example.invalid', VITE_FEEDBACK_KEY: 'k' })).toBeNull()
    expect(feedbackEndpointFrom({ VITE_FEEDBACK_APP: 'volta', VITE_FEEDBACK_KEY: 'k' })).toBeNull()
    expect(feedbackEndpointFrom({
      VITE_FEEDBACK_URL: '   ',
      VITE_FEEDBACK_APP: 'volta',
      VITE_FEEDBACK_KEY: 'k',
    })).toBeNull()
    expect(feedbackEndpoint()).toBeNull()
    expect(feedbackButtonEnabled(null, true)).toBe(false)
    expect(feedbackButtonEnabled({ url: 'http://example.invalid', app: 'volta', key: 'k' }, false)).toBe(false)
    expect(feedbackButtonEnabled({ url: 'http://example.invalid', app: 'volta', key: 'k' }, undefined)).toBe(false)
    expect(feedbackButtonEnabled({ url: 'http://example.invalid', app: 'volta', key: 'k' }, true)).toBe(true)
  })

  it('trims the endpoint and drops a trailing slash', () => {
    expect(feedbackEndpointFrom({
      VITE_FEEDBACK_URL: ' http://example.invalid/ ',
      VITE_FEEDBACK_APP: ' volta ',
      VITE_FEEDBACK_KEY: ' k ',
    })).toEqual({ url: 'http://example.invalid', app: 'volta', key: 'k' })
  })

  it('renders no button while the gate is closed', () => {
    expect(renderToStaticMarkup(<FeedbackButton theme="light" />)).toBe('')
  })
})

describe('feedback send payload', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  const shot = { dataUrl: 'data:image/jpeg;base64,aGVsbG8=', viewport: { width: 800, height: 600 } }

  it('sends the note, stripped path, mood, and theme, and keeps the screenshot on /', async () => {
    const body = feedbackSubmission({
      note: '  Map pins overlap the list  ',
      path: '/?token=drop#panel',
      theme: 'dark',
      language: 'en_US',
      mood: 'unhappy',
      shot,
      screenshots: true,
      capture: ['/'],
    })
    expect(body).toEqual({
      note: 'Map pins overlap the list',
      path: '/?token=drop#panel',
      locale: 'en-US',
      mood: 'unhappy',
      shot,
      context: { theme: 'dark' },
    })
    expect(body.context).not.toHaveProperty('email')
    expect(body.context).not.toHaveProperty('name')
    expect(body.context).not.toHaveProperty('userId')

    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ id: 'n1' }) }))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('window', { innerWidth: 1280, innerHeight: 800 })
    const result = await sendFeedback(
      { url: 'http://example.invalid', app: 'volta', key: 'publishable' },
      body,
    )
    expect(result).toEqual({ ok: true, imageDropped: false })
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://example.invalid/v1/apps/volta/notes')
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('omit')
    expect(init.headers).toMatchObject({
      'content-type': 'application/json',
      'x-feedback-key': 'publishable',
    })
    const posted = JSON.parse(String(init.body))
    expect(posted).toEqual({
      note: 'Map pins overlap the list',
      path: '/',
      locale: 'en-US',
      mood: 'unhappy',
      viewport: { width: 800, height: 600 },
      screenshot: shot.dataUrl,
      context: { theme: 'dark' },
    })
    expect(posted).not.toHaveProperty('email')
    expect(posted).not.toHaveProperty('name')
    expect(posted).not.toHaveProperty('userId')
    expect(posted).not.toHaveProperty('user')
  })

  it('follows the service capture list, including every page when that list says so', () => {
    const base = {
      note: 'Hello',
      path: '/matrix?x=1',
      theme: 'light' as const,
      shot,
      screenshots: true,
      capture: ['/'],
    }
    expect(feedbackSubmission(base).shot).toBeUndefined()
    expect(feedbackSubmission({ ...base, path: '/', screenshots: false }).shot).toBeUndefined()
    expect(feedbackSubmission({ ...base, path: '/', shot: null }).shot).toBeUndefined()
    for (const path of ['/', '/matrix', '/privacy', '/coverage/']) {
      expect(feedbackSubmission({ ...base, path, capture: ['/*'] }).shot).toBe(shot)
    }
  })
})
