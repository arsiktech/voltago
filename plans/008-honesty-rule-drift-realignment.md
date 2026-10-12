# Plan 008: Re-align the drifted honesty rules across the four clients and unify connector labels

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [AGENTS.md](../AGENTS.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (touches decision-adjacent code on three surfaces + data labels; every change is pinned by a parity test or a DB-delta gate below)
- **Depends on**: plans/006 (connector/merge characterization); independent of 003–005/007 but coordinate server.py edit timing
- **Category**: bug / tech-debt
- **Planned at**: commit `ceee003`, 2026-07-12

## Why this matters

Data honesty is Volta's entire reason to exist, and its rules are hand-copied
across four surfaces (server Python, web TypeScript, iOS Swift, legacy JS).
The 2026-07-12 audit diffed the copies and found **three live drifts** — the
same station can get different honesty treatment per client:

1. An Electromaps station page in French/Portuguese counts as an
   exact-location directory pin on server/web/iOS but NOT in the legacy
   shell (`(?:en|es)` vs `(?:en|es|fr|pt)`).
2. A `provider_station_page` primary source pins on the web map but not on
   iOS (missing from the Swift kind list).
3. A station with a missing/unknown `decision.level` renders "Check first"
   on web and iOS (lead-aware fallback) but "Verify first" always in the
   legacy shell.

Separately, **five parallel connector-label normalizers** (three in
server.py, two in scripts/ingest_sources.py) have drifted: the Electromaps
variant maps UNKNOWN tokens to `"Type 2"` — silently marking unknown plugs as
Tesla-compatible (`TESLA_OK_STANDARDS` gates `decision_for`'s
"No CCS2/Type 2/Tesla-compatible connector" rejection), while the Feníe
variant title-cases unknowns and has no Tesla/Type 1 handling. A plug's
label decides whether a driver is told "Go now" — five divergent copies is
how that flips per source.

A full "server emits, clients consume" consolidation was considered and
deferred (L effort, all-client refactor — see plans/README.md). This plan
delivers the correctness value cheaply: fix the drifts, unify the
normalizer, and add a parity test that FAILS on future drift.

## Current state

All verified on disk 2026-07-12.

- Exact-directory URL rule:
  - `server.py:3557`: `re.search(r"electromaps\.com/(?:en|es|fr|pt)/p/", url)`
  - `frontend/src/lib/format.ts:171`: `/electromaps\.com\/(?:en|es|fr|pt)\/p\//`
  - `ios/Volta/Volta/Models.swift:287`: `electromaps\.com/(en|es|fr|pt)/p/`
  - `static/app.js:424`: `/electromaps\.com\/(?:en|es)\/p\//` (drifted) and
    `static/app.js:488`: `/electromaps\.com\/(?:en|es)\/p\/|…/i` (drifted)
- Exact-directory `kind` list:
  - `server.py:3550` and `format.ts:165`:
    `{station_page, start_page, provider_station_page, map_object, live_map, official_data}`
  - `Models.swift:278`: same list MINUS `provider_station_page` (drifted)
- Decision fallback:
  - `format.ts:142-147`: unknown level → `'check'` for non-leads, `'verify'` for leads
  - `Models.swift:247-250`: same (`isLead ? .verify : .check`)
  - `static/app.js` (~line 620–680 region; grep `decisionLevel = (st)`):
    `const decisionLevel = (st) => st.decision?.level || 'verify';` (drifted —
    lead-blind)
  - NOTE (vet finding): iOS `isMapLocationVerified` official-branch
    (`Models.swift:299-301`) is NOT drifted — it matches web
    (`format.ts:190`) including the location+price checks. Do not "fix" it.
- Connector normalizers (five):
  - `server.py:1180-1188` `connector_label(std)` — dict lookup, unknown →
    `std.replace("_", " ")` passthrough
  - `server.py:1827-1837` `fenie_connector_label(raw)` — no Tesla/Type 1
    cases; unknown → `str(raw).title()`
  - `server.py:2078-2092` `electromaps_connector_label(token)` — handles
    Tesla/T1/SCEE/DOMESTIC; **unknown → `"Type 2"`** (the dangerous default)
  - `scripts/ingest_sources.py` `connector_label_from_token` (~:366) and
    `openchargemap_connector_label` (~:932) — read both before editing
- Tesla gate: `server.py` `TESLA_OK_STANDARDS` (grep it) is consumed in
  `transform_location` (`:2399` `tesla_ok`) and `decision_for` (~`:730`
  rejection copy).
- Parity-test precedent: `tests/test_ios_design_contract.py` already reads
  Swift SOURCE TEXT from Python and asserts substrings/regexes — the same
  technique pins TS/JS/Swift constants here. Model on it.
- Conventions: bug fixes → `D-###` rows; new tests → `TC-###`; run-log +
  coverage-summary; browser-tier honesty rule (say what you didn't run).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Offline verify | `./scripts/verify.sh` (or AGENTS §5 1–2) | exit 0 |
| Legacy shell syntax | `node --check static/app.js` | exit 0 |
| New parity test | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_honesty_parity.py -v` | all pass |
| iOS compile gate (if Xcode present) | `xcodebuild -quiet -project ios/Volta/Volta.xcodeproj -scheme Volta -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/volta-ios-derived CODE_SIGNING_ALLOWED=NO build` | success (this exact command appears in qa/run-log.md iteration 165) |
| DB label delta (step 4 gate) | `python3 scripts/perf_probe.py` n/a — see step 4's inline script | delta report |

## Scope

**In scope**:
- `static/app.js` — the two regex sites + the decisionLevel fallback (3 edits)
- `ios/Volta/Volta/Models.swift` — add `provider_station_page` (1 edit)
- `server.py` — one unified `normalize_connector_label` + rewiring the three
  server variants
- `scripts/ingest_sources.py` — align its two variants to the same table
- `tests/test_honesty_parity.py` (create)
- Ledger files

**Out of scope** (do NOT touch):
- `frontend/src/lib/format.ts` — it is currently the CORRECT reference copy;
  no edits (the parity test reads it).
- Any move toward server-emitted tier/eligibility fields (deferred
  consolidation — README).
- `data/free_intelligence.json` — labels change only via the next scheduled
  ingest run, not by hand-editing the DB (see step 4 note).
- Web map/list rendering logic.

## Git workflow

- `main`; commits: legacy-shell realignment / iOS kind fix / connector
  unification / parity test. Push per AGENTS.md §8 unless told otherwise.

## Steps

### Step 1: Parity test FIRST (it should fail on the current drifts)

Create `tests/test_honesty_parity.py`, modeled on
`tests/test_ios_design_contract.py` (read files as text, assert content):

1. Extract the Electromaps exact-page regex from all four files (read each
   file, regex-extract the pattern literal) → assert the language set is
   identical (`en|es|fr|pt`) in server.py, format.ts, Models.swift, app.js
   (both app.js sites).
2. Extract the exact-directory kind list from server.py:3550-region,
   format.ts:165-region, Models.swift:278-region → assert set equality
   (all six kinds incl. `provider_station_page`).
3. Assert app.js's decisionLevel fallback is lead-aware: the file must NOT
   contain the exact string `st.decision?.level || 'verify'` and MUST express
   the lead/non-lead split (assert on the replacement you'll write in step 2
   — e.g. presence of both `'check'` and `'verify'` in that function).
4. Connector-parity matrix: for a token list
   `["IEC_62196_T2_COMBO", "CCS", "CHADEMO", "TESLA", "MENNEKES", "type 2", "tipo 2", "SCHUKO", "DOMESTIC", "SCEE", "T1", "UNKNOWN_XYZ", "", None]`
   assert `server.normalize_connector_label(t)` equals
   `ingest_sources.connector_label_from_token(t)` for every t (import both
   modules; the ingest import idiom is in tests/test_ingest_merge.py).

**Verify**: run it — cases 1–3 FAIL against today's code (that's the drift),
case 4 errors (no unified function yet). Record output.

### Step 2: Re-align the legacy shell (static/app.js)

- `static/app.js:424` and `:488`: `(?:en|es)` → `(?:en|es|fr|pt)` (keep each
  regex's other alternatives byte-identical).
- The decisionLevel fallback (grep `decisionLevel = (st)`): replace with the
  lead-aware equivalent of format.ts:142-147, e.g.
  `const decisionLevel = (st) => { const l = st.decision?.level; if (l) return l; return st.lead ? 'verify' : 'check'; };`
  Match the file's existing style (it's plain ES; no TS syntax). Check for a
  SECOND fallback near the decision badge (`st.decision?.label || 'Verify first'`
  at the `decisionBadge` function) — that label default should follow the
  same split (`'Check first'` for non-leads); read the surrounding code and
  keep copy consistent with the web's DECISION_META wording.

**Verify**: `node --check static/app.js` → exit 0; parity cases 1 & 3 pass.

### Step 3: iOS kind list

`ios/Volta/Volta/Models.swift:278`: add `"provider_station_page"` to the
kind array. If Xcode is available run the compile-gate command; if not,
record honestly that the change is text-verified only (the parity test
covers the constant; the run-log must say the build was not run — per the
repo's never-claim-unrun-checks rule).

**Verify**: parity case 2 passes; xcodebuild success OR an explicit
"not built here" run-log note.

### Step 4: One connector normalizer, fail-closed unknowns

In `server.py`, add `normalize_connector_label(token) -> Optional[str]` —
superset alias table covering everything the five variants handle today
(CCS/COMBO→CCS2; CHADEMO→CHAdeMO; TESLA→Tesla; T2/MENNEKES/62196_T2/"type 2"/
"tipo 2"→Type 2; SCHUKO/SHUKO/DOMESTIC/SCEE→Schuko; T1/62196_T1→Type 1),
**unknown → title-cased passthrough (never a silent "Type 2")**, None/empty →
None. Rewire `connector_label`, `fenie_connector_label`,
`electromaps_connector_label` to delegate (keep their names/signatures — call
sites elsewhere expect them). Mirror the same table into
`scripts/ingest_sources.py`'s two variants (local copy is fine — parity case
4 pins them together; do NOT make the script import server.py without first
checking whether it already does: `grep -n "import server" scripts/ingest_sources.py`).

**The fail-closed default is a deliberate behavior change** (Electromaps
unknown tokens stop being claimed Tesla-compatible). Gate it with a delta
check BEFORE committing:

```bash
python3 - <<'EOF'
import json, importlib, sys
sys.path.insert(0, '.')
import server
db = json.load(open('data/free_intelligence.json'))
changed = []
for r in db.get('records', []):
    for c in (r.get('connectors') or []):
        raw = (c.get('raw_type') or c.get('std') or c.get('label') or '')
        new = server.normalize_connector_label(raw)
        old = c.get('label')
        if old and new and old != new:
            changed.append((r.get('id'), old, new))
print('records with changed labels:', len(changed))
for row in changed[:20]: print(row)
EOF
```

(Adapt the field names to the real record shape — inspect one DB record
first. The DB itself is NOT edited; labels regenerate on the next ingest
run.) If more than ~5% of connector labels would change, or ANY change flips
a label INTO the Tesla-ok set {CCS2, Type 2, Tesla}, STOP and report the
list — flips OUT of Tesla-ok are the intended fail-closed direction; flips
INTO it need owner eyes.

**Verify**: parity case 4 passes; plans/006's `tests/test_source_parsers.py`
still green (if a fixture pinned an old label, the fixture characterized the
drift — update THAT assertion, note it in the run-log with the defect id);
full suite green; delta report saved into the run-log.

### Step 5: Ledger

`D-###` rows: one per drift (legacy regex, legacy decision fallback, iOS
kind, Electromaps unknown→Type 2). `TC-###` for the parity test. Update
coverage-summary; run-log iteration with the parity-test before/after output
and the connector delta report. Artifact integrity check.

**Verify**: `python3 scripts/qa_artifact_integrity.py` → ok.

## Test plan

- `tests/test_honesty_parity.py` — 4 cases, written to fail first (step 1).
- Existing: `tests/test_ios_design_contract.py` (its Swift string assertions
  must not be broken by the Models.swift edit — run it),
  `test_source_parsers.py` + `test_live_merge.py` from plans/006,
  frontend `npm --prefix frontend run test` (format.ts untouched → must stay
  59/59 green).
- Browser DOM contracts cover the legacy shell only via `node --check` +
  `qa_accessibility_static.py` — run
  `python3 scripts/qa_accessibility_static.py` if it executes offline (it
  reads app.js statically); otherwise note the skip.

## Done criteria

- [ ] `python3 tests/test_honesty_parity.py -v` → 4/4 pass
- [ ] `node --check static/app.js` → exit 0; `grep -c "(?:en|es)/p/" static/app.js` → 0 (only the 4-language form remains)
- [ ] `grep -c "provider_station_page" ios/Volta/Volta/Models.swift` → ≥1
- [ ] One connector table: `grep -c "def .*connector_label" server.py` shows
      the three names but each delegating (spot-check by reading them)
- [ ] Connector delta report recorded; no un-reviewed flips INTO Tesla-ok
- [ ] Full offline verify green; iOS build run or explicitly recorded as not run
- [ ] D/TC rows + coverage-summary + run-log updated; artifact integrity ok
- [ ] Only in-scope files changed

## STOP conditions

Stop and report back (do not improvise) if:

- The connector delta gate trips (>5% label changes, or any flip INTO
  Tesla-ok) — product decision required.
- app.js's decision fallback turns out to be structured differently than the
  grep suggests (e.g. multiple call sites with different fallbacks) —
  enumerate them and report rather than patching one of several.
- `tests/test_ios_design_contract.py` fails after the Models.swift edit in
  any assertion other than one you can trace to your one-line change.
- You find a FIFTH surface re-deriving these rules (e.g. a QA script
  asserting the old regex) — add it to the parity test only if trivial,
  otherwise report.

## Maintenance notes

- The parity test is the drift alarm; when a rule legitimately changes,
  change ALL surfaces + the test in one commit — reviewers should treat a
  parity-test edit without four-surface edits as a red flag.
- The deferred endgame (server emits `source_tier` / `map_eligible` /
  final decision; clients delete their re-derivations) is recorded in
  plans/README.md — if/when that lands, this parity test shrinks to asserting
  the server fields exist and the clients consume them.
- `static/app.js`'s days may be numbered (see the static/-retirement decision
  in plans/README.md) — if the owner deletes the legacy shell, drop its two
  parity assertions in the same commit.
