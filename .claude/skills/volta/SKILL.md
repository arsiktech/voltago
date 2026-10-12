---
name: volta
description: Working knowledge for the Volta Go repo (Spain EV-charging decision app: native iPhone app in ios/Volta with MapKit, stdlib Python server.py + station_truth.py + community.py, React/Vite/MapLibre frontend/, ingestion scripts, QA receipts, Fly.io fallback host). Covers the evidence-class and data-source rules that constrain code, ingestion merge and delta-gate rules, API probing, verification and source-bound receipt procedure, and owner-only production operations. Use when changing charger data, ranking or honesty logic, ingestion, the web or iOS client, QA receipts, or deploy and release steps, or when diagnosing a rejected data refresh.
---

# Volta Go

Root `AGENTS.md` owns the honesty law, stdlib-only rule, source-transport exception list, authorization limits and external-action gates. `DEPLOY.md`, `RELEASE_RUNBOOK.md` and `GOAL_CONTRACT.md` own operations and release scope; `docs/design-system.md` is mandatory reading before UI, map or copy work; `README.md` has setup. Do not restate them; if this skill conflicts with AGENTS.md, AGENTS.md wins. This skill adds the standing owner rules that constrain code, the map of where logic lives, and traps with their reasons.

Product name is Volta Go; `Volta` stays in source, scheme, bundle (`tech.arsik.volta`), env and service identifiers. Spoken "River" means REVE / MapaREVE.

## Rules that constrain code

- Never invent chargers, prices, availability, reviews or start methods. Coverage is an ambition, never a claim of completeness; copy must not imply coverage outside Spain (mainland, Balearics, Canaries, Ceuta, Melilla).
- Keep evidence classes structurally distinct: live, official, directory, community, cached, inferred. Fail closed on stale or conflicting evidence. Community reports may annotate or downgrade; they never upgrade a charger to live. A newer build timestamp does not make old observations live; keep original source-fetch timestamps on cached evidence.
- Live authority comes only from fresh, successfully fetched REVE OCPI evidence. A configured adapter or healthy web server does not prove collection works. OpenStreetMap and OpenChargeMap rows are "Not live" leads and never become Go-now. Eligible Lidl evidence is official-policy evidence and stays subject to its source-specific rights hold in `docs/appstore/source-rights-inventory.md`.
- Source exclusions live in `data/source_policy_exclusions.json`: Electromaps, Chargemap and Consum stay excluded until reuse is permitted. Feníe is the single `granted_reuse` record (owner-attested redistribution basis, not independently verified; withdrawing it restores the exclusion). Do not present Feníe as independently verified and do not lift any other exclusion.
- Transport: GET by default. The only non-GET exceptions are the MapaREVE bbox query and the Feníe marker list, enforced in `scripts/source_transport.py` (exact HTTPS host and path, bounded body, no redirects, no credentials, rolling budget; anything else raises `SourcePolicyBlocked`). This is a narrow allowance, not general POST permission; it never covers login bypass, charging actions, payments, or lifting another source's restriction. Prefer GET (for example Overpass `?data=`). Test both allowed queries and denied wrong-host, wrong-path and write-like requests. `DEPLOY.md` and `RELEASE_RUNBOOK.md` still contain older Feníe "link-only / GET-only" wording; `AGENTS.md`, `source_transport.py` and `source_policy_exclusions.json` are current.
- Product: first public release is free and `PaywallConfig.enabled` (`ios/Volta/Volta/PaywallConfig.swift`) stays a literal `false`. Safety and honesty labels are never gated by it. Finding a charger is core; starting or paying for a session stays outside Volta unless a verified source lists that method.
- Default first paint includes lawful paid and unknown-price locations; Free and ≤ 0.25 EUR/kWh are optional chips, not the default gate. Web default has `verified/tesla/parking/free/cheap` off and `leads` on; iOS `maxPrice` defaults to 5.0 (`AppState.swift`). `lead_map_eligible: 0` or those filters on hide honest rows.
- A charger its own source marks out of service (Feníe `estado` 4) stays listed with a `source_reported_unavailable` dict; `decision_for` in `server.py` returns `avoid` / "Unavailable" for it before any price or live logic. Dropping such rows breaks the zero-missing-id gate whenever the source takes a charger offline.
- Provider handoff: cards show navigation ("Open in Maps") and, separately, an operator handoff opening the operator's own app or site. The URL comes from the ingest operator-to-app table (`NAP_OPERATOR_APPS` in `scripts/ingest_sources.py`) via `providerAppUrl` in `frontend/src/lib/provider.ts`. Omit the button when no public app or site exists; never fabricate a link; never auto-open on card tap.
- Pin click opens a sticky on-pin card (status, price, speed, plugs, access, Maps, source), not a price-only bubble, and must not resize the MapLibre container on selection (hide the browse panel with `invisible` / `pointer-events-none`).
- Native iOS already uses MapKit; web uses MapLibre with OpenFreeMap tiles. Verify current code before proposing a map-provider change. iOS is the priority surface.
- Acceptance direction from the owner: real-driver sessions (TC-051) and manual VoiceOver (TC-056) are not prerequisites for completing the agreed pilot work. Keep accessibility work and automated accessibility checks, never invent physical-driving or VoiceOver proof, and never mark TC-051/TC-056 passed without authentic evidence (AGENTS.md). Update acceptance criteria explicitly instead. This does not authorize a public release.

