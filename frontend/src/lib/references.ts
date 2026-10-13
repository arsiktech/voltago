import type { Station } from '../types'

export interface ReferenceCatalogue {
  version: 1
  revision: string
  entries: { key: string; state: 'alias' | 'withheld' | 'unresolved'; canonical_id?: string }[]
}
export type ReferenceOutcome = { state: 'active' | 'alias' | 'withheld' | 'unresolved' | 'unavailable'; station?: Station }

/** Exact UTF-8 bytes, no normalization. Saved IDs never leave the device. */
export async function referenceKey(id: string): Promise<string> {
  const bytes = new TextEncoder().encode(`volta-reference-v1\0${id}`)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

export async function resolveReference(id: string, rows: Station[], catalogue?: ReferenceCatalogue | null): Promise<ReferenceOutcome> {
  const direct = rows.find(row => row.id === id)
  if (!catalogue) return direct ? { state: 'active', station: direct } : { state: 'unavailable' }
  if (catalogue.version !== 1 || !/^[a-f0-9]{64}$/.test(catalogue.revision) || !Array.isArray(catalogue.entries) || catalogue.entries.length > 100000) return { state: 'unavailable' }
  const entries = new Map<string, ReferenceCatalogue['entries'][number]>()
  for (const entry of catalogue.entries) {
    if (!entry || !/^[a-f0-9]{64}$/.test(entry.key) || entries.has(entry.key) || !['alias', 'withheld', 'unresolved'].includes(entry.state)
      || (entry.state === 'alias' ? typeof entry.canonical_id !== 'string' || !entry.canonical_id : entry.canonical_id !== undefined)) return { state: 'unavailable' }
    entries.set(entry.key, entry)
  }
  if (direct?.reference_revision && direct.reference_revision !== catalogue.revision) return { state: 'unavailable' }
  const entry = entries.get(await referenceKey(id))
  if (entry) {
    if (direct) return { state: 'unavailable' }
    if (entry.state !== 'alias') return { state: entry.state }
    // Catalogue aliases are one hop and may only target a current canonical row.
    if (entries.has(await referenceKey(entry.canonical_id!))) return { state: 'unavailable' }
    const target = rows.find(row => row.id === entry.canonical_id && row.reference_revision === catalogue.revision)
    return target ? { state: 'alias', station: target } : { state: 'unavailable' }
  }
  return direct ? { state: 'active', station: direct } : { state: 'unavailable' }
}

export const referenceMessage = (state: ReferenceOutcome['state']): string => state === 'withheld'
  ? 'This saved reference is recognized, but its source details cannot be shown. It is not a navigation target.'
  : state === 'unresolved'
    ? 'This charger reference needs verification. No replacement has been selected.'
    : 'This charger is not in the current results. Refresh or broaden your search.'
