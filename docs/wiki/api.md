# API

The public local API is implemented in [server.py](../../server.py). `Handler`
dispatches GET in `_route_get()`, POST in `_route_post()` and DELETE in
`do_DELETE()`. JSON responses are UTF-8 and default to `Cache-Control: no-store`.
Other responses set their own caching: crawlable pages use
`public, max-age=3600`, community photos and `assets/*` use
`public, max-age=31536000, immutable`, and other static files use `no-cache`.
Large JSON/static text assets are gzip-compressed when the client advertises
`Accept-Encoding: gzip`.

Read routes that do work are rate-limited per client (`RATE_LIMIT_RPM`, default
60 per minute; `/api/geocode` 10 per minute). A loopback caller with no trusted
forwarded identity is exempt.

## Routes

| Route | Purpose |
|---|---|
| `GET /api/chargers` | Main live or overview station/lead payload. |
| `GET /api/station?id=...&snapshot=...` | Stored directory detail for one exact catalogue ID; no live or upstream request. |
| `GET /api/charger-listing?ids=...` | For up to 25 saved charger IDs, the ones whose source stopped listing them. |
| `GET /api/free-registry` | Free-charger registry, optionally limited by `bbox`; `full=true` for the full rows. |
| `GET /api/location-sources?bbox=...` | Source matrix for a small area (about 20 km at most). |
| `GET /api/geocode?q=...` | Worldwide Nominatim proxy for search/jump, cached. |
| `GET /api/health` | Server time and cache metadata. |
| `GET /api/intel` | Normalized intelligence DB records for debugging/QA; `bbox`, `limit` (at most 2,500) and `offset` page through them. |
| `GET /api/sources` | Source categories plus current requested-source strategy. |
| `GET /api/source-strategy` | Named provider strategy matrix. |
| `GET /api/source-health` | Engine version + per-source health summary: map-eligible vs hidden-from-map record counts by tier/reliability/source, coordinate/source-link issue counts, and live-cache status per provider. |
| `GET /api/community/summary?ids=...` | Community check-in summaries for up to 200 station IDs, plus totals. |
| `GET /api/community/photo/<file>` | One stored community photo. |
| `POST /api/community/checkin`, `/api/community/photo`, `/api/community/station` | Community writes; rate-limited, and they require the `X-Volta-Community` token when the deployment sets one. |
| `POST /api/community/moderate` | Owner moderation; requires the `X-Volta-Admin` token. |
| `GET /api/watches/config` | Whether availability watches and push are enabled, and their limits. |
| `POST /api/watches` | Create an availability watch. |
| `GET /api/watches/<id>`, `DELETE /api/watches/<id>` | Read or cancel one watch. The watch ID is the capability. |
| `GET /api/vehicles`, `/api/vehicles/status` | Connected-vehicle status for the caller's session cookie. |
| `GET /api/vehicles/native/start`, `/api/vehicles/callback` | Vehicle OAuth start and callback; both redirect. |
| `POST /api/vehicles/connect`, `/refresh`, `/disconnect`, `/native/exchange` | Vehicle connection changes. |
| `POST /mcp` | Read-only MCP server for AI agents (`find_chargers`, `get_charger`, `get_data_status`); see [MCP server](../mcp.md). `GET`/`DELETE` return `405`. |
| `GET /sitemap.xml`, `/cargadores`, `/cargadores/*`, `/cargador/*` | Server-rendered crawlable charger pages and their sitemap. |
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
| `force=true` | Bypass fresh cache and attempt refetch. Honoured only for a loopback caller with no forwarded identity; ignored for every public caller. |

Response shape:

```json
{
  "stations": [],
  "leads": [],
  "meta": {
    "scope": "worldwide",
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

`web_root()` serves `frontend/dist`. Startup fails loudly
when that build is absent; there is no legacy static fallback. The only
client-side route that falls back to `index.html` is `/matrix`. Any other
missing extensionless path, including `/about` and `/pricing`, is a real 404.
`assets/*` uses immutable caching.
