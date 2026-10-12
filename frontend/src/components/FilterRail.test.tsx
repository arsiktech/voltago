import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Filters } from '../types'
import { DEFAULT_FILTERS, FILTER_LABELS } from '../lib/format'
import { FilterRail } from './FilterRail'

function renderFilters(overrides: Partial<Filters> = {}) {
  const markup = renderToStaticMarkup(
    <FilterRail filters={{ ...DEFAULT_FILTERS, ...overrides }} onToggle={() => {}} onPriceChange={() => {}} onReset={() => {}} />,
  )
  const [rail, dialog] = markup.split('<dialog')
  return { rail, dialog }
}

describe('progressive filter controls', () => {
  it('keeps only the two price shortcuts on the rail and all settings in a named dialog', () => {
    const { rail, dialog } = renderFilters()
    expect(rail.match(/ aria-pressed="/g)).toHaveLength(1)
    expect(rail).toContain('Free')
    expect(rail).toContain('Charging price')
    expect(dialog).toContain('type="range"')
    expect(dialog).toContain('No price limit')
    expect(dialog).toContain('unknown prices')
    expect(rail).toContain('aria-haspopup="dialog"')
    expect(rail).toContain('aria-expanded="false"')
    expect(rail).not.toContain('Source checked')
    expect(rail).not.toContain('Clear filters')
    expect(dialog.match(/ aria-pressed="/g)).toHaveLength(FILTER_LABELS.length + 1)
    expect(dialog).toContain('aria-labelledby=')
    expect(dialog).toContain('aria-describedby=')
    expect(dialog).toContain('Official, directory or route-checked sources. This does not mean live availability.')
    expect(dialog).toContain('On by default. Include places without live availability; verify before driving.')
  })

  it('does not count the default-on leads setting as a changed filter', () => {
    const { rail, dialog } = renderFilters()
    expect(rail).not.toContain('filters changed')
    expect(rail).not.toContain('more filter')
    expect(dialog.match(/aria-pressed="true"/g)).toHaveLength(2)
    expect(dialog).toContain('aria-label="Clear all filters" disabled=""')
  })

  it('makes a hidden disabled-leads constraint discoverable and resettable', () => {
    const { rail, dialog } = renderFilters({ leads: false })
    expect(rail).toContain('1 more filter changed')
    expect(rail).toContain('1 filter changed')
    expect(rail).toContain('aria-label="Clear all filters"')
    expect(dialog).not.toContain('aria-label="Clear all filters" disabled=""')
    expect(dialog.match(/aria-pressed="true"/g)).toHaveLength(1)
  })

  it('separates the hidden settings count from the visible price choices', () => {
    const { rail } = renderFilters({ maxPrice: 0.4, fast: true, verified: true })
    expect(rail).toContain('2 more filters changed')
    expect(rail).toContain('3 filters changed')
    expect(rail).toContain('≤ €0.40/kWh')
  })
  it('announces an arbitrary chosen cap and free-only mode', () => {
    const capped = renderFilters({ maxPrice: 0.4 })
    expect(capped.rail).toContain('aria-label="Charging price: ≤ €0.40/kWh"')
    expect(capped.dialog).toContain('€0.40 per kWh maximum')
    expect(capped.dialog).toContain('value="40"')
    const free = renderFilters({ maxPrice: 0 })
    expect(free.rail).toContain('aria-pressed="true"')
    expect(free.dialog).toContain('Free charging')
    expect(free.rail).toContain('1 filter changed')
  })

})
