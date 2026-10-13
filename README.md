# Volta Go

A free-first EV charging decision tool with worldwide directory support. A Python standard-library API
combines live availability with source-backed intelligence; React and native iOS
clients show charging price, parking conditions, freshness, and provenance.

Live app: [voltago.app](https://voltago.app). The worldwide expansion in this checkout
is a local candidate, not evidence of a production rollout. Live integrations retain
their regional coverage; a directory record does not establish current availability.

This repository ships a small sample of charger records so tests and local runs
work offline. Build the full dataset from public sources with
`python3 scripts/ingest_sources.py`; see [data/README.md](data/README.md).

For worldwide candidates, use `python3 scripts/build_global_intelligence.py
--all-countries --sources ocm`. The build preserves its baseline, records per-source
coverage and licences, and never promotes itself. See the [worldwide source matrix
and runbook](docs/global-coverage.md) for additional adapters, validation and local serving.

## Run locally

Use Python **3.14.8**, Node.js **26.10.0** and npm **12.2.0**, matching the CI and
Docker toolchains. `.python-version`, `.node-version` and the package manifests
record these versions. Node 26 is the stable Current release line; Xcode 27 with
the iOS 27 SDK is the native toolchain. The backend needs no Python packages.
From the repository root:

```bash
npm --prefix frontend ci
npm --prefix frontend run build
PORT=8773 PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -u server.py
```

Open `http://127.0.0.1:8773`. Build the frontend before starting the server, even
for API development: startup requires `frontend/dist`.

For frontend development, keep that backend running and use a second terminal:

```bash
VITE_API_TARGET=http://127.0.0.1:8773 npm --prefix frontend run dev
```

Vite normally serves `http://127.0.0.1:5183`; check its output for the actual port.
Its default API target, without the override, is port 8799.

## Verify the affected work

| Change | Check |
| --- | --- |
| Python behavior | `python3 -m unittest discover -s tests -p 'test_<area>.py'` |
| Frontend behavior | `npm --prefix frontend run test -- src/lib/<area>.test.ts` |
| Combined offline check | `./scripts/verify.sh` |
| Offline check plus frontend lint/build | `./scripts/verify.sh --full` |
| Browser/API behavior | Run the relevant `scripts/qa_*.js` or `scripts/qa_*.py` against the built app |
| Web map, worker, graphics fallback | `node scripts/qa_maplibre_runtime.js http://127.0.0.1:8773` |
| Native iOS | Follow [ios/README.md](ios/README.md) for the relevant build/test target |
| Documentation | Check changed links/commands and run `git diff --check` |

Substitute the affected test filename in focused commands. Run the broader suite
when integration risk warrants it; offline verification does not include browser,
external-source, native, or manual evidence.

[PR CI](.github/workflows/verify.yml) installs the lockfiles, builds the production
frontend, runs the unchanged `verify.sh --full` gate, and checks for high/critical
dependency advisories on Ubuntu 26.04. It also checks the real MapLibre worker,
cluster/theme behavior, attribution sanitization, map-free recovery, and the built
container's Python runtime, Madrid timezone and HTTP responses. It retains
verification logs for seven days. The build runs first because artifact integrity
checks include `frontend/dist`. QA receipts are generated locally and not tracked here. CI does not
regenerate evidence or establish native, full browser-suite, external-source, or
release readiness. The focused map test uses deterministic fixtures and no external
map services; run the full browser regression suite locally for broader journeys.

Browser tooling uses the root package and defaults to installed Chrome locally.
CI installs Playwright's Chromium and sets `VOLTA_QA_CHROME` to its executable:

```bash
npm ci
python3 scripts/qa_smoke.py
python3 scripts/qa_regression_suite.py --base-url http://127.0.0.1:8773
```

Check each QA script's arguments and output path before running it: some regenerate
reports under `qa/`, which stay local (see `.gitignore`). Browser setup and selectors are documented in
[docs/spa-dom-contract.md](docs/spa-dom-contract.md). Production release verification
is described in [RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md).

### Refresh source-bound QA receipts

The regression hash covers source, tests, configuration, workflow files, canonical
data, and the built frontend. Finish those edits and build before recording final
evidence. For a source change that invalidates receipts, use the generators below
from the repository root with the built app running at `http://127.0.0.1:8773`.
Keep the previous evidence before the generators replace the `latest` files:

```bash
volta_qa_snapshot="$(mktemp -d /tmp/volta-qa-before-refresh.XXXXXX)"
cp -R qa "$volta_qa_snapshot/"
npm ci
npm --prefix frontend ci
npm --prefix frontend run build
```

Refresh the local candidate packet, then generate an explicitly partial bootstrap
receipt to break the suite's dependency on its previous receipt:

```bash
python3 scripts/qa_field_test_packet.py --base-url http://127.0.0.1:8773 --require-candidate-match --out qa/field-test-candidates.md
python3 scripts/qa_field_packet_freshness.py --base-url http://127.0.0.1:8773 --out qa/field-packet-freshness-latest.json
python3 scripts/qa_manual_proof_status.py --out qa/manual-proof-status.json
python3 scripts/qa_regression_suite.py --base-url http://127.0.0.1:8773 --bootstrap-artifacts --skip-browser
python3 scripts/qa_completion_audit.py --out qa/completion-audit-latest.json
python3 scripts/qa_artifact_integrity.py --out qa/artifact-integrity-latest.json
python3 scripts/qa_completion_audit.py --out qa/completion-audit-latest.json
```

Inspect each exit status and report; resolve failures before advancing. Bootstrap
is partial evidence and cannot establish completion. Run the actual final gates
with source and build output unchanged:

```bash
./scripts/verify.sh --full
python3 scripts/qa_regression_suite.py --base-url http://127.0.0.1:8773
python3 scripts/qa_artifact_integrity.py --out qa/artifact-integrity-latest.json
python3 scripts/qa_completion_audit.py --out qa/completion-audit-latest.json
```

Preserve a failing run and report its `failed_ids`; do not replace its result by
hand or describe a partial run as green. After a failure, the artifact and
completion audits still provide useful diagnostics. A new source change or a
different build requires fresh evidence. The default regression run excludes
external canaries; the local API may still perform its normal permitted upstream
GETs. Add `--include-external` only for that intended check scope. A consistent
completion report can exit successfully with `complete=false`; `--strict` also
requires full completion. TC-051/TC-056 need authentic manual evidence.

## Repository map

| Path | Purpose |
| --- | --- |
| `server.py`, `station_truth.py`, `community.py` | API, decision truth, and community evidence |
| `frontend/` | React/Vite web client and PWA assets |
| `ios/Volta/` | SwiftUI/MapKit client, StoreKit support, and native tests |
| `data/` | Sample charger data ([data/README.md](data/README.md)), curated leads, source exclusions, local user data |
| `scripts/`, `tests/`, `contracts/` | Ingestion, QA, unit tests, shared truth fixtures |
| `qa/` | Test/defect ledgers and manual-test checklists; generated evidence stays local |
| `deploy/` | Runtime/scheduler templates |
| `docs/`, `plans/` | Technical references, release preparation, and historical work |

## Where to change behavior

| Behavior | Start here | Focused check |
| --- | --- | --- |
| Driver search, filters, result layout | [frontend/src/App.tsx](frontend/src/App.tsx), [components](frontend/src/components/), [discovery.ts](frontend/src/lib/discovery.ts) | Frontend discovery/format tests, then affected [browser contracts](docs/spa-dom-contract.md) |
| Visual tokens and motion | [index.css](frontend/src/index.css), [motion.ts](frontend/src/lib/motion.ts) | `python3 scripts/qa_accessibility_spa.py`, mobile/light/dark browser checks |
| Request cancellation and cache identity | [api.ts](frontend/src/lib/api.ts) | `npm --prefix frontend run test -- src/lib/api.test.ts` |
| Live authority, price, parking and routing | [station_truth.py](station_truth.py), [server.py](server.py), [shared fixtures](contracts/station_truth_cases.json) | `python3 -m unittest discover -s tests -p 'test_station_truth_contract.py'`; frontend format and native decision tests |
| Public-source transport and fallback | [server.py](server.py), [source_policy.py](scripts/source_policy.py) | `python3 -m unittest discover -s tests -p 'test_filter_ranking_correctness.py'` |
| Native discovery and filters | [SheetView.swift](ios/Volta/Volta/SheetView.swift), [DiscoveryControls.swift](ios/Volta/Volta/DiscoveryControls.swift), [DiscoveryFilters.swift](ios/Volta/Volta/DiscoveryFilters.swift), [AppState.swift](ios/Volta/Volta/AppState.swift) | `DiscoveryFilterTests` in Simulator; Python `test_ios_design_contract.py` |
| Native station facts and actions | [StationViews.swift](ios/Volta/Volta/StationViews.swift), [StationPresentation.swift](ios/Volta/Volta/StationPresentation.swift), [StationActions.swift](ios/Volta/Volta/StationActions.swift) | Native decision/presentation tests and affected Simulator journeys; see [ios/README.md](ios/README.md) |
| QA orchestration and source binding | [qa_regression_suite.py](scripts/qa_regression_suite.py), [qa_revision.py](scripts/qa_revision.py) | `python3 -m unittest discover -s tests -p 'test_regression_suite.py'`; regenerate receipts after edits |

## Find the relevant context

- Agent workflow and constraints: [AGENTS.md](AGENTS.md).
- Product goals: [GOAL_CONTRACT.md](GOAL_CONTRACT.md).
- Work references: [plans/README.md](plans/README.md). Completed plans retain their
  original paths for audit links; their old execution protocols are retired.
- Current state: establish it from Git, the active PR/CI results, and locally
  generated QA receipts; dated plans and reports are not current verification.
- Client setup: [frontend/README.md](frontend/README.md), [ios/README.md](ios/README.md).
- Backend: [docs/backend-engine.md](docs/backend-engine.md); source integration:
  [docs/source-research-2026-07-17-free-first.md](docs/source-research-2026-07-17-free-first.md).
- Operations and data refresh: [DEPLOY.md](DEPLOY.md).
- Release: [RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md), [docs/appstore/README.md](docs/appstore/README.md).

Data refresh builds a candidate and checks its delta/canary before promotion. Use
[DEPLOY.md](DEPLOY.md) for the existing dedicated-checkout workflow. Preserve local
community data, caches, QA receipts, and unselected design assets.

## License

Copyright © 2026 arsiktech. All rights reserved. The source is published so people
can read it and follow development; no license to copy, modify or redistribute it
is granted. Charger data keeps the terms of its original sources (see
[data/README.md](data/README.md)).
