import { useEffect, type RefObject } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useReducedMotion } from 'framer-motion'
import type { Station } from '../types'
import { StationCard } from './StationCard'

/**
 * Virtualized station list. Only the cards near the viewport mount, so the list stays
 * smooth even with hundreds of national results. measureElement lets the Details
 * accordion (Framer Motion height:auto) reflow correctly.
 */
export function StationList({
  rows,
  selectedId,
  onSelect,
  scrollRef,
  scrollMargin = 0,
  scrollPaddingStart = 0,
}: {
  rows: Station[]
  selectedId: string | null
  onSelect: (id: string) => void
  scrollRef: RefObject<HTMLDivElement | null>
  /** Distance from the top of the scroller to this list (the results header scrolls with it). */
  scrollMargin?: number
  /** Height of the sticky results heading, so a revealed card is never hidden under it. */
  scrollPaddingStart?: number
}) {
  const reduceMotion = useReducedMotion()
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 232,
    overscan: 6,
    getItemKey: (i) => rows[i]?.id ?? i,
    scrollMargin,
    scrollPaddingStart,
  })

  // Reveal the selected card if it scrolled out of view (e.g. selection came from the map).
  useEffect(() => {
    if (!selectedId) return
    const idx = rows.findIndex((r) => r.id === selectedId)
    if (idx >= 0) virtualizer.scrollToIndex(idx, { align: 'auto', behavior: reduceMotion ? 'auto' : 'smooth' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, reduceMotion])

  const items = virtualizer.getVirtualItems()

  return (
    <div style={{ height: virtualizer.getTotalSize(), width: '100%', position: 'relative' }}>
      {items.map((vi) => {
        const st = rows[vi.index]
        if (!st) return null
        return (
          <div
            key={vi.key}
            data-index={vi.index}
            ref={virtualizer.measureElement}
            className="station-virtual-row"
            style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${vi.start - scrollMargin}px)` }}
          >
            <div data-station={st.id} className="pb-3">
              <StationCard
                st={st}
                index={vi.index}
                selected={st.id === selectedId}
                onSelect={onSelect}
                animateIn={vi.index < 8}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}
