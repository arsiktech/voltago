# API

The public local API is implemented in [server.py](../../server.py) by
`Handler.do_GET()`. Responses use UTF-8 JSON and `Cache-Control: no-store`.
Large JSON/static text assets are gzip-compressed when the client advertises
`Accept-Encoding: gzip`.

## Routes

| Route | Purpose |
|---|---|
| `GET /api/chargers` | Main live or overview station/lead payload. |
| `GET /api/geocode?q=...` | Spain-scoped Nominatim proxy for search/jump. |
| `GET /api/health` | Server time and cache metadata. |
| `GET /api/intel` | Normalized intelligence DB records for debugging/QA. |
| `GET /api/sources` | Source categories plus current requested-source strategy. |
| `GET /api/source-strategy` | Named provider strategy matrix. |
| `GET /api/source-health` | Engine version + per-source health summary: map-eligible vs hidden-from-map record counts by tier/reliability/source, coordinate/source-link issue counts, and live-cache status per provider. |
| `GET /*` | Static SPA assets from the required `frontend/dist` build. |

## `/api/chargers`

Important query parameters:

| Parameter | Meaning |
|---|---|
| `overview=true` | Leads-only static view. Does not run live fetch. Send `bbox` for a region or viewport; selection happens before the 2,500-row ranking limit. |
| `bbox=south,west,north,east` | Preferred viewport request format. |
| `lat`, `lon`, `radius_km` | User-centered live request alternative. |
| `include_leads=true` | Include source-backed intelligence/manual leads. |
| `max_price=0.25` | Price cap in EUR/kWh, clamped to 0–5 and rounded to two decimals. In overview mode, caps below `5.0` exclude unknown prices before ranking. The frontend sends `5.0` for broad discovery, retaining unknown-price locations. |
| `free_only=true` | In overview mode, require a zero tariff or an explicit free claim without a numeric tariff before ranking. A positive numeric tariff overrides a conflicting free flag. |
| `available=true` | Open-now route-ready filter. Drops to zero rather than showing stale/check-first rows. |
| `fast=true` | Fast charger filter. |
| `force=true` | Bypass fresh cache and attempt refetch. |

Response shape:

```json
{
  "stations": [],
  "leads": [],
  "meta": {
    "scope": "Spain",
    "viewport": {
      "bbox": {
        "latitude_sw": 39.3,
        "longitude_sw": -0.55,
        "latitude_ne": 39.62,
        "longitude_ne": -0.2
      },
      "source": "explicit",
      "clamped": false
    },
    "coverage": {
      "mode": "standard",
      "live_route_ready": 3,
      "lead_count": 12
    }
  },
  "cache": {
    "hit": false,
    "age_seconds": 0,
    "ttl_seconds": 60
  },
  "warning": null
}
```

Each station/lead row is enriched with optional fields mirrored in
[frontend/src/types.ts](../../frontend/src/types.ts), including:

- identity/position: `id`, `name`, `address`, `city`, `lat`, `lon`
- charging: `price_min`, `is_free`, `is_cheap`, `pricing`,
  `price_window_status`
- availability: `status`, `available`, `connector_summary`, `last_seen`
- routing: `decision`, `verification`, `recommendation_score`, `distance_km`,
  `drive_eta_min_est`
- evidence: `source`, `source_url`, `sources`, `primary_source`,
  `corroboration`
- honesty: `charge_claim`, `parking_claim`, `claim_conflict`, `access`,
  `parking`, `lead`

## Source Strategy Invariants

- `stations` are live/current only when backed by a live public source path.
- `leads` are source-backed candidates, never live availability.
- `corroboration.independent_sources` increases confidence in source agreement,
  not live status.
- Stale cache responses are allowed only with explicit `warning` and `cache.stale`
  metadata.
- Proprietary apps without public station/tariff surfaces are linked or
  attributed but not scraped.

## Static Serving

`web_root()` serves [frontend/dist](../../frontend/dist). Startup fails loudly
when that build is absent; there is no legacy static fallback. Client-side
routes without a file suffix fall back to `index.html`; `assets/*` uses
immutable caching.
