# Volta Go

Help a driver find free chargers first, cheap options next, and decide
whether a stop is usable now. Keep availability, charging price, parking cost,
provenance, and uncertainty clear. Product name: **Volta Go**; `Volta` remains the
existing source, scheme, bundle, environment, and service identifier.
Worldwide directory coverage grows through reviewed source adapters. Live feeds
retain their actual regional scope; never equate a worldwide catalogue with live coverage.

## This repository is public

Keep private material out of files, commits, issues and pull requests: machine
paths, hostnames, private IP addresses, credentials, private tracker links and
task contents. Generated QA receipts stay local because they record machine
paths; `.gitignore` keeps them out, and `tests/test_public_hygiene.py` checks
tracked files.

## Work autonomously

Carry the user's request through implementation and relevant verification. Choose
routine technical details, edit code directly, and fix related issues without
asking for approval at each step. Prefer deleting unnecessary complexity, then
simplifying it. Use additional agents when an independent task benefits from them;
there is no fixed model roster, dispatch sequence, or mandatory review ceremony.

The current request sets scope. Read only the files needed for it. Historical
plans are context: reconcile outdated instructions with current code and proceed.
Preserve unrelated working changes; a dirty checkout alone is not a blocker.
Update docs and QA records when their facts or covered behavior change, rather
than after every edit. Report the result, checks run, and material remaining gaps.

## Project map and checks

- `server.py`, `station_truth.py`, `community.py`: Python stdlib API and data engine.
- `frontend/src/`: React/TypeScript web client; `ios/Volta/`: native SwiftUI client.
- `data/`: source intelligence and local community storage; `scripts/`, `tests/`,
  `contracts/`, `qa/`: ingestion, verification, fixtures, and evidence.
- [README.md](README.md): setup and commands. Start with focused checks;
  `./scripts/verify.sh` runs offline Python/frontend tests, and `--full` also runs
  frontend lint/build. Run browser or native checks when the affected path needs
  them. Docs-only edits need link/command checks and `git diff --check`.
  Its receipt-refresh sequence explains the source-bound artifact checks; do not
  edit a receipt or suppress an integrity failure to make verification pass.
- [GOAL_CONTRACT.md](GOAL_CONTRACT.md) and [plans/README.md](plans/README.md): product
  goals and work references. Plans and QA reports are dated snapshots;
  verify relevant claims against current source or live evidence before relying on them.
- Read [docs/design-system.md](docs/design-system.md) before any UI, map, or
  user-facing copy work on web or iOS.
- Consult `ios/README.md` for native work, `docs/spa-dom-contract.md` for browser
  contracts, and `DEPLOY.md` / `RELEASE_RUNBOOK.md` for operations or release work.

## Product constraints

- Keep live, official, directory, community, cached, and inferred evidence distinct.
  Stale or unverified evidence cannot become live or route-ready. Community reports
  can annotate or downgrade a decision, never grant live authority. Track charging
  and parking provenance separately; keep network calls bounded and fallback labeled.
- Upstream integrations use public, user-verifiable GET surfaces. The only non-GET
  exceptions are two owner-approved read-only data queries (MapaREVE bbox query, Feníe
  marker list), enforced in `scripts/source_transport.py` (exact HTTPS host/path/body, no
  redirects, bounded budget); any other POST must raise `SourcePolicyBlocked`, so prefer
  GET (e.g. Overpass `?data=`) and never widen the allowlist without a new owner decision.
  Preserve source exclusions (Electromaps, Chargemap, Consum); no authenticated scraping,
  login bypass, or provider session/payment actions.
- Python backend/scripts/tests use only the standard library; iOS uses native
  frameworks only. Keep frontend dependencies purposeful and the list usable without maps.
- Rebuild intelligence to a candidate and pass delta/canary checks before promotion.
  Preserve user data and source evidence. Generate QA receipts with their tools;
  do not edit results or weaken behavioral checks to manufacture a pass.
- Keep `PaywallConfig.enabled` false until launch/pricing prerequisites are approved.
  Prices and trial terms come from StoreKit; safety and honesty are never paywalled.
- Reuse a compatible booted Simulator and incremental build state. Preserve device
  data; shut down only idle devices started for this task, using their exact UDIDs.

## External actions and evidence

Reuse authorization already given for the task. Without it, finish the local
preparation before asking for deployment, production data/settings, new credentials,
spend, Apple signing/distribution changes, or public release. Continue independent
work when one action is blocked.

Keep local checks, deployment, archive/upload, Apple processing, group assignment,
device installation, and public release as separate claims. `TC-051` field evidence
and `TC-056` native VoiceOver evidence require authentic manual proof; automated
checks cannot close them. Report skipped checks and unresolved gates accurately.
