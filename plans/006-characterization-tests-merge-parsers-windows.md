# Plan 006: Pin the untested load-bearing behavior — live merge/dedup, ingest parsers, overnight pricing windows

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [AGENTS.md](../AGENTS.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P1 (prerequisite for plans/007 and plans/008)
- **Effort**: M
- **Risk**: LOW (tests + fixtures only; zero product-code changes)
- **Depends on**: plans/001 (optional convenience)
- **Category**: tests
- **Planned at**: commit `ceee003`, 2026-07-12

## Why this matters

The 2026-07-12 audit confirmed the honesty/reliability core is well tested
(219 green backend tests), but three load-bearing areas have **zero**
coverage, and two of them are about to be refactored (plans/007 rewrites the
dedup for performance; plans/008 touches decision-adjacent labels):

1. The **live merge/dedup** that decides "these two rows are the same
   physical charger" (`likely_same_station`, `merge_gaia_stations`,
   `merge_fenie_stations`, `remove_duplicate_leads`, `source_rows_union`) is
   never called with a matching pair in any test — every `live_response` test
   patches the feeds to empty lists, so the positive-match branch has never
   executed under test. A regression here either collapses distinct chargers
   or double-pins the map.
2. The **per-source ingest parsers** in `scripts/ingest_sources.py`
   (`source_osm`, `source_openchargemap`, `source_electromaps_probe`,
   `source_electromaps_mapi`, `source_chargemap_mappy`, `source_fenie`, …)
   are unit-untested; the only guard is a network-dependent canary. The
   defect ledger's own D-006 (a silently dropped Electromaps source family)
   is exactly this failure class — this plan converts it into an offline test
   failure.
3. **Overnight pricing windows**: `pricing_window_is_current` has a
   midnight-wraparound branch (`start > end → minute >= start or minute < end`)
   and `day_allowed` gates by weekday — neither has a single assertion
   (`grep -n "22:00" tests/test_pricing_windows.py` → nothing). This logic
   feeds `decision_for`'s "Cheap later" vs "Go now" — honesty-engine output.

## Current state

Verified on disk 2026-07-12.

- `server.py:1769-1782` — `likely_same_station`: same-id OR shared source URL
  OR (≤0.12 km AND `_name_affinity`). `server.py:1799-1810`
  `merge_gaia_stations`: first match by index, gaia row REPLACES the matched
  station and takes the source union (`gaia["sources"] = source_rows_union(gaia, previous)`),
  `source_count` updated. `server.py:1813-1824` `remove_duplicate_leads`:
  matching lead folds its sources INTO the station (corroboration by union)
  and is dropped; non-matching leads survive. `server.py:2026+`
  `merge_fenie_stations`: DIFFERENT semantics — bare proximity (~80 m; read
  the function before writing tests) — characterize as-is, do not "fix".
  NOTE: `merge_fenie_stations` is also reused for Electromaps promotion at
  `server.py:3390`.
- `grep -rn "merge_gaia_stations\|remove_duplicate_leads\|likely_same_station\|merge_fenie_stations" tests/` → no matches (confirmed).
- `server.py:983-1002` — `pricing_window_is_current(window, now=None)`:
  `now` is injectable; converts to `MADRID_TZ`; wraparound branch at
  1000-1002. `server.py:968-981` — `day_allowed(days, now)` normalizes day
  aliases (read it for exact vocabulary before writing fixtures).
  `pricing_status(windows, price_cap=…, now=…)` (`server.py:1097`) is the
  higher-level API also taking `now`.
- `tests/test_pricing_windows.py` — existing pattern to extend: same-day
  windows only (`00:00–10:00`, `10:00–24:00` fixtures around lines 16–62).
- `tests/test_ingest_merge.py` — the exemplar for testing
  `scripts/ingest_sources.py` (read its import header for the sys.path
  idiom it uses to import the script as a module; it tests `add_record`/
  `dedupe`/classifiers on post-parse records).
- `scripts/ingest_sources.py:85` — `fetch_json` is the network entry the
  parsers call; `cached_overpass` similarly for OSM. Parsers to cover (grep
  `def source_` for the authoritative list): `source_osm` (~1241),
  `source_openchargemap` (~1349), `source_electromaps_probe` (~1445),
  `source_electromaps_mapi` (~1552), `source_chargemap_mappy` (~1823),
  `source_fenie` (~1957).
