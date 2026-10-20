import type { Connector } from '../types'
import { groupedConnectors } from '../lib/stationPresentation'

const MAX_GROUPS = 3

/** Hardware summary only. Live occupancy belongs in the separate availability row. */
export function ConnectorChips({ connectors }: { connectors?: Connector[] }) {
  if (!connectors?.length) return null
  const groups = groupedConnectors(connectors)
  return (
    <div className="space-y-1 text-xs leading-relaxed text-muted" data-connector-summary>
      {groups.slice(0, MAX_GROUPS).map((label, index) => <p key={index}>{label}</p>)}
      {groups.length > MAX_GROUPS && <p>+{groups.length - MAX_GROUPS} more types in Details</p>}
    </div>
  )
}
