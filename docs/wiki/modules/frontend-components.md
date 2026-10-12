# Module: Frontend Components

Components under [frontend/src/components](../../../frontend/src/components)
turn enriched API rows into a driver-facing interface: map pins, virtualized
cards, filters, source provenance, and warning states.

## Responsibilities

- Render map clusters, price pills, and hover cards without relying on an
  external glyph server.
- Keep the station list smooth with virtualization.
- Show decision, source tier, corroboration, access, parking, tariff-window, and
  live/lead state on each card.
- Reveal source provenance and separate charge/parking claims.
- Preserve accessibility contracts for focus order, live regions, and details
  disclosure.

## Key Files

- [MapView.tsx](../../../frontend/src/components/MapView.tsx) - MapLibre map,
  generated price/cluster images, selection, viewport callbacks.
- [StationList.tsx](../../../frontend/src/components/StationList.tsx) -
  TanStack virtualizer wrapper.
- [StationCard.tsx](../../../frontend/src/components/StationCard.tsx) - card
  layout and availability/route facts.
- [SourceProvenance.tsx](../../../frontend/src/components/SourceProvenance.tsx)
  - claim, conflict, source, and corroboration details.
- [SearchBar.tsx](../../../frontend/src/components/SearchBar.tsx) - geocode
  dropdown and search input.
- [FilterRail.tsx](../../../frontend/src/components/FilterRail.tsx) - filter
  chips.
- [CoverageBanner.tsx](../../../frontend/src/components/CoverageBanner.tsx) -
  degraded/stale warnings.
- [docs/spa-dom-contract.md](../../spa-dom-contract.md) - selector and flow map
  for browser QA.

## Public API

- `<MapView />`
- `<StationList />`
- `<StationCard />`
- `<SourceProvenance />`
- `<SearchBar />`
- `<FilterRail />`
- `<CoverageBanner />`
- `<DecisionPill />`, `<Badge />`, `<ConnectorChips />`

## Internal Structure

`MapView` converts stations into GeoJSON and uses MapLibre clustering with
generated canvas images for price pills and cluster badges. The map internals
are forced out of tab order because the wrapper is `aria-hidden`.

`StationList` mounts only visible card rows. `StationCard` handles the compact
decision-first card and opens `<SourceProvenance />` for detailed evidence. The
card intentionally shows `Not live` on leads and renders charge/parking source
conflicts instead of smoothing them over.

## Dependencies

- **Uses:** MapLibre GL, TanStack Virtual, Framer Motion, lucide-react,
  `frontend/src/lib/format.ts`.
- **Used by:** `App.tsx`, browser DOM/accessibility QA.

## Notable Patterns / Gotchas

- Virtualized lists mean not all cards exist in the DOM at once.
- Cluster badges and map price pills are canvas images, not DOM nodes.
- External Source/Maps links use new-tab handoff with `noopener noreferrer`.
- Selector drift should be mirrored in `docs/spa-dom-contract.md`.