## Where things live

| Concern | Location |
|---|---|
| API and decision logic | `server.py` (`/api/chargers`, `/api/health`, `/api/intel`, `/api/geocode`; `decision_for`, `clamp_bbox`, viewport resolution, overview response capped at 2,500 rows by score), `station_truth.py`, `community.py` |
| Truth contract shared by clients | `contracts/station_truth_cases.json` exercised from Python, TypeScript and Swift; `docs/backend-engine.md` |
| Source policy and transport | `scripts/source_policy.py`, `scripts/source_transport.py`, `data/source_policy_exclusions.json` |
| Ingestion and merge | `scripts/ingest_sources.py` (`dedupe`, `merge_cross_source_duplicates`, `record_merged_ids`, `apply_source_policy_exclusions_to_payload`), `scripts/station_identity.py` (`identity_keys`, `preserve_record_ids`) |
| Data refresh | `scripts/auto_refresh.py` (Git-committing weekly flow), `scripts/runtime_refresh.py` (external gated runtime bundle), `scripts/qa_ingestion_delta.py`, `scripts/qa_ingestion_canary.py`, canonical `data/free_intelligence.json` |
| Web client | `frontend/src/` (`App.tsx`, `components/`, `lib/api.ts`, `lib/format.ts` decision labels, `lib/discovery.ts`, `lib/provider.ts`) |
| iOS client | `ios/Volta/Volta/` (`AppState.swift`, `Models.swift`, `StationViews.swift`, `Localizable.xcstrings`), tests `ios/Volta/VoltaTests/`, UI tests `ios/Volta/VoltaUITests/`; see `ios/README.md` |
| Tests | `tests/test_*.py` (stdlib unittest), `frontend/src/**/*.test.ts(x)` (vitest), `scripts/qa_*.py|js` browser and API contracts |
| QA receipts | `qa/` (source-bound; never hand-edit) |
| Deploy | `Dockerfile`, `fly.toml` (fallback host), `deploy/systemd/`, `deploy/launchd/` (disabled templates), `DEPLOY.md` |

## Commands

Python 3.14 and Node 26 per `.python-version`, `.node-version`, `README.md`. Backend needs no packages.

- Build UI then run: `npm --prefix frontend ci && npm --prefix frontend run build && PORT=8773 PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -u server.py` (startup requires `frontend/dist`).
- Frontend dev: `VITE_API_TARGET=http://127.0.0.1:8773 npm --prefix frontend run dev`.
- Focused: `python3 -m unittest discover -s tests -p 'test_<area>.py'`; `npm --prefix frontend run test -- src/lib/<area>.test.ts`.
- Offline gate: `./scripts/verify.sh`; with lint and build: `./scripts/verify.sh --full`. It does not run browser contracts or external canaries and says so; never imply they ran.
- Browser tooling (root package, system Chrome or `VOLTA_QA_CHROME`): `python3 scripts/qa_smoke.py`, `node scripts/qa_maplibre_runtime.js http://127.0.0.1:8773`, `python3 scripts/qa_regression_suite.py --base-url http://127.0.0.1:8773`. Check each script's arguments first; some rewrite tracked reports under `qa/`.
- Native: follow `ios/README.md` (scheme `Volta`, Simulator destinations, serial StoreKit tests). Reuse a booted compatible Simulator; shut down only devices you started.


## Verifying a change on the real surface

