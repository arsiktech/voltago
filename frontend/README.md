# Volta Go — frontend

Modern single-page UI for Volta Go. Place search and map navigation work worldwide;
charger coverage depends on the backend's connected sources. Spain remains the default
landing region. Inventory is requested for the current area, with date-line crossing and
polar bounds supported. Wide views use stored evidence; live requests fit the backend's
default 1.2° latitude budget (longitude adjusted for latitude). It renders the Python
backend's reliable-sources, data-honest API (`server.py`). Euro filters never convert
foreign-currency prices, and worldwide place search does not imply live coverage.

## Stack

- **Vite + React 19 + TypeScript**
- **Tailwind CSS v4** (`@tailwindcss/vite`, theme tokens in `src/index.css`, light/dark via `data-theme`)
- **TanStack Query** — live data with 60s refresh, `keepPreviousData`, stale handling
- **MapLibre GL 6** — keyless, theme-aware OpenFreeMap basemap; WebGL2 is required
  for the map. Search and the list stay usable when graphics are unavailable.
- **Framer Motion** — card/detail transitions; **lucide-react** icons; **Inter** (bundled via `@fontsource`)

## Develop

The UI calls the Python API at `/api/*`. First run the installation/build steps in
[the root README](../README.md): the Python server requires `frontend/dist` even
when Vite serves the development UI. Then run both:

```bash
# 1) backend (API + data engine) — from the repo root
PORT=8799 python3 -u server.py

# 2) this app (Vite dev server, proxies /api -> :8799)
npm run dev            # http://127.0.0.1:5183
```

Override the proxy target with `VITE_API_TARGET` (e.g. `VITE_API_TARGET=http://127.0.0.1:8773 npm run dev`).

## Build

```bash
npm run build          # tsc -b typecheck + Vite build -> dist/
```

`server.py` serves `frontend/dist` — the SPA is the only UI, and the server exits with an
actionable error at startup if it isn't built (no fallback), so a production run is just:
`npm run build` then `python3 server.py`.

## Structure

- `src/App.tsx` — orchestrator: map backdrop + glass panel, filters, search, geolocation, state
- `src/components/` — `MapView`, `StationCard`, `SourceProvenance`, `DecisionPill`, `FilterRail`,
  `SearchBar`, `CoverageBanner`, `ConnectorChips`, `Badge`
- `src/lib/api.ts` — query hooks; `src/lib/format.ts` — formatting + client-side filters; `src/types.ts` — API types
- `src/lib/discovery.ts` — map inventory and selected-station/list continuity
- `src/index.css`, `src/lib/motion.ts` — shared light/dark tokens and reduced-motion-aware transitions

Keep evidence and filtering rules in the pure `lib/` modules so their tests can
cover behavior independently of layout. Shared route-readiness cases live in
[`contracts/station_truth_cases.json`](../contracts/station_truth_cases.json) and
are consumed by Python, frontend, and native tests. Browser selectors and user
journeys are maintained in [the DOM contract](../docs/spa-dom-contract.md).

Run `npm run test`, `npm run lint`, and `npm run build` from this directory.
The `/coverage/` page reads `/api/sources` from the current service and reports loaded
source/country counts separately from adapter and live-cache status. Its browser check is
`node frontend/scripts/qa_coverage.cjs http://127.0.0.1:8773` from the repository root
against a built/served frontend; it uses source fixtures without upstream requests.
The Vite build bundles MapLibre's worker as an asset. Custom cluster icons use
`setMissingStyleImageResolver`; theme reloads recreate layers from the current
theme. Verify these behaviors from the repository root with
`node scripts/qa_maplibre_runtime.js http://127.0.0.1:8773` against the built app.
UI changes also need the affected mobile/desktop browser checks. The root
[receipt-refresh sequence](../README.md#refresh-source-bound-qa-receipts) explains
when changed source and `dist/` invalidate existing QA evidence.

## Design principles (inherited from the project)

Honesty over coverage: keep live / official / directory / community confidence distinct, keep
charging-price and parking-cost provenance separate, and never present stale/unverified data as
"go now". See the repo-root `AGENTS.md` for the full contract.
