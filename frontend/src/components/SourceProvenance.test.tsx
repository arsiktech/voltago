import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Station } from '../types'
import { SourceProvenance } from './SourceProvenance'

describe('SourceProvenance source-rights disclosure', () => {
  it('renders attribution, linked license, transformation, and source update metadata', () => {
    const station: Station = {
      id: 'endolla-license',
      name: 'Endolla B:SM',
      lat: 41.39,
      lon: 2.17,
      sources: [{
        name: 'Barcelona Endolla location/details open data',
        url: 'https://opendata-ajuntament.barcelona.cat/dataset',
        evidence: 'Official location and connector facts.',
        license: 'CC BY 4.0',
        license_url: 'https://creativecommons.org/licenses/by/4.0/',
        attribution: 'Ajuntament de Barcelona / Endolla',
        transformation: 'Normalized and combined by Volta; status remains non-live provenance.',
        source_fetched_at: '2026-07-15T08:01:00Z',
      }, {
        name: 'Zaragoza municipal open data',
        url: 'https://www.zaragoza.es/sede/servicio/equipamiento/list/44',
        source_last_updated: '2026-07-14T10:00:00Z',
      }],
    }

    const markup = renderToStaticMarkup(<SourceProvenance st={station} />)

    expect(markup).toContain('Attribution: Ajuntament de Barcelona / Endolla')
    expect(markup).toContain('CC BY 4.0')
    expect(markup).toContain('https://creativecommons.org/licenses/by/4.0/')
    expect(markup).toContain('Transformation: Normalized and combined by Volta')
    expect(markup).toContain('Retrieved: 2026-07-15T08:01:00Z')
    expect(markup).toContain('Source updated: 2026-07-14T10:00:00Z')
    expect(markup).not.toContain('Source update:')
  })
})
