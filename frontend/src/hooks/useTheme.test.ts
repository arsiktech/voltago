import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyTheme } from './useTheme'
import indexHtml from '../../index.html?raw'

/** The two places a theme lands: <html data-theme> and <meta name="color-scheme">. */
function fakeDocument() {
  const meta = {
    content: 'light dark',
    setAttribute(name: string, value: string) {
      if (name === 'content') this.content = value
    },
  }
  const documentElement = { dataset: {} as Record<string, string> }
  const querySelector = (selector: string) => (selector === 'meta[name="color-scheme"]' ? meta : null)
  return { meta, documentElement, querySelector }
}

/** The body of the one attribute-less inline script in index.html: the pre-paint theme script. */
function prePaintScript(): string {
  const open = '<script>'
  const start = indexHtml.indexOf(open)
  const end = indexHtml.indexOf('</script>', start)
  if (start < 0 || end < 0) throw new Error('index.html has no inline pre-paint script')
  return indexHtml.slice(start + open.length, end)
}

/** Run the pre-paint script from index.html against a fake browser. */
function runPrePaint(options: { stored?: string | null; prefersDark?: boolean; storageBlocked?: boolean }) {
  const script = prePaintScript()
  const doc = fakeDocument()
  const localStorage = {
    getItem: () => {
      if (options.storageBlocked) throw new Error('blocked')
      return options.stored ?? null
    },
  }
  const window = { matchMedia: () => ({ matches: options.prefersDark === true }) }
  new Function('document', 'localStorage', 'window', script)(doc, localStorage, window)
  return { theme: doc.documentElement.dataset.theme, colorScheme: doc.meta.content }
}

describe('theme and browser color scheme stay in step', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('names the chosen theme in the color-scheme meta when the user toggles', () => {
    const doc = fakeDocument()
    vi.stubGlobal('document', doc)

    applyTheme('dark')
    expect(doc.documentElement.dataset.theme).toBe('dark')
    expect(doc.meta.content).toBe('dark')

    applyTheme('light')
    expect(doc.documentElement.dataset.theme).toBe('light')
    expect(doc.meta.content).toBe('light')
  })

  it('applies a stored theme before paint even when the OS prefers the other one', () => {
    expect(runPrePaint({ stored: 'light', prefersDark: true })).toEqual({ theme: 'light', colorScheme: 'light' })
    expect(runPrePaint({ stored: 'dark', prefersDark: false })).toEqual({ theme: 'dark', colorScheme: 'dark' })
  })

  it('follows the OS before paint when nothing is stored', () => {
    expect(runPrePaint({ prefersDark: true })).toEqual({ theme: 'dark', colorScheme: 'dark' })
    expect(runPrePaint({ prefersDark: false })).toEqual({ theme: 'light', colorScheme: 'light' })
  })

  it('falls back to light for both when storage is blocked', () => {
    expect(runPrePaint({ storageBlocked: true, prefersDark: true })).toEqual({ theme: 'light', colorScheme: 'light' })
  })
})
