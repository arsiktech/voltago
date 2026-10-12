# Module: React SPA

The React SPA in [frontend/src](../../../frontend/src) is the primary user
interface. It is designed as a map-backed list tool, with the list and station
cards remaining usable even if the map or tile network fails.

## Responsibilities

- Manage overview/live modes, viewport, filters, search, geolocation, and theme.
- Query `/api/chargers` and `/api/geocode` through TanStack Query.
- Paint instant source-backed leads while live data is fetching.
- Drop stale live rows after live-fetch errors and show honest failure banners.
- Scope the list to the current viewport and filters.

## Key Files

- [frontend/src/main.tsx](../../../frontend/src/main.tsx) - React root and query
  client.
- [frontend/src/App.tsx](../../../frontend/src/App.tsx) - app orchestration.
- [frontend/src/lib/api.ts](../../../frontend/src/lib/api.ts) - query hooks and
  URL construction.
- [frontend/src/lib/format.ts](../../../frontend/src/lib/format.ts) - formatting,
  filters, source tier, availability, fail-closed data choice.
- [frontend/src/types.ts](../../../frontend/src/types.ts) - TypeScript API row
  types.
- [frontend/vite.config.ts](../../../frontend/vite.config.ts) - Vite, Tailwind,
  dev proxy, and MapLibre chunk split.

## Public API

- `useChargers(query)` - TanStack Query hook for live or overview charger data.
- `useGeocode(query)` - TanStack Query hook for city/place search.
- `chooseChargersData(args)` - data selector for instant leads vs live data vs
  fail-closed live errors.
- `passesFilters(station, filters, search)` - display filter predicate.
- `availability(station)` - honest live/lead availability summary.

## Internal Structure

`App.tsx` runs two charger queries in live mode: the main live query and an
instant `overview=true` query scoped to the committed bbox. `chooseChargersData`
decides whether to show live rows, instant leads, or fail-closed leads. Map rows
use chip filters only; list rows also apply viewport and search. The app keeps
geolocation/search as entry points into live mode and starts from national
overview mode.

## Dependencies

- **Uses:** React 19, TanStack Query, Framer Motion, MapLibre through lazy
  component import, Tailwind v4, lucide-react.
- **Used by:** browser users, QA browser contracts, Python server static serving.

## Notable Patterns / Gotchas

- MapLibre is lazy and wrapped in an error boundary because it is enhancement,
  not the product core.
- TanStack Query can retain last-good data after an error; `chooseChargersData`
  explicitly avoids presenting that as current live status.
- `available` filter excludes all leads; empty-state recovery clears it when
  showing all leads.
- `overview=true` is not an error state; it is the default national leads view.

## Request cancellation contract

`getJSON(url, timeoutMs?, signal?)` accepts an optional caller signal. Both query
hooks forward TanStack Query's signal through their shared query-options factories.
A query that loses its last observer (including a city or viewport key change)
aborts its pending fetch/body read; Query reverts its state without a failure or
retry. Charger placeholder data and freshness rules remain unchanged.

The request deadline covers headers and JSON body consumption. Transport diagnostics
separate `cancelled`, `timeout`, `http`, `network`, and `invalid-response`; request
URLs, caller abort reasons and raw upstream errors are never retained. Timers and
caller listeners are removed after settlement. Run the transport and real
QueryObserver regressions with `npm --prefix frontend test -- src/lib/api.test.ts`.
