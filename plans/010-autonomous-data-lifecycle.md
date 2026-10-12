# Plan 010: The charger database lives on its own — unattended refresh with fail-closed gates, auto-commit, service supervision

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [AGENTS.md](../AGENTS.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

> **Final-convergence reconciliation (2026-07-13)**: the owner explicitly approved
> the brief unload, rollback backup, and persistent replacement. The host migration
> is now complete and exercised from the dedicated checkout. No health timer is
> authorized without a durable public HTTPS service URL.

## Status

- **Priority**: P1 for the owner's stated goal ("the app and its charger DB should live automatically"); technically last in execution order
- **Effort**: M
- **Risk**: MED (an unattended pipeline that pushes commits must be aggressively fail-closed; every promote gate below already exists in the repo — this plan wires them together, it does not invent new trust)
- **Depends on**: plans/002 (hygiene/gitignore), plans/004 (resilience) and plans/005 (public hardening) before public + unattended operation; plans/001 for the verify gate
- **Category**: dx / direction
- **Planned at**: commit `ceee003`, 2026-07-12 — owner mandate 2026-07-12: "всё должно быть автономно и без моего ручного управления… чтобы приложение и его база зарядок жила автоматически"
- **Implemented and locally verified**: At the 2026-07-13 implementation
  checkpoint, the exact-candidate dry run,
  fail-closed transaction/race/hook tests, health probe, and disabled templates
  are complete. The final generated audits are coherent, `./scripts/verify.sh
  --full` is green, and the completion-grade regression is 30/30 with browser
  and external families enabled. Repository implementation is complete.
- **Implementation reconciliation (2026-07-13)**: the ignored
  `data/cache/auto-refresh/` work directory deliberately supersedes the plan's
  original `qa/auto-refresh-last.json` and `/tmp/volta-auto-refresh.lock`
  literals. Real and dry runs write separate mode-0600 receipts (`last.json`
  and `dry-run-last.json`) so a dry run cannot overwrite health evidence, and
  the default lock is `data/cache/auto-refresh/refresh.lock`. Database
  staleness is measured from the payload's timezone-aware `generated_at`, not
  filesystem mtime, so copying or restoring the file cannot make old data look
  fresh. Real runs now fetch and reconcile `origin/main` before capturing the
  transaction HEAD: behind checkouts fast-forward-only and reexec the fetched
  orchestrator under the inherited lock; divergence, remote rewind, fetch
  failure, and dirty-after-fetch races fail closed. Dry-run never fetches.
- **Host activation complete (2026-07-13)**: the dedicated checkout at
  `~/.local/share/volta-refresh` completed both a live dry run and
  multiple real transactional exact-SHA pushes. The owner-approved canonical
  `com.volta.refresh` LaunchAgent is loaded there with Monday 07:30 and
  `VOLTA_REFRESH_PUSH=true`; its first kickstarted run exited 0 and published
  `3a1d0cd2c35483a6f700e49ccaf0c66b85981625`. The legacy label is absent and its
  byte-identical rollback plist is preserved outside the autoload directory.

## Why this matters

Before this plan, the moat DB (`data/free_intelligence.json`) was refreshed by
a human running `scripts/refresh_intelligence.sh` and committing the result;
the server ran as a foreground process someone started. The owner's target is
zero-manual operation: refresh → validate → promote → commit → serve, on a
schedule, with the old DB kept on any failure and a notification going out.
Every safety gate already exists (non-destructive build to /tmp,
`qa_ingestion_delta.py`, `qa_ingestion_canary.py`, mtime-based hot reload in
the server, bounded canary history) — what's missing is the unattended
orchestration, the service supervision, and the alerting.

Deployment reality (from DEPLOY.md and AGENTS.md §4): live scrapers can be
rate-limited/challenged from datacenter IPs, so the most reliable topology is
a **residential/home always-on host behind a Cloudflare Tunnel** — the same
pattern the owner already runs for another project. This
plan's artifacts must therefore be host-agnostic: macOS launchd AND
Linux systemd/cron templates.

## Current state

Verified on disk 2026-07-12 (read each file before editing — this plan was
written from AGENTS.md/DEPLOY.md and the scripts listing; the executor must
read `scripts/refresh_intelligence.sh` fully first):

- `scripts/refresh_intelligence.sh` — the existing refresh wrapper (weekly
  cadence per DEPLOY.md). Read it: it likely already chains ingest →
  delta — whatever it does is the base; do not duplicate its logic.
- `scripts/ingest_sources.py --out /tmp/volta-intelligence.json` —
  non-destructive build (AGENTS.md §4: "NEVER write straight over
  data/free_intelligence.json").
- `scripts/qa_ingestion_delta.py` — promote gate #1 (expect `ok=true`,
  `missing_ids=0`).
- `scripts/qa_ingestion_canary.py` — promote gate #2 (tolerates rate limits
  with explicit warning evidence; writes bounded history
  `qa/ingestion-canary-history.jsonl`).
