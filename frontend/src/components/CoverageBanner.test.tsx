import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { CoverageBanner } from './CoverageBanner'

describe('driver-facing coverage guidance', () => {
  it('keeps healthy coverage quiet', () => {
    expect(renderToStaticMarkup(<CoverageBanner coverage={{ mode: 'standard' }} />)).toBe('')
  })

  it('labels stale evidence and gives a complete action without raw diagnostics', () => {
    const markup = renderToStaticMarkup(<CoverageBanner
      cache={{ stale: true }}
      coverage={{ mode: 'degraded', warning: 'HTTPError: request failed /api/chargers?lat=39&lon=-0.3' }}
    />)
    expect(markup).toContain('Showing cached data')
    expect(markup).toContain('Verify the source before driving.')
    expect(markup).not.toContain('HTTPError')
    expect(markup).not.toContain('/api/chargers')
  })

  it('keeps the offline no-live boundary explicit', () => {
    const markup = renderToStaticMarkup(<CoverageBanner cache={{ stale: true, offline: true }} />)
    expect(markup).toContain('Offline')
    expect(markup).toContain('Nothing is live.')
  })

  it('distinguishes limited fresh coverage from cached evidence', () => {
    const markup = renderToStaticMarkup(<CoverageBanner coverage={{ mode: 'degraded' }} />)
    expect(markup).toContain('Limited live coverage')
    expect(markup).not.toContain('Showing cached data')
  })

  it('keeps a bounded upstream continuation warning visible even without a low-count mode', () => {
    const markup = renderToStaticMarkup(<CoverageBanner coverage={{
      partial: true,
      partial_reason: 'fetch_budget',
      warning: 'Live results may be incomplete because the MapaREVE fetch time budget elapsed before every page was received. Refresh or narrow the map before routing.',
    }} />)
    expect(markup).toContain('Live results may be incomplete')
    expect(markup).toContain('fetch time budget elapsed')
  })
})
