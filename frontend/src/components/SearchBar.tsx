import { useEffect, useId, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { MapPin, Search, X } from 'lucide-react'
import { useGeocode } from '../lib/api'
import type { GeocodeResult } from '../types'
import { fadeSlide, popIn, quickEase } from '../lib/motion'

export function SearchBar({
  value,
  onChange,
  onPlaceSelect,
}: {
  value: string
  onChange: (v: string) => void
  onPlaceSelect?: (r: GeocodeResult) => void
}) {
  const reduceMotion = useReducedMotion()
  const [focused, setFocused] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // "/" jumps to search from anywhere that is not already a text field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return
      const target = e.target as HTMLElement | null
      if (target?.closest('input, textarea, select, [contenteditable="true"], dialog')) return
      e.preventDefault()
      inputRef.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const listId = `home-place-results-${useId().replace(/:/g, '')}`
  const query = value.trim()
  const [settledQuery, setSettledQuery] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => setSettledQuery(query), 250)
    return () => window.clearTimeout(timer)
  }, [query])
  const waitingForInput = query.length >= 3 && query !== settledQuery
  // Switch away from the previous query immediately: its request is cancelled and
  // its suggestions cannot be selected while the next city is still being typed.
  const geo = useGeocode(waitingForInput ? '' : query)
  const results = geo.data?.results ?? []
  const [activeOption, setActiveOption] = useState({ value, resultCount: results.length, index: results.length ? 0 : -1 })
  const optionScopeChanged = activeOption.value !== value || activeOption.resultCount !== results.length
  const activeIndex = optionScopeChanged ? (results.length ? 0 : -1) : activeOption.index
  // Reset before committing the new suggestions, so aria-activedescendant never
  // points at the previous query's option (and cannot revive it when returning).
  if (optionScopeChanged) {
    setActiveOption({ value, resultCount: results.length, index: activeIndex })
  }
  const setActiveIndex = (index: number) => setActiveOption({ value, resultCount: results.length, index })
  const showDrop =
    Boolean(onPlaceSelect) &&
    focused &&
    value.trim().length >= 3 &&
    (waitingForInput || results.length > 0 || geo.isFetching || geo.isFetched || geo.isError)

  const pick = (r: GeocodeResult) => {
    onPlaceSelect?.(r)
    onChange('')
    setActiveIndex(-1)
    setFocused(false)
  }

  return (
    <div className="relative">
      <motion.span
        aria-hidden="true"
        className="pointer-events-none absolute left-3 top-1/2 grid -translate-y-1/2 text-muted"
        animate={reduceMotion ? undefined : { scale: focused ? 1.08 : 1, color: focused ? 'var(--brand-strong)' : 'var(--muted)' }}
        transition={quickEase}
      >
        <Search size={16} />
      </motion.span>
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          setFocused(true)
        }}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && results.length > 0) {
            e.preventDefault()
            setFocused(true)
            setActiveIndex(showDrop ? (activeIndex + 1) % results.length : 0)
          } else if (e.key === 'ArrowUp' && results.length > 0) {
            e.preventDefault()
            setFocused(true)
            setActiveIndex(!showDrop || activeIndex <= 0 ? results.length - 1 : activeIndex - 1)
          } else if (e.key === 'Enter' && showDrop && results[activeIndex >= 0 ? activeIndex : 0]) {
            e.preventDefault()
            pick(results[activeIndex >= 0 ? activeIndex : 0])
          } else if (e.key === 'Escape') {
            setFocused(false)
          }
        }}
        placeholder="Search a city or address"
        aria-label="Enter home address or city"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showDrop}
        aria-controls={showDrop ? listId : undefined}
        aria-activedescendant={showDrop && activeIndex >= 0 ? `${listId}-option-${activeIndex}` : undefined}
        inputMode="search"
        autoComplete="off"
        className="search-input h-12 w-full rounded-xl border border-border-strong bg-surface py-3 pl-10 pr-10 text-sm text-fg outline-none transition placeholder:text-muted focus:border-brand focus-visible:ring-2 focus-visible:ring-brand"
      />
      <AnimatePresence initial={false}>
        {value && (
          <motion.button
            type="button"
            onClick={() => onChange('')}
            aria-label="Clear search"
            variants={popIn(Boolean(reduceMotion), 0.9)}
            initial="hidden"
            animate="show"
            exit="exit"
            className="absolute right-0.5 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-lg text-muted transition hover:text-fg active:scale-[0.96]"
          >
            <X size={16} />
          </motion.button>
        )}
      </AnimatePresence>

      {/* Dismiss synchronously: exiting options must not remain clickable or
          selectable after aria-expanded becomes false. Keep the entrance motion. */}
      {showDrop && (
          <motion.ul
            id={listId}
            role="listbox"
            aria-label="Home area suggestions"
            variants={fadeSlide(Boolean(reduceMotion), 6)}
            initial="hidden"
            animate="show"
            className="bg-surface absolute inset-x-0 top-[calc(100%+6px)] z-30 max-h-72 overflow-auto rounded-xl border border-border p-1 shadow-[var(--shadow-lg)]"
          >
            {(waitingForInput || geo.isFetching) && results.length === 0 && (
              <li className="px-3 py-2.5 text-xs text-muted">Searching…</li>
            )}
            {geo.isError && !geo.isFetching && (
              <li className="px-3 py-2.5 text-xs text-muted">Place search is unavailable. Try again.</li>
            )}
            {!geo.isError && geo.isFetched && !geo.isFetching && results.length === 0 && (
              <li className="px-3 py-2.5 text-xs text-muted">No matching home area found.</li>
            )}
            {results.map((r, i) => (
              <motion.li
                key={`${r.lat},${r.lon},${i}`}
                id={`${listId}-option-${i}`}
                role="option"
                aria-selected={activeIndex === i}
                onPointerDown={(e) => e.preventDefault()}
                onClick={() => pick(r)}
                onMouseEnter={() => setActiveIndex(i)}
                initial={reduceMotion ? { opacity: 0 } : { opacity: 0, x: -4 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.14, delay: Math.min(i, 4) * 0.025 }}
                className={`flex cursor-pointer items-start gap-2 rounded-lg px-2.5 py-2 text-left transition active:scale-[0.99] ${activeIndex === i ? 'bg-surface-2' : 'hover:bg-surface-2'}`}
              >
                <MapPin size={14} className="mt-0.5 flex-none text-brand-strong" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-fg">{r.name}</span>
                  <span className="block truncate text-xs text-muted">{r.label}</span>
                </span>
              </motion.li>
            ))}
          </motion.ul>
      )}
    </div>
  )
}
