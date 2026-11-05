/** Saved chargers stay in this browser. Sign in does not create a server account. */

const SESSION_KEY = 'volta.savedChargers.signedIn'
const SAVED_KEY = 'volta.savedChargers'
const MAX_SAVED = 200

export type SavedCharger = {
  id: string
  name: string
  city: string | null
}

export function browserStorage(): Storage | null {
  try {
    return globalThis.localStorage
  } catch {
    return null
  }
}

export function readSignedIn(storage: Storage): boolean {
  try {
    return storage.getItem(SESSION_KEY) === '1'
  } catch {
    return false
  }
}

export function writeSignedIn(storage: Storage, signedIn: boolean): void {
  try {
    if (signedIn) storage.setItem(SESSION_KEY, '1')
    else storage.removeItem(SESSION_KEY)
  } catch {
    /* The in-memory choice still applies for this visit. */
  }
}

function cleanText(value: unknown, limit: number): string {
  if (typeof value !== 'string') return ''
  return value.replace(/\s+/g, ' ').trim().slice(0, limit)
}

export function readSaved(storage: Storage): SavedCharger[] {
  try {
    const raw = storage.getItem(SAVED_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    const rows: SavedCharger[] = []
    for (const item of parsed) {
      if (!item || typeof item !== 'object') continue
      const record = item as Record<string, unknown>
      const id = cleanText(record.id, 200)
      if (!id) continue
      rows.push({
        id,
        name: cleanText(record.name, 200),
        city: cleanText(record.city, 120) || null,
      })
      if (rows.length >= MAX_SAVED) break
    }
    return rows
  } catch {
    return []
  }
}

export function writeSaved(storage: Storage, rows: SavedCharger[]): void {
  try {
    storage.setItem(SAVED_KEY, JSON.stringify(rows.slice(0, MAX_SAVED)))
  } catch {
    /* The in-memory list still applies for this visit. */
  }
}

export function toggleSaved(rows: SavedCharger[], station: SavedCharger): SavedCharger[] {
  const id = cleanText(station.id, 200)
  if (!id) return rows
  if (rows.some((row) => row.id === id)) return rows.filter((row) => row.id !== id)
  return [{ id, name: cleanText(station.name, 200), city: cleanText(station.city, 120) || null }, ...rows].slice(0, MAX_SAVED)
}
