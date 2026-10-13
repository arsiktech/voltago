# Web performance: findings and priorities

Measured on 13 October 2026 against the production build at `88650db` and the
performance changes described below. These are local lab results, not production
Core Web Vitals. The public site and PageSpeed API were unreachable from the
measurement environment. No physical Android/4G test has been completed.

## Improvements implemented

- **City search:** wait 250 ms after typing stops before geocoding. Switch away
  from the previous query immediately so cancellation still works and Enter cannot
  select an old city's suggestions during the delay. Clearing the input cancels a
  pending lookup. Local input feedback stays immediate.
- **Offline installation:** copy content-hashed assets into Cache Storage using
  their existing immutable HTTP cache. The previous `cache: no-store` downloaded
  the app, map engine and fonts again. Continue fetching mutable shell documents
  and the default directory snapshot from the network. The complete offline build
  is still installed; nearby/live responses remain network-only.
- **Main font:** preload the Latin Manrope WOFF2 subset using the same Vite-generated
  asset URL as the CSS. Other scripts load normally when needed; there is still
  one locally hosted font family.
- **Community summaries:** count recent failures for all stations in one grouped
  query instead of querying once per station. Preserve moderation status, time
  windows and recovery after a successful check-in.

## Measurements

Three fresh Chromium contexts per version, 390×844 viewport, 4× CPU slowdown,
1.6 Mbps download and 150 ms latency. The built app and its checked-in sample
catalogue were served locally. Geocoding and the external basemap were controlled;
no live provider latency is represented. HTTP caching stayed enabled, including
for service-worker requests. Transfer figures are response-body bytes observed
over the complete visit, including offline installation, not billing totals.
The sample contains 162 records; the default response includes 163 leads after
the application's additional seed data.

| Metric | Before | After |
| --- | ---: | ---: |
| Median cold-visit response bodies | 1,356,372 B | 871,757 B (−35.7%) |
| JavaScript response bodies | 1,017,596 B | 581,106 B (−42.9%) |
| Font response bodies | 97,256 B | 72,420 B |
| Requests while typing `Valencia` | 6 | 1 |
| Median first station card | 3.674 s | 3.263 s |
| Median first contentful paint | 2.808 s | 2.792 s |
| SQL SELECTs for 10,000 stations' community summaries | 10,006 | 7 |
| Median community summary rebuild (10,000 stations / 40,000 check-ins) | 162.70 ms | 123.65 ms (−24%) |

First-paint timing is effectively unchanged; first-card timing is a small sample
and can vary on this shared machine. The byte and query-count reductions are
the stronger evidence. There were no page errors or horizontal mobile overflow.
Synthetic community measurements use isolated test data, not production history;
their timing is the median of five alternating runs per version. Full before/after
summary dictionaries matched.

Lighthouse 13.5.0 mobile runs also improved: median performance score 49 → 72 and
median LCP 4.812 → 2.859 seconds across three runs per build. Scores ranged 44–67
before and 71–83 after; one baseline run warned about a slow host CPU. Treat this
as a local diagnostic, not a reliable prediction of production scores. The
basemap was unavailable in these runs, so its unused-JavaScript warning cannot
justify removing the map dependency.

The full 268,626-record worldwide index was measured separately: the default
2,500-row compact overview expands to approximately 11.3 MB of JSON (513 KB gzip).
A cold response took about 3.6 seconds, while a repeated same-minute serialized
cache hit took less than 1 ms. These are backend measurements, distinct from the
browser runs using the small checked-in sample.

## How the checklist applies

| Suggestion | Volta Go assessment / next action |
| --- | --- |
| Lighthouse and mobile PageSpeed | Local Lighthouse and throttled browser measurements completed. Production PageSpeed and real-user measurements remain needed. |
| WebP / AVIF images | Main UI uses SVG icons; source photos only load inside expanded details. Inspect the relatively large PNG install icon separately while preserving Apple/PWA support. Images are not the main measured bottleneck. |
| Lazy images below the fold | Source photos already use `loading="lazy"` and are mounted only when details open. |
| Static CDN | Hashed assets already have gzip and year-long immutable caching. Verify actual edge-cache behavior on the deployed domain before changing hosting. A tunnel alone does not prove a cache hit. |
| Remove unused packages | Runtime dependencies have active uses: React, query cache, list virtualization, motion, icons, map and font. Do not remove the map because a blocked-map Lighthouse run reports unused map code. |
| Split JavaScript | MapLibre, its worker and MapView already have separate chunks. Next evaluate loading detailed station disclosures and optional interaction code on demand. |
| Preload fonts / limit families | One local variable font; primary subset now preloaded. Keep other scripts for international station names. |
| Database indexes | Coordinate, identifier and community indexes already cover the main lookups. Measure query plans before adding more. |
| N+1 queries | Fixed the measured per-station query in community summaries. |
| Pagination | `/api/intel` is paginated; overview queries are capped and result cards virtualized. The 2,500-row overview still needs a smaller initial card payload and separate map summaries. |
| Cache stable APIs | Serialized directory responses and source summaries are cached; writes/data changes invalidate caches. Geocoding is cached. Preserve live freshness and explicit offline labels. |
| Stream AI answers | No AI-generated answer stream in the driver UI, so this does not apply. |
| Skeletons / loading states | Station skeletons, search progress and source-inventory loading states already exist. |
| Background heavy work | Data collection, candidate validation and index building already run outside HTTP requests. The default overview is warmed in a background thread. |
| Server near users | Static delivery and payload reduction come first. Measure regional latency before moving the service; collection currently depends on where upstream sources remain accessible. |
| Cold starts | Persistent Python service, not request-launched serverless functions. Cold data-query/cache work remains measurable. |
| Debounce input | Price slider was already debounced; city search is now debounced too. |
| Remove third-party scripts | No analytics/ad script was found in the entry page. Map resources are necessary for the optional map. |
| Cheap Android on 4G | Still required. CPU/network emulation does not prove low-memory device behavior or real cellular performance. |
| What makes it slow? | Duplicate offline downloads and repeated search requests were avoidable. Large directory JSON, initial React/motion execution and the map's execution cost are the next substantial issues. |

## Next implementation priorities

1. Separate lightweight map summaries from paginated station cards. Fetch full
   evidence when details open, while keeping price, availability, freshness and
   uncertainty visible before selection. Preserve station IDs and correct ranking;
   silently dropping results is not pagination.
2. Profile React work on a real low-end Android, including scrolling, opening
   details and changing the search area. Use that evidence to split optional UI
   code; avoid moving an equally large download into the immediate startup path.
3. Verify production compression, cache-hit headers and regional time to first
   byte. Measure mobile Lighthouse/PageSpeed on the actual deployed version and
   compare repeated runs under the same conditions.

## Regression checks

Run `./scripts/verify.sh --full` for backend/frontend tests, lint and build.
Against a running production build, run `scripts/qa_search_debounce.js` for
request suppression, clearing and stale-selection protection;
`scripts/qa_near_home_first_use.js` for the search/location flow; and
`scripts/qa_cdn_blocked_ui.js` for the map-free and one-visit offline behavior.
The search regression also runs in GitHub Verify. Local measurement receipts
stay outside Git because they contain environment-specific details.
