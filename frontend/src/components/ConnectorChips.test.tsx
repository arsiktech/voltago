import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ConnectorChips } from './ConnectorChips'

describe('connector inventory and availability', () => {
  it.each([
    ['AVAILABLE', 'go'],
    ['unavailable', 'danger'],
    ['OUT_OF_SERVICE', 'danger'],
    ['FAULTED', 'danger'],
    ['CHARGING', 'check'],
    ['UNKNOWN', 'faint'],
  ])('renders %s using the %s status token', (status, token) => {
    const markup = renderToStaticMarkup(<ConnectorChips connectors={[{ label: 'CCS2', status }]} />)
    expect(markup).toContain(`background:var(--${token})`)
    if (status.toUpperCase() === 'UNAVAILABLE') expect(markup).not.toContain('background:var(--go)')
  })

  it('preserves repeated upstream connector IDs with their individual power and state', () => {
    const markup = renderToStaticMarkup(<ConnectorChips connectors={[
      { connector_id: 'socket:type2', label: 'Type 2', power_kw: 11, status: 'AVAILABLE' },
      { connector_id: 'socket:type2', label: 'Type 2', power_kw: 22, status: 'UNAVAILABLE' },
    ]} />)
    expect(markup.match(/class="badge"/g)).toHaveLength(2)
    expect(markup).toContain('Type 2 · 11 kW')
    expect(markup).toContain('Type 2 · 22 kW')
    expect(markup.match(/background:var\(--go\)/g)).toHaveLength(1)
    expect(markup.match(/background:var\(--danger\)/g)).toHaveLength(1)
  })

  it('counts overflow from the complete inventory even when IDs repeat', () => {
    const markup = renderToStaticMarkup(<ConnectorChips connectors={Array.from({ length: 8 }, (_, i) => ({
      connector_id: 'socket:type2', label: `Socket ${i + 1}`, status: 'UNKNOWN',
    }))} />)
    for (let i = 1; i <= 6; i++) expect(markup).toContain(`Socket ${i}`)
    expect(markup).not.toContain('Socket 7')
    expect(markup).not.toContain('Socket 8')
    expect(markup).toContain('+2 more')
  })
})
