import type { Station } from '../types'
export function selectedMapRow(id: string | null | undefined, rows: Station[]): Station | null {
  return rows.find(st => st.id === id) ?? null
}
/** Static locations remain visible between cities; never retain old live telemetry. */
export function discoveryRows(inventory: Station[], current: Station[]): Station[] {
  const rows = new Map(inventory.filter(st => st.lead === true).map(st => [st.id, st]))
  for (const st of current) rows.set(st.id, st)
  return [...rows.values()]
}