- Server hot-reload: `_INTEL_RAW`/overview caches key on file mtime
  (`server.py:2827-2842`, `3052-3061`) — promoting a new DB file requires NO
  server restart.
- AGENTS.md §8: "data/free_intelligence.json must be committed promptly after
  a verified rebuild — an uncommitted DB inside this Syncthing-synced folder
  can be silently reverted."
- DEPLOY.md documents three deploy paths; the tunnel path is called "most
  reliable live data". `fly.toml` exists for the Fly alternative.
- Precedent for env-hook notification: the repo has none — keep it a
  generic, env-configured command (`VOLTA_NOTIFY_CMD`), NOT a hardcoded
  integration (no tokens in this repo, ever).
- Conventions: stdlib-only Python; every new env var documented in
  AGENTS.md §8; run-log discipline; never hand-edit `qa/*-latest.json`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Offline verify | `./scripts/verify.sh` | exit 0 |
| Dry-run the pipeline | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 scripts/auto_refresh.py --dry-run` | full pass, NO promote/commit, exit 0 |
| Real run (manual trigger) | `python3 scripts/auto_refresh.py` | promote+commit only if both gates pass |
| Health probe | `python3 scripts/health_probe.py --base-url http://127.0.0.1:8765` | exit 0 when /api/health ok |

## Scope

**In scope**:
- `scripts/auto_refresh.py` (create — the orchestrator)
- `scripts/health_probe.py` (create — tiny /api/health + staleness checker)
- `deploy/launchd/` and `deploy/systemd/` template files (create; NOT installed)
- `DEPLOY.md` — a new "Unattended operation" section
- `AGENTS.md` §8 — new env vars
- `qa/run-log.md`, `qa/test-cases.csv`, `qa/coverage-summary.md`
- `tests/test_auto_refresh.py` (create — gate-logic tests with mocked subprocesses)

**Out of scope** (do NOT do):
- Installing/enabling any launchd/cron/systemd job (prepare-only; owner enables).
- Any Cloudflare/Fly account action, DNS, tunnel creation.
- Changing ingest/delta/canary logic itself.
- Adding notification-service credentials of any kind to the repo.

## Git workflow

- `main`; imperative commits. The orchestrator's OWN auto-commits (at
  runtime) use a distinct prefix `data: weekly intelligence refresh` so
  history separates human/agent commits from pipeline commits. Push per
  AGENTS.md §8; the pipeline itself pushes only when
  `VOLTA_REFRESH_PUSH=true`.

## Steps

### Step 1: Read the existing pieces; write the orchestrator

Read `scripts/refresh_intelligence.sh`, `qa_ingestion_delta.py`,
`qa_ingestion_canary.py` argument conventions first. Then
`scripts/auto_refresh.py` (stdlib only):

1. For a real run, require the exact clean dedicated `main` checkout, fetch
   `origin/main`, and accept only equal history or a fast-forward. Reexec the
   fetched orchestrator under the inherited lock after a fast-forward so one
   process version owns the whole gate transaction. Reject fetch failure,
   divergence, remote rewind, and dirty/ref races. Dry-run performs no Git sync.
   A single verified local-ahead refresh is recoverable only through its
   mode-0600 `pending-push.json`; a `--push` retry publishes that pinned SHA
   before a new build, while arbitrary local commits remain rejected.
