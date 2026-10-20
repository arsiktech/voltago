import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ConnectorChips } from './ConnectorChips'
import { groupedConnectors } from '../lib/stationPresentation'

describe('grouped connector inventory', () => {
  it('groups ten identical published ports without inventing a connector type', () => {
    const connectors = Array.from({ length: 10 }, (_, i) => ({
      connector_id: String(i), label: 'Wall outlet (connector unspecified)', count: 1, power_kw: 3.6,
    }))
    const markup = renderToStaticMarkup(<ConnectorChips connectors={connectors} />)
    expect(markup).toContain('10 × Wall outlet, type not published · 3.6 kW')
    expect(markup.match(/<p>/g)).toHaveLength(1)
    expect(markup).not.toContain('+4 more')
    expect(connectors).toHaveLength(10)
  })

  it.each(['AVAILABLE', 'UNAVAILABLE', 'OUT_OF_SERVICE', 'FAULTED', 'CHARGING', 'UNKNOWN'])('does not turn hardware state %s into a live status', status => {
    const markup = renderToStaticMarkup(<ConnectorChips connectors={[{ label: 'CCS2', status }]} />)
    expect(markup).not.toMatch(/background:|--go|--danger|--check/)
    expect(markup).toContain('CCS2')
  })

  it('keeps different types and powers separate, regardless of repeated source IDs', () => {
    expect(groupedConnectors([
      { connector_id: 'socket:type2', label: 'Type 2', count: 2, power_kw: 11 },
      { connector_id: 'socket:type2', label: 'Type 2', count: 3, power_kw: 22 },
      { label: 'CCS2', count: 1, power_kw: 22 },
    ])).toEqual(['2 × Type 2 · 11 kW', '3 × Type 2 · 22 kW', '1 × CCS2 · 22 kW'])
  })

  it('sums published quantities while keeping missing quantities and types explicit', () => {
    expect(groupedConnectors([{ label: 'Type 2', count: 2 }, { label: 'Type 2', count: 4 }])).toEqual(['6 × Type 2'])
    expect(groupedConnectors([{}, {}])).toEqual(['Type not published · quantity not published'])
    expect(groupedConnectors([{ label: 'CCS2', count: 2 }, { label: 'CCS2' }])).toEqual(['CCS2 · quantity not published'])
  })

  it('counts overflow by distinct groups, not individual ports', () => {
    const markup = renderToStaticMarkup(<ConnectorChips connectors={Array.from({ length: 8 }, (_, i) => ({ label: `Socket ${i + 1}` }))} />)
    for (let i = 1; i <= 3; i++) expect(markup).toContain(`Socket ${i}`)
    expect(markup).not.toContain('Socket 4')
    expect(markup).toContain('+5 more types in Details')
  })
})
