import { useId, useRef, useState } from 'react'
import { Check, SlidersHorizontal, X } from 'lucide-react'
import type { FilterKey, Filters } from '../types'
import { DEFAULT_FILTERS, FILTER_LABELS, priceLimitText } from '../lib/format'

// Integer cents keep keyboard steps exact. The final position clears the cap.
const NO_PRICE_LIMIT = 101
const FILTER_GROUPS: { title: string; keys: FilterKey[] }[] = [
  { title: 'The charger', keys: ['available', 'fast', 'tesla'] },
  { title: 'Evidence & parking', keys: ['verified', 'parking', 'leads'] },
]
const DESCRIPTIONS: Record<FilterKey, string> = {
  available: 'Only live, available chargers marked Go now.',
  fast: 'Show chargers listed as fast charging.',
  tesla: 'Hide chargers known to be incompatible. Unknowns remain.',
  verified: 'Official, directory or route-checked sources. This does not mean live availability.',
  parking: 'Hide known paid, metered or time-limited parking. Unknown costs remain.',
  leads: 'On by default. Include places without live availability; verify before driving.',
}

function FilterOption({
  filterKey,
  pressed,
  onToggle,
}: {
  filterKey: FilterKey
  pressed: boolean
  onToggle: (key: FilterKey) => void
}) {
  const id = useId()
  return (
    <button
      type="button"
      className="chip filter-chip relative w-full justify-between gap-5 overflow-hidden rounded-2xl bg-surface px-4 py-3.5 text-left whitespace-normal shadow-none [&[aria-pressed=true]]:border-brand/50 [&[aria-pressed=true]]:bg-surface-2 [&[aria-pressed=true]]:text-fg [&[aria-pressed=true]]:shadow-none"
      aria-pressed={pressed}
      aria-labelledby={`${id}-label`}
      aria-describedby={`${id}-description`}
      onClick={() => onToggle(filterKey)}
    >
      <span className="min-w-0">
        <span id={`${id}-label`} className="block text-sm font-semibold text-fg">
          {FILTER_LABELS.find(({ key }) => key === filterKey)?.label}
        </span>
        <span id={`${id}-description`} className="mt-1 block text-xs leading-relaxed font-normal text-muted">
          {DESCRIPTIONS[filterKey]}
        </span>
      </span>
      <span
        aria-hidden="true"
        className={`grid size-6 shrink-0 place-items-center rounded-full border ${pressed ? 'border-brand bg-brand text-brand-contrast' : 'border-border-strong bg-surface'}`}
      >
        <Check size={14} strokeWidth={2.6} className="filter-chip-check" />
      </span>
    </button>
  )
}