- Real captured payloads exist under `data/cache/` (ingestion HTTP cache,
  gitignored) — use them as the SOURCE for fixture material (trim to 2–3
  entries), but the fixtures themselves must be committed under `tests/`
  (data/cache is deletable). Keep each fixture small (<10 KB); redact
  nothing blindly — these are public-data payloads, but eyeball them for
  anything odd before committing.
- Conventions: stdlib unittest, standalone files, UTF-8 Spanish text
  preserved; NEW test behavior gets `TC-###` rows; this plan fixes no bugs,
  so NO `D-###` rows — unless a characterization test EXPOSES a live bug, in
  which case: capture it as a defect row with the failing assertion as
  evidence, mark the test `@unittest.expectedFailure` with a comment naming
  the defect id, and report it (STOP condition C) rather than fixing it here.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| New tests | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_live_merge.py -v` | all pass |
| New tests | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_source_parsers.py -v` | all pass |
| Extended tests | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_pricing_windows.py -v` | all pass |
| Whole suite | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -m unittest discover -s tests` | OK, count grows |

## Scope

**In scope** (create/extend):
- `tests/test_live_merge.py` (create)
- `tests/test_source_parsers.py` (create)
- `tests/fixtures/` or inline fixture dicts (follow whatever
  `test_ingest_merge.py` does — if it inlines dicts, inline; do not invent a
  new fixtures layout if one exists)
- `tests/test_pricing_windows.py` (extend, do not rewrite)
- `qa/test-cases.csv`, `qa/coverage-summary.md`, `qa/run-log.md`

**Out of scope** (do NOT touch):
- ANY product code: `server.py`, `scripts/ingest_sources.py`. This plan is
  characterization — it pins what IS, including oddities (e.g. the Feníe 80 m
  proximity-only merge vs Gaia's name-affinity rule; the Electromaps
  unknown-connector → "Type 2" default). Note oddities in test comments;
  plans/007/008 act on them.
- `data/free_intelligence.json`, `data/cache/` contents.

## Git workflow

- `main`; one commit per test file is fine. Imperative style. Push per
  AGENTS.md §8 unless told otherwise.

## Steps

### Step 1: `tests/test_live_merge.py`

Build minimal station/lead dicts (copy the field shapes from
`tests/test_live_coverage.py` fixtures — id, name, lat, lon, sources list of
`{name,url}` dicts). Cases (one test method each):

1. `likely_same_station`: (a) same id, different coords → True;
   (b) shared source URL, 5 km apart → True (characterizes that URL match
   ignores distance — comment it); (c) 100 m apart + shared name tokens →
   True; (d) 100 m apart + unrelated names → False; (e) 200 m apart + same
   name → False (distance gate).
2. `merge_gaia_stations`: one matching pair → merged list length, gaia row
   won the slot, `sources` is the union (assert both source URLs present),
   `source_count` == len(sources); one non-matching gaia row → appended.
3. `remove_duplicate_leads`: matching lead dropped AND its source folded into
   the station (union + count); non-matching lead survives untouched.
4. `merge_fenie_stations`: read the function first; characterize its
   proximity threshold with a just-inside and just-outside pair; assert
   whatever the CURRENT semantics are (comment the asymmetry vs
   likely_same_station).
5. `source_rows_union`: dedupes by (name,url), caps at 10.

**Verify**: `python3 tests/test_live_merge.py -v` → all pass.

### Step 2: `tests/test_source_parsers.py`

For each parser family (minimum: OSM, OpenChargeMap, one Electromaps variant,
Chargemap-mappy, Feníe — grep `def source_` in scripts/ingest_sources.py for
the authoritative list and cover what exists):

1. Extract a REAL payload from `data/cache/` (find the right cache file by
   grepping cache filenames for the family name), trim to 2–3 entries.
2. Monkeypatch the network entry (`ingest_sources.fetch_json` /
   `cached_overpass` — read each parser to find what it calls) to return the
   fixture.
