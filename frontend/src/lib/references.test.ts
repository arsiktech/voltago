import { describe, expect, it } from 'vitest'
import { referenceKey, resolveReference, type ReferenceCatalogue } from './references'
import type { Station } from '../types'
const revision = 'a'.repeat(64)
const row = { id: 'owner', reference_revision: revision } as Station
const catalogue = async (state: 'alias' | 'withheld' | 'unresolved' = 'alias'): Promise<ReferenceCatalogue> => ({ version: 1, revision, entries: [{ key: await referenceKey('old'), state, ...(state === 'alias' ? { canonical_id: 'owner' } : {}) }] })
describe('charger references', () => {
  it('uses exact UTF-8 SHA256 domain encoding', async () => {
    expect(await referenceKey('old')).toBe('cc44436bce2af9dce4f4e0eaaabfa325587c667ef23dbef59d9ac70c378700b4')
    expect(await referenceKey('café/Зарядка')).toBe('4b8eabe483059336d14d3db10d39d71a223e7c6847f24936f8c69c638e8cc6a8')
    expect(await referenceKey('old ')).not.toBe(await referenceKey('old'))
  })
  it('resolves active and validated aliases without extra pins', async () => {
    expect((await resolveReference('old', [row], await catalogue())).station).toBe(row)
    expect((await resolveReference('owner', [row], await catalogue())).state).toBe('active')
  })
  it('never gives withheld or unresolved a station', async () => {
    for (const state of ['withheld', 'unresolved'] as const) expect(await resolveReference('old', [row], await catalogue(state))).toEqual({ state })
  })
  it('fails closed for different revisions and unavailable catalogues', async () => {
    expect((await resolveReference('old', [{ ...row, reference_revision: 'b'.repeat(64) }], await catalogue())).station).toBeUndefined()
    expect((await resolveReference('old', [row], null)).state).toBe('unavailable')
  })
  it('rejects duplicate owners and malformed catalogue', async () => {
    const c = await catalogue(); c.entries.push(c.entries[0])
    expect((await resolveReference('old', [row], c)).state).toBe('unavailable')
    c.entries = [{ key: 'bad', state: 'alias', canonical_id: 'owner' }]
    expect((await resolveReference('old', [row], c)).state).toBe('unavailable')
  })
  it('rejects cycles and canonical conflicts', async () => {
    const c = await catalogue(); c.entries.push({ key: await referenceKey('owner'), state: 'alias', canonical_id: 'old' })
    expect((await resolveReference('old', [row], c)).state).toBe('unavailable')
    expect((await resolveReference('old', [{ ...row, id: 'old' }], await catalogue())).station).toBeUndefined()
  })
})
