import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Station } from '../types'
import { stationDetailsQueryOptions, TransportError } from '../lib/api'
import { connectorInventoryText, stationPhoto } from '../lib/format'
import { stationTitle } from '../lib/provider'
import { stationSourceReference } from '../lib/stationSource'
import { SourceProvenance } from './SourceProvenance'
import { VehicleEstimate } from './VehicleEstimate'
import { PriceEvidence } from './PriceEvidence'
import { StationConditions } from './StationConditions'

function Evidence({ st }: { st: Station }) {
  const photo = stationPhoto(st)
  return <>
    <PriceEvidence items={st.price_evidence} />
    {st.connectors && st.connectors.length > 0 && (
      <section className="mt-3 space-y-2" aria-label="Connector inventory">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Connectors</h3>
        <ul className="space-y-1.5 text-sm text-fg-soft">
          {st.connectors.map((connector, index) => {
            const source = stationSourceReference(st, connector.source_url, st.source_details?.source_name || st.source)
            return <li key={`${connector.connector_id || 'connector'}:${index}`}>
              <span>{connectorInventoryText(connector)}</span>
              {connector.connector_id && <span className="block text-xs text-muted">Port {connector.connector_id}</span>}
              {source && <a href={source.url} target="_blank" rel="noopener noreferrer"
                className="ml-2 inline-flex min-h-11 items-center font-medium text-brand-strong underline">{source.exact ? `Open ${source.name}` : `Connector reference: ${source.name}`}</a>}
            </li>
          })}
        </ul>
        <p className="text-xs text-muted">Source-reported hardware. Current socket availability is not verified by these details.</p>
      </section>
    )}
    {photo && (
      <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface-2"
        title={`Public source photo via ${photo.source}. May be stale; onsite view wins.`}>
        <img src={photo.src} alt={`${stationTitle(st)} public source photo`} loading="lazy"
          className="h-28 w-full object-cover"
          onError={(e) => { e.currentTarget.parentElement?.classList.add('hidden') }} />
      </div>
    )}
    <SourceProvenance st={st} />
  </>
}

function DeferredEvidence({ st, snapshot }: { st: Station; snapshot: string }) {
  const client = useQueryClient()
  const query = useQuery(stationDetailsQueryOptions(st.id, snapshot))
  const changed = query.error instanceof TransportError && query.error.status === 409
  const waiting = query.isPending || query.isFetching
  // A failed/paused refresh must not turn cached evidence into a fresh claim.
  if (query.isPaused || query.isError || waiting) {
    return <>
      <div role="status" className="mt-3 space-y-2 text-sm text-muted">
        <p>{query.isPaused ? 'Full source details are unavailable offline. Saved source links remain below.'
          : changed ? 'The catalogue has changed. Refresh chargers to load current source details.'
          : waiting ? 'Loading source details…'
          : 'Source details could not load. Check your connection or use a source link below.'}</p>
        {!waiting && !query.isPaused && (
          <button className="min-h-11 rounded-xl border border-border px-3 py-2 font-semibold text-fg"
            onClick={() => { if (changed) void client.invalidateQueries({ queryKey: ['chargers'] }); else void query.refetch() }}>
            {changed ? 'Refresh chargers' : 'Retry details'}
          </button>
        )}
      </div>
      <PriceEvidence items={st.price_evidence} />
      <SourceProvenance st={st} />
    </>
  }
  return <Evidence st={{ ...query.data, price_evidence: query.data.price_evidence ?? st.price_evidence }} />
}

/** Only mounted by an open disclosure. Directory detail never replaces the card/live row. */
export default function StationDetails({ st }: { st: Station }) {
  return <><StationConditions st={st} /><VehicleEstimate st={st} />{st.detail_snapshot
    ? <DeferredEvidence key={`${st.id}:${st.detail_snapshot}`} st={st} snapshot={st.detail_snapshot} />
    : <Evidence st={st} />}</>
}