1. **Data path, not just health.** `/api/health` `ok: true` proves a response, not live-source coverage. Probe `/api/chargers` with `bbox=south,west,north,east` (parameters like `sw_lat` or `ne_lng` are silently ignored and fall back to the default Valencia viewport; assert `meta.viewport.source == "explicit"` before reading counts). Use `include_leads=true`, an ordered regional bbox, and both `max_price=5.0` and `max_price=0.25`. Omit `energy_price_max` from the REVE query when `max_price >= 1.0`. Details and honesty expectations: [references/api-evidence.md](references/api-evidence.md).
2. **Rendered page.** After a UI or data change, load the built app and check the entry JS is the build you expect, the map mounts on OpenFreeMap, a city search works, and clicking a pin keeps its card open with speed, plugs and charger count. Hidden or background tabs never fire `requestAnimationFrame`, so the map will not mount there; use a foreground browser page (Playwright headed or the repo's QA scripts) before concluding the map is broken.
3. **Clamp order.** `clamp_bbox` in `server.py` must never emit inverted south/north or west/east pairs; island Overpass tiles are separate boxes, not one Atlantic rectangle.
4. **Strings.** New Release-visible iOS literals need en/es/ru entries in `ios/Volta/Volta/Localizable.xcstrings` with `extractionState: manual`. Grep `tests/test_ios_design_contract.py` before changing any `accessibilityHint`; retarget `frontend/src/lib/format.test.ts`, `api.test.ts` and `DecisionHonestyTests.swift` when defaults change.
5. **Honest empties.** Cheap-only Tenerife live can be 0 while all-public live is dozens (island prices often sit near 0.28 to 0.30 EUR/kWh); report both counts. Ceuta and Melilla empty on REVE stays empty. Never explain a missing charger from proximity: trace its id (see [references/data-pipeline.md](references/data-pipeline.md)).
6. **Receipts.** Any change under the regression hash (source, tests, config, workflows, canonical data, built frontend) invalidates QA receipts; regenerate with the README sequence and commit them on the same branch. See [references/verification-and-receipts.md](references/verification-and-receipts.md).

## Data pipeline

Merge rules, the delta gate (`missing_ids=0` is never relaxed), identity keys, tariff-conflict handling, and the full procedure for diagnosing a rejected refresh: [references/data-pipeline.md](references/data-pipeline.md). Headlines:

- Same-family rows never merge, directly or through a third row.
- Every merge stays traceable via `merged_ids`; cross-family merges also carry `merged_source_identifiers` so `identity_keys` can prove the old id is not lost.
- Rebuild to a candidate and pass delta and canary checks before promotion; never hand-edit the bundle, receipts or gates to get a refresh through.
- Served data comes from `VOLTA_INTELLIGENCE_DB` when set, otherwise from `data/free_intelligence.json`. A code change that touches `data/` or the pipeline does not change what drivers see until a refresh runs on the new code.

## Deploy and release

- This repo's `DEPLOY.md` documents local run, container, Fly.io and tunnel options, and the gated refresh flows. Production `voltago.app` is served by the owner's own host through a Cloudflare tunnel; `fly.toml` (app `volta-vlc`, a legacy id) is only a fallback host with community writes disabled. Fly-only config changes (memory, machine size) never affect `voltago.app`.
- Merging to `main` does not deploy. Production and pilot staging are separate systems: verify host and source bindings before any live probe, and never replay a refused probe by switching route or actor.
- Owner-only operations (SSH to the host, timers, on-demand deploy, rollback) are documented outside this public repository.
- Keep local checks, deployment, live collection, cached-data generation, scheduled execution, archive, upload, Apple processing, group assignment, device install and public release as separate claims, per AGENTS.md. A version-only question needs no rebuild, restart, probe or tracker change.
- Release authority comes from the current assignment and recorded approvals. A general "continue" or "all approved" covers the decisions just presented, not production, TestFlight, App Store Connect or public-store mutations.
- Before escalating an App Store Connect access problem, check the installed `asc` CLI help and `asc auth status` (non-verbose). Keychain-based authentication is independent of any browser login. Do a bounded read-only lookup of the exact bundle; app-list access does not imply agreements or DSA access.
- For App Store prep, compare current native source with every template in `docs/appstore/`. Community submissions, photos, watch tokens and AI review can invalidate old no-upload or privacy prose even while public URLs return 200. Treat plain-client HTTP failures (a single 403) separately from native or browser reachability; do not infer an outage or bypass a security policy.

## Reporting

State what was implemented, verified (command and result), blocked and deferred. Distinguish fetched rows from live-verified rows from route-ready rows, and raw adapter rows from merged dataset rows. Report skipped checks and unresolved gates exactly.

<!-- Ported from the owner's Hermes skill volta-ios-release on 2026-10-11. Keep in sync deliberately; repo AGENTS.md wins on conflict. -->