2. Build to a temp path (`--out /tmp/volta-intelligence.json`), never in-place.
3. Gate 1: run the delta check → require its documented success shape.
4. Gate 2: run the ingestion canary → require ok (rate-limit warnings are
   tolerated per its own semantics; hard failures are not).
5. Promote: atomic replace of `data/free_intelligence.json`
   (`os.replace` from a same-filesystem temp copy).
6. Generate the current completeness and Valencia audit reports from the exact gated
   candidate, require their embedded SHA-256 to match it, and transactionally promote
   DB + canary history + both reports. Commit exactly those four allowlisted paths with
   the pipeline prefix; push the pinned verified SHA iff `VOLTA_REFRESH_PUSH=true`.
   Refuse to trust a commit unless its command succeeded, its parent is the preflight
   HEAD, its changed paths stay inside the allowlist, and every committed blob matches.
   Remove rollback snapshots after verified success or a verified-commit push failure;
   retain them only when rollback itself is uncertain.
7. On ANY gate failure: leave the old DB untouched, write the mode-0600 real
   receipt at `data/cache/auto-refresh/last.json` ({ts, stage, ok:false,
   detail}), invoke `VOLTA_NOTIFY_CMD` (if set) with a short message argument,
   and exit non-zero. On success write the same receipt with ok:true. Dry runs
   use `data/cache/auto-refresh/dry-run-last.json` instead, preserving the last
   real-run evidence for the health probe.
8. `--dry-run`: everything except Git sync/promote/commit/push/notify.
9. Serialize with `data/cache/auto-refresh/refresh.lock` (or the explicit
   `--lock-file` override) so overlapping schedules can't double-run.

**Verify**: `python3 -m py_compile scripts/auto_refresh.py`;
`python3 scripts/auto_refresh.py --dry-run` → real output showing both gates
evaluated, no file/commit mutations (`git status` unchanged,
DB bytes/SHA-256 unchanged).

### Step 2: Gate-logic tests

`tests/test_auto_refresh.py` uses disposable real Git repositories to exercise
the complete transaction rather than mocking away its safety boundary. Its 27
tests cover both gate failures, exact candidate/baseline revalidation, lock
contention, clean/dedicated-checkout enforcement, exact allowlisting,
working/index/HEAD races, post-promotion rollback for expected and unexpected
exceptions, exact-candidate derived-report generation, four-artifact rollback,
hook isolation, rejection of a concurrent mixed commit, local commit preservation,
pinned gated-SHA push, bounded backup cleanup, and containment of a concurrent
unrelated HEAD commit. Repository-sync coverage includes up-to-date fetch,
fast-forward plus fetched-version reexec under the inherited lock, fetch failure,
divergence, dirty-after-fetch, remote rewind, untrusted local-ahead rejection,
durable repeated push retry, and proof that dry-run never mutates refs.

**Verify**: `python3 tests/test_auto_refresh.py -v` → 27/27 pass; full suite green.

### Step 3: Health probe + staleness alarm

`scripts/health_probe.py`: GET `/api/health` (base-url arg/env); exit 0 on
ok. Add `--max-db-age-days N` (default 10): warn/exit non-zero when
`data/free_intelligence.json`'s timezone-aware payload `generated_at` is older
— this catches a silently dead schedule without trusting copy-sensitive
filesystem mtime. Same `VOLTA_NOTIFY_CMD` hook on failure.

**Verify**: run against a locally started server → exit 0; with
`--max-db-age-days 0` → non-zero + message.

### Step 4: Scheduling templates (prepare-only)

- `deploy/launchd/com.volta.refresh.plist` (weekly, e.g. Mon 05:30 local) and
  `com.volta.health.plist` (every 30 min) — macOS.
- `deploy/systemd/volta-refresh.{service,timer}`, `volta-health.{service,timer}`,
  and `volta-server.service` (runs `python3 server.py` with restart=always,
  env `PORT`/`HOST`) — Linux.
- Each template header-comments the exact install command
  (`launchctl bootstrap` / `systemctl --user enable --now`) and states it
  must not be enabled without the owner's go.

**Verify**: `plutil -lint deploy/launchd/*.plist` → OK;
`systemd-analyze verify deploy/systemd/*.service 2>&1 || true` (only on a
systemd host; otherwise record skipped).

