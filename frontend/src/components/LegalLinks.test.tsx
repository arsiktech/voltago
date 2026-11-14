import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import appSource from '../App.tsx?raw'
import privacyPage from '../../public/privacy/index.html?raw'
import supportPage from '../../public/support/index.html?raw'
import termsPage from '../../public/terms/index.html?raw'
import { LEGAL_LINKS } from '../lib/legalLinks'
import { LegalLinks } from './LegalLinks'

const PAGES: Record<string, string> = { '/privacy': privacyPage, '/terms': termsPage, '/support': supportPage }

describe('in-app Privacy, Terms and Support links', () => {
  it('renders one named navigation with all three pages', () => {
    const markup = renderToStaticMarkup(<LegalLinks />)
    expect(markup).toContain('<nav aria-label="Legal and support"')
    expect(LEGAL_LINKS.map((link) => link.label)).toEqual(['Privacy', 'Terms', 'Support'])
    for (const link of LEGAL_LINKS) {
      expect(markup).toContain(`href="${link.href}"`)
      expect(markup).toContain(`>${link.label}</a>`)
    }
  })

  it('stays a plain same-origin link so the Android app keeps the page inside the app', () => {
    const markup = renderToStaticMarkup(<LegalLinks />)
    expect(markup).not.toContain('target=')
    expect(markup).not.toContain('rel=')
    for (const link of LEGAL_LINKS) expect(link.href).toMatch(/^\/[a-z]+$/)
    // Phone tap target: at least 44 px high.
    expect(markup.match(/min-h-11/g)).toHaveLength(LEGAL_LINKS.length)
  })

  it('sits in the pinned footer, so it is one tap from the first screen', () => {
    const footer = appSource.slice(
      appSource.indexOf('function DataHonestyDisclosure'),
      appSource.indexOf('export default function App'),
    )
    expect(footer).toContain('<LegalLinks />')
    expect(appSource).toContain('<DataHonestyDisclosure />')
  })

  it('opens pages that exist and lead back to the map', () => {
    for (const link of LEGAL_LINKS) {
      const page = PAGES[link.href]
      expect(page).toContain('<a class="brand" href="/">')
      expect(page).toContain(`<a href="${link.href}" aria-current="page">${link.label}</a>`)
    }
  })
})
