# Plan 001: One command proves the repo is green offline

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [current contracts](../docs/backend-engine.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `ceee003`, 2026-07-12

## Why this matters

Volta's verification pipeline exists only as four copy-paste blocks in
AGENTS.md §5. Every agent session re-derives it by hand, and nothing enforces
that all steps ran before "done" is claimed. The two offline suites currently
pass (219 backend unittest, 59 frontend vitest — verified 2026-07-12), but a
fresh agent has no single command that proves it, and the 15 browser DOM
contracts run only with a local Chrome stack, so their status must be
*reported*, never silently implied. This plan creates that single command.
Every other plan in `plans/` uses it as a verification gate.

## Current state

- `scripts/` contains 36 files; the only shell script is
  `scripts/refresh_intelligence.sh` (use it as the style exemplar: bash,
  `set -euo pipefail`, comments explaining each phase). There is no
  `verify.sh`, no Makefile, no root `package.json` scripts field, no CI.
- AGENTS.md §5 ("Verify before you claim done") documents the tiers, on-disk
  today at lines 200–223:

```
# 1. Compile / typecheck gates (fast, no running server)
PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -m py_compile server.py scripts/*.py
npm --prefix frontend run build          # tsc -b typecheck + Vite production build

# 2. Unit tests — backend (stdlib unittest) + frontend (Vitest on honesty-critical logic)
for t in tests/test_*.py; do echo "== $t =="; python3 "$t" || break; done
npm --prefix frontend run test          # Vitest: format.ts (availability/decision/filters/pricing)
```

  (Note the `|| break` loop swallows the failure exit code — a failing test
  file stops the loop but leaves `$?` of the `for` construct at 0. The verify
  script must NOT reproduce that bug; `python3 -m unittest discover -s tests`
  is the reliable form and is what qa/run-log.md iteration 165 actually used.)
- The browser tier (15 Playwright contracts) is orchestrated by
  `scripts/qa_regression_suite.py` and needs a RUNNING server; it is out of
  scope for this script except as an explicit status line.
- Repo conventions: Python is stdlib-only (Prime Directive — do not add pip
  anything); Spanish text is UTF-8; changes are logged in `qa/run-log.md`
  (append-only narrative, read the last entry and match its format).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Backend compile | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -m py_compile server.py scripts/*.py` | exit 0, no output |
| Backend tests | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -m unittest discover -s tests` | `Ran 219 tests` … `OK` |
| Frontend tests | `npm --prefix frontend run test` | `Test Files  2 passed`, `Tests  59 passed` |
| Frontend build | `npm --prefix frontend run build` | exit 0 (tsc -b + vite build) |
| Frontend lint | `npm --prefix frontend run lint` | exit 0 |

(Test counts are the 2026-07-12 baseline; more is fine, fewer is a failure.)

## Scope

**In scope** (the only files you should modify/create):
- `scripts/verify.sh` (create)
- `AGENTS.md` (§5 only — add the one-command entry point at the top of the section)
- `qa/run-log.md` (append one entry)

**Out of scope** (do NOT touch):
- `scripts/qa_regression_suite.py` and all `qa_*.py`/`qa_*.js` — the browser
  tier is a different plan's territory (see plans/002 for its node-path fix).
- `package.json` / `frontend/package.json` — no new scripts fields needed.
- Any product code.

## Git workflow

- Work on `main` (the repo's convention — no feature branches in history).
- One commit, message style matches history (short imperative, e.g.
  `Add one-command offline verify script`).
- AGENTS.md §8 says commit and push; push only if the operator has not said otherwise.

## Steps

### Step 1: Create `scripts/verify.sh`

Bash, `set -euo pipefail`, executable. Behavior:

- Default run (no args): backend compile → backend unittest → frontend vitest.
  These are the fast, fully-offline gates (~10s total).
- `--full`: additionally run `npm --prefix frontend run lint` and
  `npm --prefix frontend run build` (build writes only the gitignored
  `frontend/dist/`).
- Always set `PYTHONPYCACHEPREFIX=/tmp/volta-pycache` for the Python steps.
- On success print a summary block that includes an HONEST browser-tier line, e.g.:

```
== VERIFY OK (offline tiers) ==
backend:  py_compile OK, unittest 219/219 OK
frontend: vitest 59/59 OK[, lint OK, build OK]
NOT RUN:  15 browser DOM contracts + external canaries
          (needs a running server: scripts/qa_regression_suite.py --base-url ...)
```

- Any failing step must fail the script with that step's non-zero exit code
  (no `|| true`, no swallowed failures — this is the point of the script).
- Parse the unittest/vitest totals from their output for the summary if easy;
  otherwise print the raw last lines. Do not over-engineer.

**Verify**: `bash -n scripts/verify.sh` → exit 0; `chmod +x scripts/verify.sh`.

### Step 2: Run it, both modes

**Verify**: `./scripts/verify.sh` → exits 0, summary shows `unittest 219/219`
(or current higher count) and the `NOT RUN` browser-tier line.
Then `./scripts/verify.sh --full` → exits 0 (lint + build included).

### Step 3: Prove it fails loudly

Temporarily append `raise SystemExit("verify-canary")` as the last line of
`tests/test_corroboration.py`, run `./scripts/verify.sh`, confirm non-zero
exit, then **revert that edit** (`git checkout -- tests/test_corroboration.py`
if the file was otherwise clean; confirm with `git diff tests/`).

**Verify**: `./scripts/verify.sh; echo "exit=$?"` during the canary → `exit=1`
(or other non-zero); after revert → `exit=0` and `git status` shows no
unintended test changes.

### Step 4: Document it in AGENTS.md §5

At the top of §5, before the existing command blocks, add 2–3 lines: the
default is now `./scripts/verify.sh` (offline tiers), `--full` adds
lint+build, and the browser tier still follows the existing §5 instructions.
Do not delete the existing per-command documentation — agents still need the
individual commands for targeted runs.

**Verify**: `grep -n "verify.sh" AGENTS.md` → at least one hit in §5.

### Step 5: Append to qa/run-log.md

Read the last iteration entry first and match its heading/format. Record:
what was added, the exact verification output (real numbers), and that no
product code changed.

**Verify**: `tail -20 qa/run-log.md` shows the new entry.

## Test plan

The script IS test infrastructure; its own tests are Steps 2–3 (green run,
`--full` run, loud-failure canary). No new unittest/vitest files.

## Done criteria

- [ ] `./scripts/verify.sh` exits 0 and prints the summary incl. the `NOT RUN` browser line
- [ ] `./scripts/verify.sh --full` exits 0
- [ ] Canary experiment demonstrated non-zero exit (noted in run-log entry)
- [ ] `grep -n "verify.sh" AGENTS.md` → hit in §5
- [ ] `git status` — only `scripts/verify.sh`, `AGENTS.md`, `qa/run-log.md` changed
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The baseline suites do NOT pass before your change (`unittest discover`
  currently fails or vitest currently fails) — the repo has drifted; this
  plan assumes a green baseline.
- `npm --prefix frontend run test` cannot run (node_modules missing) after a
  plain `npm --prefix frontend install` — report the environment gap instead
  of installing anything else.
- You find an existing verify/aggregate script this plan missed.

## Maintenance notes

- Plans 003–008 use `./scripts/verify.sh` as their base gate; if you rename
  or change its exit behavior, update those plans' Commands tables.
- If a CI runner is ever added (currently none — deliberate for a private
  single-owner repo), it should call this script rather than re-encoding the
  pipeline.
- The browser tier deliberately stays manual; if it ever becomes
  env-var-free (see plans/002 step on node paths), consider a `--browser`
  flag here rather than a second script.