### Step 5: Document the autonomous topology

New DEPLOY.md section "Unattended operation": recommended topology =
always-on home host (residential IP — the scrapers' friendliest origin, per
the existing datacenter-IP caveat) running `volta-server` + weekly
`auto_refresh` + health timer, exposed via the already-documented Cloudflare
Tunnel path; Fly/container listed as the fallback with its known scraping
caveat. Document `VOLTA_NOTIFY_CMD`, `VOLTA_REFRESH_PUSH`, and the separate
real/dry receipts under `data/cache/auto-refresh/`. Add the env vars to
AGENTS.md §8.

**Verify**: `grep -n "VOLTA_NOTIFY_CMD" DEPLOY.md AGENTS.md` → both hit.

### Step 6: Activate only at the persistent-host approval gate

The original plan produced the exact per-host enable commands without changing
persistent host state. Final convergence additionally requires migrating the
already-loaded legacy macOS job to the dedicated checkout after the managed
runtime's risk-specific approval. Preserve the owner's Monday 07:30 cadence,
set `VOLTA_REFRESH_PUSH=true`, exercise the new job once, and keep rollback
evidence until the new receipt and clean remote-tip checks pass.

**Verify**: `com.volta.refresh` is loaded from the dedicated checkout,
`com.volta.intelligence-refresh` is absent, the exercised receipt is mode 0600
and green, the dedicated checkout is clean at `origin/main`, and no health job
is enabled without a durable public URL.

## Test plan

- `tests/test_auto_refresh.py` (step 2) — the fail-closed promote logic.
- One REAL `--dry-run` against the live network recorded in the run-log
  (expected: gates evaluated on a fresh /tmp build; if upstreams rate-limit,
  the canary's warning semantics apply — record honestly).
- Existing `tests/test_ingestion_canary.py` / `test_ingest_merge.py` remain
  the gates' own coverage — untouched.

## Done criteria

- [x] `auto_refresh.py --dry-run` full pass recorded; real run promotes ONLY on green gates
- [x] Gate-failure paths proven by tests (no promote, notify fired, old DB intact)
- [x] Health probe + staleness alarm working; templates lint clean
- [x] Original prepare-only enable commands and disabled templates documented
- [x] Loaded legacy LaunchAgent replaced and exercised from the dedicated checkout
- [x] DEPLOY.md "Unattended operation" section + AGENTS.md env vars documented
- [x] Consolidated verification: `./scripts/verify.sh --full` passes (448 backend tests,
  one documented suite skip; 97 frontend tests; lint/build), all 27 lifecycle tests pass,
  the completion-grade regression
  passes 30/30 with browser and external families enabled, and generated integrity/completion
  audits are coherent. TC rows, run-log, and coverage-summary are current.
- [x] plans/README.md records the completed persistent-host migration

## STOP conditions

Stop and report back (do not improvise) if:

- `refresh_intelligence.sh` already implements part of this orchestration —
  extend it or supersede it explicitly, but report the overlap first if the
  reconciliation isn't obvious.
- The delta/canary scripts' success-output shape differs from what their
  names/AGENTS.md imply (never guess a gate's pass condition — read the
  script; a wrong pass condition here = silent data corruption forever).
- The repo has uncommitted human changes at pipeline-commit time in your
  tests' simulated flow AND the dirty-guard design proves incompatible with
  the Syncthing reality (e.g. qa artifacts churn constantly) — report; the
  guard may need an allowlist, which is an owner-visible decision.
- Anything requires a credential.

## Maintenance notes

- When plans/007 lands, its mtime-keyed enriched cache makes DB promotion
  invisible to latency; nothing to coordinate.
- If the DB repo-growth policy ever changes (see the deferred DEBT-06 note in
  plans/README.md — e.g. moving snapshots out of git), the commit step here
  is the single place to update.
- The notify hook is intentionally a generic command: wiring it to Telegram/
  Hermes/etc. happens OUTSIDE this repo with the owner's own tooling.
- Future direction fit: the national free-charger expansion (direction item
  4) plugs into this pipeline as new ingest sources behind the same two
  gates — build sources, not new pipelines.
