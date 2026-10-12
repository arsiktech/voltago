# Volta Go Code Wiki

> **Provenance notice:** this wiki was generated 2026-07-06 at commit `5706893`.
> The API, architecture, deploy, ingestion, and workflow sections were manually
> reconciled with repository truth on 2026-07-13. Use current source for module
> details and the root README for setup; this wiki is historical context.

Volta Go is a local-first EV-charging decision tool for Spain. It combines a Python
stdlib API/data engine with a React/Vite single-page app, source-backed static
intelligence, and a QA evidence harness. The product goal is not to list every
charger; it is to tell the driver whether a cheap or free Tesla-compatible stop
is usable now, with live, official, directory, and community evidence kept
visibly distinct.

This wiki was generated from the repository at commit
`5706893e1fe24ddcc7cd50d2cd4589f7dbaa44e1`. Start with the root
[README.md](../../README.md) and [AGENTS.md](../../AGENTS.md), with
deployment details in [DEPLOY.md](../../DEPLOY.md) and frontend details in
[frontend/README.md](../../frontend/README.md).

## Key Concepts

- **Data honesty** - live availability, source corroboration, charge claims, and
  parking claims are separate concepts.
- **Viewport-first Spain coverage** - `/api/chargers` resolves a bbox, geocoded
  place, or user location instead of relying on a fixed Valencia region.
- **Intelligence-first render** - the UI can show source-backed leads while the
  slower live fetch runs, but those rows stay marked `Not live`.
- **Fail closed** - when live data fails, stale `Go now` cards are not retained
  as current availability.
- **Public sources only** - integrations are GET public pages/APIs or documented open
  datasets, plus two owner-approved read-only data-query POSTs (MapaREVE bbox query, Feníe
  marker list) enforced in `scripts/source_transport.py`; private app/session scraping is
  out of bounds. Candidate sources across Europe (discovery only, not a reuse licence):
  [source catalog](../source-catalog-europe-2026-10-12.md).

## Entry Points

- [server.py](../../server.py) - Python stdlib HTTP server, JSON API, live source
  adapters, source-honesty enrichment, and static SPA serving.
- [frontend/src/main.tsx](../../frontend/src/main.tsx) - React root and TanStack
  Query provider.
- [frontend/src/App.tsx](../../frontend/src/App.tsx) - SPA orchestrator for map,
  filters, search, live/overview modes, and fail-closed data selection.
- [scripts/ingest_sources.py](../../scripts/ingest_sources.py) - non-destructive
  public-source intelligence database builder.
- [scripts/qa_regression_suite.py](../../scripts/qa_regression_suite.py) - master
  QA runner and evidence report generator.
- [ios/Volta/Volta/VoltaApp.swift](../../ios/Volta/Volta/VoltaApp.swift) - native
  iOS client entry point.

## High-Level Architecture

The Python server is the contract boundary: it serves `/api/*`, enriches every
station/lead with decision, provenance, parking, corroboration, and coverage
metadata, and serves the required built SPA from `frontend/dist`; startup fails
if the build is absent. The React
SPA queries that API, keeps the map as an enhancement, and renders an honest
driver-facing card list. Background intelligence is built by scripts into
`data/free_intelligence.json`; QA scripts and CSV/JSON artifacts encode the
completion contract.

See [architecture.md](architecture.md) and [diagrams/sequences.md](diagrams/sequences.md).

## Module Map

| Module | Purpose |
|---|---|
| [backend-server](modules/backend-server.md) | HTTP serving, API routes, caching, and response assembly. |
| [data-honesty-engine](modules/data-honesty-engine.md) | Decision, verification, parking, pricing, corroboration, and rejection logic. |
| [live-sources](modules/live-sources.md) | Public live adapters for MapaREVE/Gaia, Electromaps promotion, and retired-source policy gates. |
| [frontend-spa](modules/frontend-spa.md) | React app orchestration, data fetching, filters, live/overview modes. |
| [frontend-components](modules/frontend-components.md) | Map, virtualized list, station cards, source provenance, filters, banners. |
| [intelligence-ingestion](modules/intelligence-ingestion.md) | Public-source intelligence build, dedupe, and safe refresh workflow. |
| [data-artifacts](modules/data-artifacts.md) | Canonical JSON data, caches, source probes, and QA artifact surfaces. |
| [qa-regression](modules/qa-regression.md) | Unit, browser, smoke, canary, manual-gate, and artifact-integrity checks. |
| [deploy-runtime](modules/deploy-runtime.md) | Local, tunnel, Docker/container, Fly.io, and environment configuration. |
| [ios-client](modules/ios-client.md) | SwiftUI/MapKit client consuming the same backend API. |

## Unknowns And Risky Areas

- Browser QA contracts are sensitive to SPA DOM selectors and virtualized cards;
  selector drift should be updated in [docs/spa-dom-contract.md](../spa-dom-contract.md).
- Live providers can rate-limit or challenge datacenter IPs, especially cloud
  deploys. Residential/local tunnel remains the most reliable live path.
- `data/free_intelligence.json` is large and source-derived; never overwrite it
  without the non-destructive delta/canary path.
- Completion remains intentionally incomplete until physical field test and
  native VoiceOver evidence exist.

## Getting Started

See [getting-started.md](getting-started.md).
