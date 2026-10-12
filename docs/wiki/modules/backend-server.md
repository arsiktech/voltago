# Module: `server.py` Backend Server

[server.py](../../../server.py) is the stdlib-only HTTP server, API contract, live
data engine, and SPA static file server.

## Responsibilities

- Serve `frontend/dist` — the SPA is the only UI; refuse to start (loud error, non-zero
  exit) if it isn't built.
- Expose `/api/chargers`, `/api/geocode`, `/api/health`, `/api/intel`,
  `/api/sources`, and `/api/source-strategy`.
- Resolve request viewport and clamp overly broad bbox requests.
- Cache live/overview/static responses enough to keep driving UX responsive.
- Merge live stations with source-backed leads without losing provenance.

## Key Files

- [server.py](../../../server.py) - all backend runtime code.
- [frontend/dist](../../../frontend/dist) - production SPA output; the server exits at
  startup if it's missing (built via `npm --prefix frontend run build`).
- [data/free_intelligence.json](../../../data/free_intelligence.json) - static
  source-backed leads loaded by the API.
- [data/seed_leads.json](../../../data/seed_leads.json) - manual leads loaded
  alongside intelligence DB rows.

## Public API

- `web_root() -> Path` - returns the built SPA root (`frontend/dist`); pure, so unit
  tests can import it freely even without a build. `main()` is the sole enforcement
  point and refuses to start the server without one.
- `overview_response(params, max_price, fast_only) -> (status, payload)` -
  leads-only national/viewport overview.
- `overview_response_bytes(...) -> (status, json_bytes, gz_bytes)` - cached
  serialized overview hot path.
- `live_response(params) -> (status, payload)` - main live + leads response.
- `requested_source_strategy() -> dict` - source policy/status matrix.
- `Handler(BaseHTTPRequestHandler)` - HTTP route dispatch and static serving.
- `main()` - starts `ThreadingHTTPServer`.

## Internal Structure

`live_response()` is the core orchestration point. It resolves viewport, checks
the in-memory live cache, fetches MapaREVE, merges Gaia/Electromaps, loads
leads when requested, dedupes lead/live duplicates into source unions, enriches
rows, computes coverage, applies structured source-policy exclusions at every
fresh/stale/cache boundary, and attaches source-policy metadata. Feníe is reported
as homepage attribution/link-only and contributes no direct station evidence.

`Handler.do_GET()` keeps route dispatch small and delegates the hot path to
`live_response()` or `overview_response_bytes()`. Static asset serving is kept in
`serve_static()` and uses gzip memoization for large text assets.

## Dependencies

- **Uses:** Python stdlib only, public HTTP endpoints, `data/*.json`.
- **Used by:** React SPA, iOS client, QA scripts, browser smoke tests.

## Notable Patterns / Gotchas

- Cache keys include filter and snapped viewport tile state. Stale broader-cache
  fallback is possible, but filters and bbox are reapplied before response.
- `overview=true` is intentionally not live and should not raise a live-coverage
  warning.
- The server returns `200` with zero live stations plus honest leads when live
  source fetch fails and no cache exists; it does not pretend the backend is down
  with a bare 502.
