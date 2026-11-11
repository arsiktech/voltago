import { useEffect, useState } from 'react'

/** Holds a geocoder query back until typing pauses, so partial place names are never sent. */
export function useSettledQuery(query: string, delayMs = 250): { query: string; waiting: boolean } {
  const [settled, setSettled] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(query), delayMs)
    return () => window.clearTimeout(timer)
  }, [query, delayMs])
  const waiting = query.length >= 3 && query !== settled
  return { query: waiting ? '' : query, waiting }
}
