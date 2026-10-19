import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Station } from '../types'
import { stationDetailsQueryOptions, TransportError } from '../lib/api'
import { stationPhoto } from '../lib/format'
import { stationTitle } from '../lib/provider'
import { SourceProvenance } from './SourceProvenance'
import { VehicleEstimate } from './VehicleEstimate'

function Evidence({ st }: { st: Station }) {
  const photo = stationPhoto(st)
  return <>
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
      <SourceProvenance st={st} />
    </>
  }
  return <Evidence st={query.data} />
}

/** Only mounted by an open disclosure. Directory detail never replaces the card/live row. */
export default function StationDetails({ st }: { st: Station }) {
  return <><VehicleEstimate st={st} />{st.detail_snapshot
    ? <DeferredEvidence key={`${st.id}:${st.detail_snapshot}`} st={st} snapshot={st.detail_snapshot} />
    : <Evidence st={st} />}</>
}
