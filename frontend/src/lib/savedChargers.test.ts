import { describe, expect, it } from 'vitest'
import { readSaved, readSignedIn, savedAbsenceText, toggleSaved, writeSaved, writeSignedIn, type SavedCharger } from './savedChargers'

function memory(): Storage {
  const data = new Map<string, string>()
  return {
    get length() { return data.size },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => { data.delete(key) },
    setItem: (key, value) => { data.set(key, value) },
  }
}

const charger = (id: string, name = id): SavedCharger => ({ id, name, city: 'Valencia' })

describe('saved chargers', () => {
  it('starts unsigned-in with an empty list', () => {
    const storage = memory()
    expect(readSignedIn(storage)).toBe(false)
    expect(readSaved(storage)).toEqual([])
  })

  it('keeps sign-in and saved chargers in the supplied storage', () => {
    const storage = memory()
    writeSignedIn(storage, true)
    writeSaved(storage, [charger('a', 'Harbor')])
    expect(readSignedIn(storage)).toBe(true)
    expect(readSaved(storage)).toEqual([charger('a', 'Harbor')])
    writeSignedIn(storage, false)
    expect(readSignedIn(storage)).toBe(false)
  })

  it('adds and removes a charger without inventing fields', () => {
    const added = toggleSaved([], { id: 'a', name: '  North   quay ', city: '  ' })
    expect(added).toEqual([{ id: 'a', name: 'North quay', city: null }])
    expect(toggleSaved(added, charger('a'))).toEqual([])
  })

  it('drops an unreadable list', () => {
    const storage = memory()
    storage.setItem('volta.savedChargers', '{')
    expect(readSaved(storage)).toEqual([])
  })

  it('keeps the newest 200 chargers', () => {
    let rows: SavedCharger[] = []
    for (let i = 0; i < 205; i += 1) rows = toggleSaved(rows, charger(`id-${i}`, `Charger ${i}`))
    expect(rows).toHaveLength(200)
    expect(rows[0]?.id).toBe('id-204')
    expect(rows.some((row) => row.id === 'id-0')).toBe(false)
  })
})

describe('savedAbsenceText', () => {
  it('does not guess why a charger without a listing record is absent', () => {
    expect(savedAbsenceText(undefined)).toBe('Not in this search')
  })

  it('names the source and the last listed date, and claims no current status', () => {
    expect(savedAbsenceText({ source: 'Etecnic public charger map', last_listed_at: '2026-11-04T12:37:55Z' }))
      .toBe('Etecnic public charger map no longer lists this charger. Last listed 2026-11-04. Current status unknown.')
  })

  it('leaves out a date it cannot read', () => {
    expect(savedAbsenceText({ last_listed_at: 'soon' })).toBe('Its source no longer lists this charger. Current status unknown.')
  })
})