3. Call the parser; assert: expected record count, and for one record the
   load-bearing fields — id, lat, lon, price/free evidence, source URL, the
   fields `add_record` requires.
4. One malformed-payload case per family (missing key / empty list) →
   parser returns no records WITHOUT raising (if it currently raises, that is
   a finding — STOP condition C, do not fix here).

If `data/cache/` lacks a family's payload (it is deletable), reconstruct a
minimal payload from the parser's own field accesses (read the code, build
the smallest dict it fully consumes) and comment that it is synthetic.

**Verify**: `python3 tests/test_source_parsers.py -v` → all pass, runs
offline (`grep -n "urlopen\|urllib" tests/test_source_parsers.py` → only in
mock/patch context if at all).

### Step 3: Extend `tests/test_pricing_windows.py`

Append test methods (match the file's existing style/fixtures):

1. Overnight window `22:00–06:00`: current at 03:00 → True; at 12:00 →
   False; at exactly 22:00 → True (start inclusive); at exactly 06:00 →
   False (end exclusive). Use `pricing_window_is_current(window, now=datetime(..., tzinfo=server.MADRID_TZ))`
   with fixed datetimes — no wall-clock dependence.
2. `day_allowed`: a window restricted to a weekday vocabulary the code
   accepts (read `day_allowed` at server.py:968-981 for the alias map) →
   allowed on that weekday, rejected on another; empty/None days → allowed.
3. One `pricing_status(...)` end-to-end: an overnight-cheap window at a
   fixed 23:00 Madrid `now` → status reflects a currently-valid cheap window
   (read `pricing_status` to assert its actual output keys — do not guess).

**Verify**: `python3 tests/test_pricing_windows.py -v` → all pass including
the pre-existing cases.

### Step 4: Ledger

`TC-###` rows for the three coverage areas (map them to the test files);
update `qa/coverage-summary.md` (it tracks what is and isn't covered — the
audit's coverage gaps for merge/parsers/overnight windows should flip to
covered); append run-log iteration with the real new totals from
`unittest discover`.

**Verify**: `python3 scripts/qa_artifact_integrity.py` → ok.

## Test plan

This plan IS the test plan. Target: `unittest discover` total grows by
roughly 15–25 tests, all offline, suite still completes in ~1s
(the current suite runs in 0.64s — if your additions push it past ~5s,
something is doing I/O it shouldn't).

## Done criteria

- [ ] Three test files green individually and via `unittest discover` (total ≥ 235)
- [ ] `tests/test_source_parsers.py` covers ≥5 source families incl. one malformed-payload case each
- [ ] Overnight + weekday window assertions exist (grep `22:00` in tests/test_pricing_windows.py → hits)
- [ ] Suite runs offline (disconnect/no-network sanity if feasible) and in <5s
- [ ] No product code modified (`git diff --stat` shows only tests/ + qa/)
- [ ] TC rows + coverage-summary + run-log updated; artifact integrity ok
- [ ] `plans/README.md` row updated

## STOP conditions

Stop and report back (do not improvise) if:

- (A) You cannot import `ingest_sources` the way `test_ingest_merge.py` does
  (environment drift).
- (B) A parser's cache payload format in `data/cache/` doesn't match what the
  parser consumes (upstream shape drifted since the cache was written) —
  report which family; do not hand-wave a fixture that never matched reality.
- (C) A characterization test exposes a REAL bug (e.g. malformed payload
  raises, merge drops sources, overnight window evaluates wrong): record the
  defect row, mark the test `expectedFailure` with the defect id, finish the
  rest of the plan, and report the finding prominently. Do not fix product
  code under this plan.

## Maintenance notes

- plans/007 (spatial-bucket dedup) and plans/008 (connector-label
  unification) MUST run these tests before/after their refactors — that is
  this plan's entire purpose; their reviewers should reject any change that
  edits these tests to make a refactor pass without explanation.
- When a source family's upstream shape changes for real, update the fixture
  from a fresh `data/cache/` capture and note it in the run-log — the fixture
  is the contract of record for that parser.
- The characterized oddities (Feníe 80 m proximity-only merge; URL-match
  ignoring distance) are inputs to any future merge-unification work
  (deferred DEBT-04 — see plans/README.md).
