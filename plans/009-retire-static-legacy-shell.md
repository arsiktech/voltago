# Plan 009: Retire the static/ legacy shell — the SPA becomes the only UI

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [current contracts](../docs/backend-engine.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P2 (before plan 008 if executed — it removes 008's legacy-shell steps)
- **Effort**: M
- **Risk**: MED (removes the no-build fallback: serving without `frontend/dist` must fail LOUDLY, not blankly)
- **Depends on**: plans/001 (verify script), plans/002 (hygiene) recommended first
- **Category**: tech-debt
- **Planned at**: commit `ceee003`, 2026-07-12 — owner decision 2026-07-12: "не нужна наверное" (leaning delete; this plan is the delete path)

## Why this matters

`static/` is 2,210 lines of superseded vanilla UI (app.js 1,236 + styles.css
787 + index.html 150) untouched since repo init, yet it is neither dead nor
maintained: `web_root()` silently serves it whenever `frontend/dist` is
missing, the Dockerfile ships it, three QA checks still bind it, and it
carries a fourth, already-drifted copy of the honesty rules (audit
2026-07-12). Every honesty change must currently be ported to it or it rots
further. The owner's direction is a fully autonomous, publicly deployed app —
one UI, one honesty implementation per surface, and a server that fails
loudly instead of silently serving a stale shell.

## Current state

Verified on disk 2026-07-12:

- `server.py:48` — `web_root()` returns `frontend/dist` when
  `frontend/dist/index.html` exists, else `STATIC` (`ROOT / "static"`,
  defined `server.py:39`). Read the function fully before editing.
- `Dockerfile:15` — `COPY static/ ./static/` into the runtime image
  (the image ALSO builds and copies the SPA at `Dockerfile:16`, so in-image
  the fallback is never used).
- QA bindings to static/:
  - `scripts/qa_regression_suite.py:29` — `node --check static/app.js` check
  - `scripts/qa_accessibility_static.py:112` — reads `static/app.js`
  - `scripts/qa_pwa_shell.py` — verify what it reads (static/ manifest vs
    frontend/public/manifest.webmanifest) before touching
- `tests/test_regression_suite.py:68` asserts the suite has exactly 30
  checks and required IDs (`:38-56`) — removing checks changes these
  assertions AND the "30-check suite" language in `AGENTS.md` (§2 ¶5, §5)
  and `qa/coverage-summary.md`.
- `static/app.js` honesty drift (documented in plans/008): if THIS plan runs
  first, plans/008's app.js steps become void — note it in both READMEs.
- AGENTS.md references to static/: §3 map ("LEGACY vanilla shell. Fallback
  only"), §8 encoding note mentioning app.js `displayName`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Offline verify | `./scripts/verify.sh` | exit 0 |
| Suite wiring test | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_regression_suite.py` | OK (after your updates) |
| SPA build | `npm --prefix frontend run build` | exit 0, `frontend/dist/index.html` exists |
| No-dist failure mode | `mv frontend/dist /tmp/dist-bak && PORT=8791 python3 server.py` (then restore) | clear startup error, NOT a silent legacy serve |

## Scope

**In scope**:
- Delete `static/` (git rm)
- `server.py` — `web_root()` and the `STATIC` constant; a loud no-dist error
- `Dockerfile` — drop the `COPY static/` line
- `scripts/qa_regression_suite.py`, `scripts/qa_accessibility_static.py`,
  `scripts/qa_pwa_shell.py` — remove/retarget static-bound checks (retarget
  to `frontend/` sources where the check is still meaningful, e.g. PWA shell
  → `frontend/public/manifest.webmanifest` + built `dist/index.html`)
- `tests/test_regression_suite.py` — new check count/IDs
- `AGENTS.md` (§3 map, §8 notes), `qa/coverage-summary.md`
- Ledger files (`qa/defects.csv` is NOT needed — this is not a bug fix;
  `qa/test-cases.csv` rows referencing static-only behavior get updated
  status per that CSV's own conventions — read them first)

**Out of scope**:
- `frontend/` code changes of any kind.
- `docs/spa-dom-contract.md` (already the SPA contract).
- plans/008's server/iOS/parity work.

## Git workflow

- `main`; commits: retarget QA / remove fallback / delete static. Push per
  AGENTS.md §8 unless told otherwise. The deletion commit message should name
  this plan for archaeology (`Retire static legacy shell (plans/009)`).

## Steps

### Step 1: Inventory every reference first

`grep -rn "static/" --include="*.py" --include="*.md" --include="*.toml" --include="Dockerfile" . | grep -v node_modules | grep -v frontend/dist | grep -v plans/`
and `grep -rn "app\.js" scripts/ tests/ AGENTS.md docs/`. Build the full
touch-list; if it contains a consumer this plan doesn't mention (e.g. a
qa_*.js browser script loading the legacy shell), STOP and report the list.

**Verify**: the list matches the Scope section (± docs mentions).

### Step 2: Make the server fail loudly without a build

In `server.py`: `web_root()` returns `frontend/dist` if present; otherwise
`main()` must exit at startup with a clear message
(`frontend/dist missing — run: npm --prefix frontend run build`) rather than
serving anything. Keep `web_root()` itself pure (tests may import it);
enforce the exit in `main()` so unit tests never die on import. Remove the
`STATIC` constant and the fallback branch.

**Verify**: no-dist failure-mode command (table above) prints the message and
exits non-zero; with dist restored the server serves the SPA
(`curl -s localhost:8791/ | grep -c "<div id=\"root\""` ≥ 1 or equivalent).

### Step 3: Retarget or remove the three QA bindings

- Drop the `node --check static/app.js` entry from `qa_regression_suite.py`.
- `qa_accessibility_static.py`: read it — if its assertions are legacy-only,
  remove the script and its suite entry; if some assertions translate to the
  SPA source, retarget them; do not keep a check that tests deleted code.
- `qa_pwa_shell.py`: retarget to `frontend/public/manifest.webmanifest` (and
  built dist when present).
- Update `tests/test_regression_suite.py` expected count/IDs to the new
  reality; update the "30-check suite" phrasing in AGENTS.md and
  `qa/coverage-summary.md` to the new number (grep `30` in both — only the
  suite-count mentions).

**Verify**: `python3 tests/test_regression_suite.py` → OK;
`grep -rn "static/app.js" scripts/ tests/` → no matches.

### Step 4: Delete the shell + Dockerfile line

`git rm -r static/`; remove `COPY static/ ./static/` from Dockerfile; update
AGENTS.md §3 map (delete the static/ row; note the SPA is the only UI and a
build is required) and §8's app.js encoding note (repoint to format.ts's
`displayName`).

**Verify**: `ls static 2>&1` → no such directory;
`grep -n "static" Dockerfile` → no COPY of the shell;
`./scripts/verify.sh --full` → green (build + tests).

### Step 5: Ledger

Run-log iteration (what was removed, new suite count, the no-dist failure
mode demonstrated with real output); `qa/test-cases.csv` rows referencing the
legacy shell updated per that file's conventions; coverage-summary counts.

**Verify**: `python3 scripts/qa_artifact_integrity.py` → ok.

## Test plan

- `tests/test_regression_suite.py` (updated) — suite wiring.
- `./scripts/verify.sh --full` — the SPA build becomes a hard gate, which is
  exactly the new contract.
- Manual failure-mode check (step 2) recorded in the run-log.
- If the local Chrome stack is available, run one browser contract against
  the served SPA (e.g. `qa_mobile_ui.js` per its header docs) to prove the
  serving path; otherwise record the honest skip.

## Done criteria

- [ ] `static/` gone from the tree and from Dockerfile
- [ ] Server without `frontend/dist` exits with the actionable message (demonstrated)
- [ ] Suite wiring test green with the new check count; no script references static/
- [ ] AGENTS.md §3/§8 and coverage-summary updated (no "fallback shell" language left)
- [ ] `./scripts/verify.sh --full` green
- [ ] Run-log + artifact integrity ok; only in-scope files changed
- [ ] plans/README.md: this row DONE; note added that plans/008's app.js steps are void

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1's inventory finds an unlisted consumer of static/ (a deploy script,
  a browser QA contract, a doc that promises the no-build path to some
  environment).
- Any deployment note (DEPLOY.md) turns out to RELY on serving without a
  node toolchain — that was the fallback's one real job; the owner must
  confirm every serving environment can build (the Docker image already
  does).
- `qa_accessibility_static.py` contains accessibility assertions with no SPA
  equivalent — losing a11y coverage silently violates Prime Directive §2.6;
  report what would be lost.

## Maintenance notes

- After this lands, plans/008 shrinks: its `static/app.js` regex + decision
  fallback steps are void (the parity test then pins three surfaces, not
  four).
- The "works from a bare checkout with only Python" property is gone by
  design; AGENTS.md documents the build requirement. If a zero-node
  environment ever matters again, the answer is committing a built dist
  snapshot, not resurrecting the legacy shell.
