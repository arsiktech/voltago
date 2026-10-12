# Plan 002: Make the repo's self-descriptions true and the tree Syncthing-safe

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [AGENTS.md](../AGENTS.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: docs
- **Planned at**: commit `ceee003`, 2026-07-12
- **Completed**: 2026-07-13. The original sync-conflict STOP was resolved by
  the explicit D-175 source-policy decision: all 12 divergent records are
  direct Fénie-origin evidence and therefore must not re-enter the canonical
  DB. Two locations remain independently represented by permitted OSM
  evidence; the other ten correctly fail closed until a permitted source
  corroborates them. Neither conflict copy was merged; both were removed.
- **Local Git maintenance complete**: the owner explicitly accepted the early-prune
  recovery tradeoff and `git gc --prune=now` removed the protected temporary pack.
  Final verification reports one packed store (`size-pack: 76.04 MiB`), zero garbage,
  and a clean `git fsck --no-dangling`. Later ordinary commits naturally create a small
  number of reachable loose objects; that is normal Git activity, not garbage or a
  reopened maintenance gate. No object directory was deleted directly and no history
  was reset.

## Why this matters

This repo is maintained by agents, and AGENTS.md is the file every session
reads first. Right now its §3 code map has **six wrong line numbers** (off by
up to ~1,200 lines) pointing at the highest-risk honesty functions, §8
overstates the browser-QA setup requirements (scaring agents into skipping
the suite), the SPA DOM contract documents selectors that no longer exist,
and a foreign machine's absolute node path is committed into the QA runner.
Separately, two Syncthing `*.sync-conflict-*` files (one a 5.3 MB copy of the
intelligence DB) sit untracked next to canonical files with no gitignore
protection — the exact hazard AGENTS.md §8 itself warns about. Wrong
self-description is worse than none: it sends agents to the wrong code and
the wrong setup steps.

## Current state

Verified on disk 2026-07-12:

1. **AGENTS.md §3 stale line refs** (actual locations from `grep -n "^def "
   server.py`):
   - `AGENTS.md:124` says `live_response() (server.py:1951)` → actually `server.py:3149`
   - `AGENTS.md:137` says `reject_station_reason() (server.py:256)` → actually `:328`
   - `AGENTS.md:138` says `ensure_claim_contract() (server.py:499)` → actually `:593`
   - `AGENTS.md:140` says `verification_profile() (server.py:547)` → actually `:641`
   - `AGENTS.md:141` says `decision_for() (server.py:601)` → actually `:701`
   - `AGENTS.md:143` says `recommendation_score() (server.py:740)` → actually `:840`
   - §3 also points at legacy `static/app.js` for decision wording /
     `displayName` logic that now primarily lives in `frontend/src/lib/format.ts`.
2. **AGENTS.md §8 (lines ~296–302)** claims you "must provide" `VOLTA_QA_NODE`,
   `NODE_PATH`, and `VOLTA_QA_CHROME` for browser checks. Reality:
   `scripts/qa_regression_suite.py` `resolve_node()` falls back to
   `shutil.which("node")`; `playwright` is a root devDependency (root
   `package.json`, `node_modules/playwright` present); every `qa_*.js`
   defaults Chrome to the standard macOS path. With Chrome installed and node
   on PATH, zero env vars are needed.
3. **`scripts/qa_regression_suite.py:24-25`** hardcodes
   `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`
   (+ sibling `node_modules`) as `BUNDLED_NODE`/`BUNDLED_NODE_MODULES`,
   preferred over PATH node in `resolve_node()` and prepended to `NODE_PATH`.
4. **`docs/spa-dom-contract.md:22-25`** promises
   `aria-label="Search a city to jump there, or filter the list"` and a
   `section ul > li > button` dropdown; the real component
   (`frontend/src/components/SearchBar.tsx:70-71,101-122`) uses
   `aria-label="Enter home address or city"`, `role="combobox"`, and a
   `role="listbox"`/`role="option"` dropdown labeled "Home area suggestions".
   The contract file itself says "If a selector here drifts from the code,
   the code wins — update this file in the same change."
5. **`frontend/README.md:3`** still says "for Valencia" — the app went
   national on 2026-06-25 (AGENTS.md §1).
6. **`docs/wiki/api.md:10-18`** route table omits `GET /api/source-health`
   (real handler at `server.py:3954`); `docs/wiki/.codewiki-state.json` says
   the wiki was generated 2026-07-06 at commit `5706893` — 51 commits behind.
7. **`.gitignore`** (30 lines) has no `*.sync-conflict-*`, no `/tmp/`, no
   `/outputs/` patterns; on disk sit
   `data/free_intelligence.sync-conflict-20260709-130735-P7NJXZR.json` (5.3 MB,
   dated 07-09; canonical file is newer, 07-12),
   `qa/free-db-completeness-latest.sync-conflict-20260709-130735-P7NJXZR.json`,
   `tmp/pdfs/...` and `outputs/branding/...` scratch. `.gitignore:22` ignores
   the whole `.claude/` dir, which also hides `.claude/launch.json` (valid
   dev-server config: `volta-web` on 5183, `volta-prod` on 8791).
8. **`.git` object store**: `git count-objects -vH` → `count: 5639`,
   `size: 176.96 MiB`, `packs: 0`, plus `garbage: .git/objects/pack/tmp_pack_sW4o1K`
   — entirely loose objects from re-committing the 7 MB DB ~53 times.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Offline verify | `./scripts/verify.sh` (if plan 001 landed) else AGENTS.md §5 steps 1–2 | exit 0 |
| QA suite wiring test | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_regression_suite.py` | `OK` |
| Git store size | `git count-objects -vH` | see step 8 |

## Scope

**In scope**:
- `AGENTS.md` (§3 map refs, §8 node/env wording only)
- `docs/spa-dom-contract.md` (Search section only)
- `docs/wiki/api.md` (route table) + `docs/wiki/README.md` (staleness banner)
- `frontend/README.md` (line 3 scope wording)
- `scripts/qa_regression_suite.py` (BUNDLED_* paths / resolve_node / NODE_PATH wiring)
- `.gitignore`
- Deleting the two on-disk `*.sync-conflict-*` files (after the diff gate in step 6)
- `qa/run-log.md` (append one entry)

**Out of scope** (do NOT touch):
- `static/app.js` and all honesty logic — that's plans/008.
- `data/free_intelligence.json` (canonical DB) — never edit by hand.
- The `docs/wiki/modules/` bodies — full regeneration needs the code-wiki
  tool; this plan only patches api.md and adds a banner.
- `qa/*-latest.json` — never hand-edit evidence artifacts.

## Git workflow

- Work on `main`; 2–3 logical commits are fine (e.g. docs truth pass /
  QA-runner portability / gitignore+cleanup). Imperative messages matching
  history. Push per AGENTS.md §8 unless the operator said otherwise.

## Steps

### Step 1: Fix AGENTS.md §3 references so they cannot rot again

Replace the six `server.py:NNNN` refs with the current numbers (re-grep each:
`grep -n "def live_response" server.py` etc. — do not trust this plan's
numbers blindly) AND change the format to name-first, e.g.
`live_response() — grep "def live_response" (server.py, ~line 3149)`.
Repoint the two §3 mentions of `app.js` presentation logic to
`frontend/src/lib/format.ts` (keep a parenthetical that `static/app.js` is the
legacy fallback copy).

**Verify**: for each of the six functions:
`n=$(grep -n "def decision_for" server.py | cut -d: -f1)` and confirm the
number now written in AGENTS.md matches. `grep -c "server.py:1951\|server.py:256)\|server.py:499\|server.py:547\|server.py:601\|server.py:740" AGENTS.md` → 0.

### Step 2: Correct AGENTS.md §8 QA-env claims

Rewrite the node/Chrome bullet to state: the browser tier resolves node from
`VOLTA_QA_NODE` → PATH, uses the repo root `node_modules` (playwright is a
root devDependency), and defaults Chrome to the standard macOS path —
`VOLTA_QA_CHROME` is only needed for a non-standard Chrome location. Keep the
"never claim full-suite green when browser checks were skipped" rule intact.

**Verify**: `grep -n "must provide" AGENTS.md` → no hit in the QA bullet (or
reworded so the three vars are described as optional overrides).

### Step 3: Remove the machine-specific node path from the QA runner

In `scripts/qa_regression_suite.py`:
- Delete the `BUNDLED_NODE` / `BUNDLED_NODE_MODULES` constants (lines 24–25
  region) and their branch in `resolve_node()` (lines ~120–128), so
  resolution is `--node` arg → `VOLTA_QA_NODE` env → `shutil.which("node")`.
- Where `NODE_PATH` was seeded from `BUNDLED_NODE_MODULES` (lines ~477–478),
  seed it from the repo root `node_modules` instead:
  `ROOT / "node_modules"` if it exists (that is where `playwright` lives),
  preserving any caller-provided `NODE_PATH` by prepending.

**Verify**: `grep -n "codex-runtimes" scripts/qa_regression_suite.py`
→ no matches. `python3 tests/test_regression_suite.py` → `OK` (it asserts
suite wiring; if it asserted the old constants, update the test to the new
resolution order — read it first). `python3 -m py_compile scripts/qa_regression_suite.py` → exit 0.

### Step 4: Update docs/spa-dom-contract.md Search section

Re-read `frontend/src/components/SearchBar.tsx` (lines ~60–125) and rewrite
the contract's Search block to the real accessible structure: combobox input
with `aria-label="Enter home address or city"`, listbox dropdown
"Home area suggestions", option rows. Copy the exact strings from the
component, not from this plan.

**Verify**: every `aria-label`/role string you wrote exists in SearchBar.tsx:
`grep -n "Enter home address or city" frontend/src/components/SearchBar.tsx docs/spa-dom-contract.md` → one hit in each.

### Step 5: Patch the generated wiki honestly + fix frontend/README scope

- `docs/wiki/api.md`: add `GET /api/source-health` to the route table
  (one row; describe it as the engine-version + per-source health summary,
  see `server.py` handler `do_GET` → `/api/source-health`).
- `docs/wiki/README.md`: add a 2-line banner at top: generated 2026-07-06 at
  commit 5706893; regenerate with the code-wiki tool before trusting details;
  api.md patched by hand on <today's date>.
- `frontend/README.md:3`: "for Valencia" → all-Spain wording (match AGENTS.md
  §1: national, viewport/bbox-driven).

**Verify**: `grep -n "source-health" docs/wiki/api.md` → hit;
`grep -in "valencia" frontend/README.md` → no scope claim left (mentions of
Valencia as an example city are fine if clearly examples).

### Step 6: Sync-conflict cleanup with a diff gate

The conflict copy of the DB is OLDER (07-09) than canonical (07-12), but do
not assume — check:

```bash
python3 - <<'EOF'
import json
a=json.load(open('data/free_intelligence.json'))
b=json.load(open('data/free_intelligence.sync-conflict-20260709-130735-P7NJXZR.json'))
ids=lambda d:{r.get('id') for r in d.get('records',[])}
print('canonical records:',len(ids(a)),'conflict records:',len(ids(b)))
print('in conflict but NOT canonical:',len(ids(b)-ids(a)))
EOF
```

If `in conflict but NOT canonical` is 0 (or the only missing ids are
explainable dedup/rename churn — when in doubt STOP), delete both
`*.sync-conflict-*` files. The `qa/free-db-completeness-latest.sync-conflict-*`
file is derived evidence — deletable without a gate once the DB question is
settled.

**Resolution (2026-07-13):** the gate's 12 divergent IDs were compared against
commit `0bb999a`, the conflict snapshot, the current canonical DB, and
`data/source_policy_exclusions.json`. All 12 match exclusion
`fenie-rights-and-request-method` through their direct source label,
`recarga.fenieenergia.es` URL, and Fénie-backed charge/parking claims. None is
present by exact ID in the current DB. The two Marjals coordinates have
independent permitted OpenStreetMap records within 2 m; the other ten have no
permitted record within 75 m and remain absent by design. This is an explicit
policy resolution, not dedup/rename inference: the two conflict artifacts were
removed without promoting any of their rows.

**Verify**: `ls data/*.sync-conflict-* qa/*.sync-conflict-* 2>&1` → no such file.

### Step 7: .gitignore hardening (+ keep launch.json shareable)

Append a "Syncthing / scratch" block: `*.sync-conflict-*`, `/tmp/`,
`/outputs/`. Change `.claude/` to:

```
.claude/*
!.claude/launch.json
```

(A dir-level ignore cannot be re-included below it; the `/*` form can.)
Then `git add .claude/launch.json` so the dev-server config travels.

**Verify**: `git check-ignore -v tmp outputs data/foo.sync-conflict-x` → all
three matched; `git check-ignore .claude/launch.json` → exit 1 (NOT ignored);
`git status --short | grep launch.json` → shows as added.

### Step 8: Pack the object store

`git gc` (safe, non-destructive; also clears the stale
`.git/objects/pack/tmp_pack_sW4o1K` garbage). This is a local-machine
optimization — note it in the run-log so other machines know to do the same.

**Verify**: `git count-objects -vH` → `size-pack` > 0 and loose `size` drops
dramatically (expect the ~177 MiB of near-identical JSON snapshots to
delta-compress to a small fraction); `git fsck --no-dangling` → no errors;
`./scripts/verify.sh` (or §5 steps 1–2) still green.

### Step 9: QA ledger

Append a `qa/run-log.md` entry (match the latest entry's format): what was
corrected, the before/after `git count-objects` numbers, files deleted, and
explicit note that no product logic changed. This is a docs/hygiene pass, not
a bug fix — no `qa/defects.csv` row needed (that ledger is for product
defects; read its last rows to confirm the convention).

**Verify**: `tail -25 qa/run-log.md` shows the entry.

## Test plan

No new automated tests (docs/config pass). The regression gate is:
`./scripts/verify.sh` (or AGENTS §5 steps 1–2) green after every step, plus
`python3 tests/test_regression_suite.py` after step 3.

## Done criteria

- [x] All six AGENTS.md §3 function refs match a fresh grep of server.py
- [x] `grep -rn "codex-runtimes" scripts/` → no matches
- [x] spa-dom-contract Search section strings each exist in SearchBar.tsx
- [x] `/api/source-health` documented in docs/wiki/api.md; staleness banner present
- [x] No `*.sync-conflict-*` files on disk; pattern in .gitignore
- [x] `.claude/launch.json` tracked; `tmp/`, `outputs/` ignored
- [x] `git count-objects -vH` shows packed store with zero garbage; `git fsck` clean
- [x] Offline verify green; `plans/README.md` row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The DB conflict file contains record ids missing from canonical (step 6
  gate) — that means the 07-09 divergence held real data; the owner must
  arbitrate a merge, not a delete.
- `tests/test_regression_suite.py` asserts the BUNDLED_NODE constants in a
  way that suggests the Codex runtime path is load-bearing on some machine —
  report before deleting.
- AGENTS.md on disk differs materially from the excerpts (the file was
  uncommitted-modified at planning time).

The first STOP was triggered on 2026-07-12 and was not bypassed. It was
subsequently resolved on 2026-07-13 by D-175's explicit, tested exclusion
policy and the record-by-record provenance comparison documented in step 6.

## Maintenance notes

- The name-first reference format in §3 (grep-based) is the rot-proof
  convention going forward — reviewers should reject new raw line-number refs
  in AGENTS.md.
- The wiki remains 51 commits stale in its module bodies; a full regeneration
  with the code-wiki tool is a separate, optional follow-up.
- `git gc` should be re-run occasionally (or `git config gc.auto` respected)
  while the weekly 7 MB DB commit cadence continues; the deeper fix (commit
  cadence / snapshot policy) was considered and deferred — see plans/README.md.