export function FilterRail({
  filters,
  onToggle,
  onPriceChange,
  onReset,
}: {
  filters: Filters
  onToggle: (k: FilterKey) => void
  onPriceChange: (maxPrice: number | null) => void
  onReset?: () => void
}) {
  const dialogId = useId()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const openerRef = useRef<HTMLButtonElement | null>(null)
  const priceRef = useRef<HTMLInputElement>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  // Leads is default-on. Count deviations, including turning it off, so hidden
  // constraints never disappear from the rail's summary or its reset affordance.
  const changed = FILTER_LABELS.filter(({ key }) => filters[key] !== DEFAULT_FILTERS[key])
  const hiddenCount = changed.length
  const changedCount = hiddenCount + Number(filters.maxPrice !== null)
  const dirty = changedCount > 0

  function openFilters(opener: HTMLButtonElement, focusPrice = false) {
    const dialog = dialogRef.current
    if (!dialog || dialog.open) return
    openerRef.current = opener
    dialog.showModal()
    if (focusPrice) priceRef.current?.focus()
    setDialogOpen(true)
  }

  function closeFilters() {
    dialogRef.current?.close()
    setDialogOpen(false)
    // Return focus within the closing action. The native close event is queued;
    // focusing from that event can steal a user's next Tab/Enter target.
    openerRef.current?.focus()
  }

  return (
    <div role="group" aria-label="Filters">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          className="chip filter-chip relative flex-none overflow-hidden rounded-xl bg-surface px-2.5 text-xs shadow-none aria-pressed:border-transparent aria-pressed:bg-brand aria-pressed:text-brand-contrast"
          aria-pressed={filters.maxPrice === 0}
          onClick={() => onPriceChange(filters.maxPrice === 0 ? null : 0)}
        >
          Free
        </button>
        <button
          type="button"
          data-price-filter
          aria-label={filters.maxPrice !== null && filters.maxPrice > 0 ? `Charging price: ${priceLimitText(filters.maxPrice)}` : 'Charging price'}
          aria-describedby={`${dialogId}-price-summary`}
          aria-haspopup="dialog"
          aria-controls={dialogId}
          aria-expanded={dialogOpen}
          className={`chip min-h-11 relative flex-none rounded-xl bg-surface px-2.5 text-xs shadow-none ${filters.maxPrice !== null && filters.maxPrice !== 0 ? 'border-brand/50 bg-surface-2 text-fg' : ''}`}
          onClick={(event) => openFilters(event.currentTarget, true)}
        >
          {filters.maxPrice === null || filters.maxPrice === 0 ? 'Price' : priceLimitText(filters.maxPrice)}
        </button>
        <span id={`${dialogId}-price-summary`} className="sr-only">
          {filters.maxPrice === null ? 'No charging price limit' : filters.maxPrice === 0 ? 'Free charging only' : `Maximum €${filters.maxPrice.toFixed(2)} per kWh`}
        </span>
        <button
          type="button"
          className="ml-auto inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-border-strong bg-surface px-2.5 text-xs font-semibold text-fg transition-colors hover:bg-surface-2"
          aria-haspopup="dialog"
          aria-controls={dialogId}
          aria-expanded={dialogOpen}
          onClick={(event) => openFilters(event.currentTarget)}
        >
          <SlidersHorizontal size={15} aria-hidden="true" />
          <span>All filters</span>
          {hiddenCount > 0 && (
            <span className="grid size-5 place-items-center rounded-full bg-brand text-[10px] text-brand-contrast">
              <span aria-hidden="true">{hiddenCount}</span>
              <span className="sr-only">{hiddenCount} more {hiddenCount === 1 ? 'filter' : 'filters'} changed</span>
            </span>
          )}
        </button>
      </div>
      {dirty && onReset && (
        <div className="flex items-center justify-between gap-3 pt-1 text-xs text-muted">
          <span>{changedCount} {changedCount === 1 ? 'filter' : 'filters'} changed</span>
          <button
            type="button"
            aria-label="Clear all filters"
            onClick={onReset}
            className="min-h-11 rounded-lg px-1 font-medium text-fg underline decoration-border-strong underline-offset-4 hover:decoration-fg"
          >
            Clear filters
          </button>
        </div>
      )}
      <dialog
        ref={dialogRef}
        id={dialogId}
        aria-labelledby={`${dialogId}-title`}
        aria-describedby={`${dialogId}-description`}
        className="fixed inset-x-4 top-auto bottom-4 m-0 max-h-[calc(100dvh-32px)] w-auto max-w-none overflow-hidden rounded-3xl border border-border bg-surface p-0 text-fg shadow-2xl open:flex open:flex-col backdrop:bg-black/40 md:inset-0 md:m-auto md:h-fit md:w-[520px] md:max-w-[calc(100vw-48px)]"
        onCancel={(event) => {
          event.preventDefault()
          closeFilters()
        }}
        // An older queued close event must not mark a newly reopened dialog closed.
        onClose={() => setDialogOpen(Boolean(dialogRef.current?.open))}
      >
        <div className="flex shrink-0 items-start justify-between gap-5 border-b border-border px-5 py-5 md:px-7">
          <div>
            <h2 id={`${dialogId}-title`} className="text-xl font-semibold tracking-tight">All filters</h2>
            <p id={`${dialogId}-description`} className="mt-1 text-sm text-muted">Results update as you choose.</p>
          </div>
          <button
            type="button"
            aria-label="Close filters"
            onClick={closeFilters}
            className="grid size-11 shrink-0 place-items-center rounded-full border border-border bg-surface text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 overflow-y-auto overscroll-contain px-5 py-6 md:px-7">
          <fieldset className="mb-7 min-w-0">
            <legend className="mb-2 text-sm font-semibold text-fg">Charging price</legend>
            <p id={`${dialogId}-price-help`} className="text-xs leading-relaxed text-muted">
              Use reported euro charging prices. A limit excludes unknown prices and other-currency tariffs. Parking charges are separate.
            </p>
            <div className="mt-4 rounded-2xl border border-border px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <label htmlFor={`${dialogId}-price`} className="text-sm font-semibold">Maximum charging price</label>
                <output htmlFor={`${dialogId}-price`} className="shrink-0 text-sm font-semibold text-fg tabular-nums">
                  {priceLimitText(filters.maxPrice)}
                </output>
              </div>
              <input
                ref={priceRef}
                id={`${dialogId}-price`}
                type="range"
                min={0}
                max={NO_PRICE_LIMIT}
                step={1}
                value={filters.maxPrice === null ? NO_PRICE_LIMIT : Math.round(filters.maxPrice * 100)}
                aria-valuetext={filters.maxPrice === null ? 'No price limit' : filters.maxPrice === 0 ? 'Free charging' : `€${filters.maxPrice.toFixed(2)} per kWh maximum`}
                aria-describedby={`${dialogId}-price-help`}
                className="price-slider mt-2 min-h-11 w-full cursor-pointer rounded-lg"
                onChange={(event) => {
                  const cents = Number(event.currentTarget.value)
                  onPriceChange(cents === NO_PRICE_LIMIT ? null : cents / 100)
                }}
              />
              <div aria-hidden="true" className="flex justify-between text-xs text-muted tabular-nums">
                <span>Free</span><span>€0.50</span><span>No limit</span>
              </div>
              <button
                type="button"
                className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-lg px-1 text-xs font-medium text-fg underline decoration-border-strong underline-offset-4"
                aria-pressed={filters.maxPrice === null}
                onClick={() => onPriceChange(null)}
              >
                No limit
                {filters.maxPrice === null && <Check size={14} aria-hidden="true" />}
              </button>
            </div>
          </fieldset>
          {FILTER_GROUPS.map(({ title, keys }) => (
            <fieldset key={title} className="mb-7 min-w-0 last:mb-0">
              <legend className="mb-2 text-sm font-semibold text-fg">{title}</legend>
              <div className="grid gap-2">
                {keys.map((key) => <FilterOption key={key} filterKey={key} pressed={filters[key]} onToggle={onToggle} />)}
              </div>
            </fieldset>
          ))}
        </div>
        <div className="flex shrink-0 items-center justify-between gap-5 border-t border-border bg-surface px-5 py-4 md:px-7">
          {onReset ? (
            <button
              type="button"
              aria-label="Clear all filters"
              disabled={!dirty}
              onClick={onReset}
              className="min-h-11 rounded-lg px-1 text-sm font-medium text-fg underline underline-offset-4 disabled:cursor-default disabled:text-muted disabled:no-underline disabled:opacity-60"
            >
              Clear filters
            </button>
          ) : <span />}
          <button
            type="button"
            onClick={closeFilters}
            className="min-h-11 min-w-28 rounded-xl bg-brand px-7 text-sm font-semibold text-brand-contrast hover:bg-brand-strong"
          >
            Done
          </button>
        </div>
      </dialog>
    </div>
  )
}
