# Plan 007: Stop recomputing static intelligence per request — memoize enrichment, bucket the dedup, reuse the assembled payload

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [AGENTS.md](../AGENTS.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P2 (high value, but correctness plans go first)
- **Effort**: L
- **Risk**: MED–HIGH (touches the live request path and caching semantics; mitigated by plans/006 characterization tests + the equality gates below)
- **Depends on**: plans/006 (REQUIRED — do not start without it landed), plans/004 (locking convention), plans/003 (filter predicate placement)
- **Category**: perf
- **Planned at**: commit `ceee003`, 2026-07-12

## Why this matters

Measured on this machine (2026-07-12 audit, python3 timing on the real 7 MB
DB, ~955 records):

- `load_intelligence(SPAIN_BBOX)` costs **~190 ms of pure CPU per call** and
  is memoized only at the RAW-parse layer — every call re-deep-copies and
  re-normalizes (full `enrich_station` pipeline) every in-bbox record. A
  second consecutive call: ~196 ms (zero reuse).
- `remove_duplicate_leads(959 leads × 300 stations)` is O(n×m) with haversine
  + name tokenization per pair: **~865 ms** on a wide viewport.
- The 60 s live cache stores only the RAW MapaREVE payload — a cache HIT
  still runs viewport filter, two merges, lead load + dedup, Electromaps
  refresh, and the full per-station enrich loop. A hit is nearly as expensive
  as a miss.
- The SPA's "instant" first-paint query always carries a bbox, so it routes
  past the overview bytes-cache into the uncached path (~200 ms serialize+
  enrich per viewport change), and the bytes cache itself is a SINGLE slot —
  any filter toggle evicts it.

This is a decision tool used while driving; server CPU latency sits directly
on the first-paint and refresh budgets the QA suite tracks
(`scripts/qa_first_render_budget.js`, 4000 ms).

## Current state

Excerpts verified 2026-07-12 (re-grep symbols; line numbers will have moved
if plans/003–005 landed).

- `server.py:2827-2842` — `_INTEL_RAW` mtime cache holds the RAW parsed JSON
  only. `server.py:2845-2859` — `load_intelligence(bbox)`:

```python
data["records"] = [
    normalized for r in records
    for normalized in [_normalize_lead(deepcopy(r), "free/cheap intelligence DB")]
    if not reject_station_reason(normalized)
]
```

  (deepcopy is there because callers MUTATE returned rows — corroboration,
  distance fields, source unions. Any shared cache must keep handing out
  copies.)
- `server.py:2862-2880` — `load_all_leads(bbox)` = normalized intel + seed
  leads, deduped by rounded coord / name key.
- `server.py:1813-1824` — `remove_duplicate_leads`: linear scan
  `next((st for st in stations if likely_same_station(lead, st)), None)` per
  lead. `likely_same_station` (`:1769-1782`): id match → URL-set intersection
  → haversine ≤ 0.12 km + `_name_affinity`. NOTE the URL branch matches at
  ANY distance — a grid bucketed by location must therefore ALSO keep a
  URL-keyed index, or it changes semantics (plans/006 test 1b pins this).
- `merge_gaia_stations` (`:1799`) and `merge_fenie_stations` (`:2026`, also
  reused for Electromaps at `:3390`) — same O(n×m) shape.
- `server.py:3149+` — `live_response`: single-slot `CACHE` keyed
  `f"{tile}{max_price}:{available_only}:{fast_only}"`; on hit (3181-3193)
  deep-copies raw payload, re-fetches gaia/fenie (their own 60s caches
  inside), then the shared tail: bbox filter (3280), merges (3294/3319),
  leads load+dedup (3341-3346), Electromaps block (3348-3401), enrich loops
  (3403-3435). Distance fields depend on per-request lat/lon.
- `server.py:3003-3042` — `overview_response` (pure function of DB+filters);
  `server.py:3048-3069` — `OVERVIEW_BYTES_CACHE` single `"entry"` keyed
  (db mtime, seeds mtime, max_price, fast_only, compact);
  `Handler.do_GET` (3930-3936) uses it ONLY when `overview=true` and NO bbox.
- Locks: plans/004 added `_CACHE_LOCKS` — reuse it (add keys, same pattern).
- Conventions: stdlib only; perf claims need measured evidence in
  `qa/run-log.md` (the repo's discipline: real command output, never
  asserted numbers).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Full offline verify | `./scripts/verify.sh` | exit 0 (≥235 tests after plan 006) |
| Characterization guard | `python3 tests/test_live_merge.py -v` | all pass, UNMODIFIED |
| Perf probe (created in step 1) | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 scripts/perf_probe.py` | prints timings |

## Scope

**In scope**:
- `server.py` — an enriched-leads memo layer around
  `load_intelligence`/`load_all_leads`; spatial index for the three merge/
  dedup helpers; assembled-payload reuse in `live_response`; multi-slot
  `OVERVIEW_BYTES_CACHE`; bbox-scoped overview reuse.
- `scripts/perf_probe.py` (create — small, stdlib, offline timing harness)
- `tests/test_perf_invariants.py` (create — equality gates, not timing)
- Ledger files.

**Out of scope** (do NOT touch):
- Payload FIELD content (audit finding PERF-05, a leaner first-paint
  projection, was deliberately deferred — see plans/README.md; do not slim
  fields here).
- Upstream fetch budgets/TTL values; the stale-fallback DECISION logic
  (which branch fires when) — you may reuse its outputs, never change its
  semantics.
- `frontend/` — client behavior must not need to change for this plan.

## Git workflow

- `main`; one commit per step group (memo layer / spatial dedup / payload
  reuse / overview slots), each leaving the suite green. Push per AGENTS.md §8.

## Steps

### Step 1: Baseline probe + equality harness FIRST

Create `scripts/perf_probe.py` (stdlib `time.perf_counter`, imports `server`):
times (a) `load_all_leads(SPAIN_BBOX)` twice (cold/warm), (b)
`remove_duplicate_leads` on the real leads vs a synthetic 300-station list
placed on top of real lead coords (deterministic — build stations by copying
leads' coords/names), (c) `overview_response` compact. Prints a table.

Create `tests/test_perf_invariants.py`: golden-equality gates that do NOT
time anything —
1. `load_all_leads(bbox)` result (sorted by id, deep-compared minus any
   per-call timestamps) equals itself across two consecutive calls;
2. capture the CURRENT `remove_duplicate_leads`/`merge_gaia_stations` outputs
   for the plans/006 fixture sets into local expected values (import and call
   — before your refactor these pass trivially; after, they prove equality).

Run the probe; record the baseline numbers in the run-log draft.

**Verify**: probe prints ~190 ms warm `load_all_leads(SPAIN)` and ~0.5–1 s
dedup (machine-dependent; record whatever it prints); invariants test green.

### Step 2: Memoize normalized+enriched leads by data mtime

Add a second memo layer next to `_INTEL_RAW`:

```python
_INTEL_ENRICHED: Dict[str, Any] = {"key": None, "records": None}  # guarded by _CACHE_LOCKS["intel"]
```

- Key: (db mtime, seeds mtime) — BOTH, because `load_all_leads` folds seeds.
- On miss: normalize/enrich ALL records once (the current per-record pipeline,
  no bbox filter), store the finished list plus a prebuilt coord array for
  fast bbox scans. On hit: bbox-filter the cached list, `deepcopy` ONLY the
  in-view subset, return.
- Restructure `load_intelligence`/`load_all_leads` on top of it. Preserve
  BOTH functions' exact output shape (dict with records / merged list —
  callers: overview_response, live_response, `/api/intel`, and grep for
  others). The lead-vs-seed dedup keying (rounded coord / name) must run on
  the same data it does today.
- Memory note: one enriched copy of ~955 records ≈ tens of MB — acceptable
  single-process; do NOT also cache per-bbox copies here.

**Verify**: `python3 tests/test_perf_invariants.py` green (byte-equal
outputs); full suite green; probe: warm `load_all_leads(SPAIN)` **< 100 ms**
(expect ~a deepcopy of 955 rows, ~88 ms measured; zoomed bboxes ~12 ms).

### Step 3: Spatial-bucket the pairwise matchers

Add an index builder used by all three merge/dedup helpers:

```python
def _station_match_index(stations): 
    # grid: dict[(int(lat/0.002), int(lon/0.002))] -> [i]   (~200 m cells)
    # urls: dict[url] -> [i]   (mirrors station_source_urls)
    # ids:  dict[lower_id] -> i
```

Candidate set for a lead = id hit ∪ url hits ∪ 3×3 neighbor cells; then run
the UNCHANGED `likely_same_station` on candidates only (first-match-by-index
order must be preserved for `merge_gaia_stations` — iterate candidates in
ascending index). The 0.12 km distance gate fits inside a 3×3 of 0.002°
cells at Spain's latitudes (cell ≈ 222 m N-S, ≈ 160–180 m E-W; radius check
remains exact inside `likely_same_station`, the grid only prunes).
`merge_fenie_stations` keeps its own (different, ~80 m proximity) predicate —
bucket it the same way but with its own radius; read the function first.
Rebuild the index after each merge stage mutates the stations list (or
append-update it — simplest correct thing wins).

**Verify**: `python3 tests/test_live_merge.py -v` — UNMODIFIED file, all
green (this is the point of plans/006); invariants test green (identical
outputs on the fixture sets); probe: the 959×300 dedup **< 100 ms**.

### Step 4: Make a live cache HIT actually cheap

Cache the ASSEMBLED, pre-distance payload alongside the raw one:

- After the tail finishes building `stations` + `leads` (post-merge,
  post-enrich, post-route-filter, BEFORE the per-request distance/eta
  injection and final sort), store
  `CACHE["assembled"] = {"key": cache_key, "include_leads": include_leads, "ts": now, "stations": deepcopy(stations), "leads": deepcopy(leads), "warning": payload.get("warning"), ...}`
  — ONLY on the fully-fresh path (cache miss with successful fetch, no stale
  flags). Never store assembled results from stale/fallback branches.
- On a fresh cache HIT (`cache_fresh` true) where an assembled entry matches
  (same cache_key, same include_leads, within TTL, and gaia/fenie/electromaps
  sub-caches were hits — detectable from their payloads' `cache.hit` flags;
  if ANY sub-source did live work this request, fall through to the full
  tail), deep-copy the assembled rows, apply per-request distance/eta + sort
  (the cheap loop), and skip merges/dedup/enrich entirely.
- The bbox nuance: assembled rows were filtered to the SNAPPED envelope's
  request bbox; a hit with a different exact bbox under the same snapped key
  must re-run `in_bbox` on the assembled rows (cheap) — mirror the existing
  re-bound comment at the top of the tail.
- Guard all reads/writes with `_CACHE_LOCKS["live"]` per plans/004's pattern.

This is the riskiest step. The safety rails: stale/fallback paths are
UNTOUCHED (they never see assembled entries); any sub-source cache miss
bypasses reuse; `tests/test_live_coverage.py` pins every fallback branch.

**Verify**: full suite green, three consecutive runs; add one test to
`test_perf_invariants.py`: two identical `live_response` calls (patched
fetchers, second call within TTL) return equal payloads (minus
`cache.age_seconds` / timing meta), and with a call-counter on the patched
`enrich_station` proving the second request did NOT re-enrich
(`unittest.mock.patch("server.enrich_station", wraps=server.enrich_station)`).

### Step 5: Multi-slot overview cache + bbox'd overview reuse

- `OVERVIEW_BYTES_CACHE`: single `"entry"` → small dict keyed by the full
  key tuple, FIFO/LRU capped at 8 entries (evict oldest under its lock).
- Extend the Handler fast-path (do_GET, currently `overview AND no bbox`) to
  ALSO serve bbox-carrying overview requests from a second bounded memo
  (key = full tuple + the bbox string, cap ~16): after step 2 the compute is
  cheap (~12 ms zoomed), so this is optional icing — implement only if it
  stays under ~30 lines; otherwise note "compute now cheap, memo skipped"
  in the run-log and move on.

**Verify**: two alternating filter-toggle overview requests both serve from
cache on their second occurrence (add a counter assertion or check
`perf_probe` timings); suite green.

### Step 6: Measure, record, ledger

Re-run `scripts/perf_probe.py`; append run-log iteration with the
BEFORE/AFTER table (real output, both runs). `TC-###` rows for the new
invariant tests; coverage-summary update. This plan fixes a performance
defect class — one `D-###` row referencing the measured before/after is
appropriate (match ledger vocabulary; severity per its conventions).

**Verify**: `python3 scripts/qa_artifact_integrity.py` → ok.

## Test plan

- plans/006's `test_live_merge.py` (unmodified) — semantic equality of the
  bucketed matchers.
- `tests/test_perf_invariants.py` — output equality + no-re-enrich-on-hit.
- `tests/test_live_coverage.py` (unmodified) — every stale/fallback branch.
- `scripts/perf_probe.py` — the numbers, recorded not asserted.

## Done criteria

- [ ] Full offline verify green; `test_live_merge.py` and
      `test_live_coverage.py` pass UNMODIFIED
- [ ] Probe (recorded in run-log): warm `load_all_leads(SPAIN)` < 100 ms;
      959×300 dedup < 100 ms; overview compact serialize served from cache on repeat
- [ ] Second identical live request within TTL provably skips re-enrichment
      (mock-counter test)
- [ ] Stale/fallback branches never serve assembled entries (code-review
      checkable: assembled reuse is inside the `cache_fresh` branch only)
- [ ] Ledger + run-log (with before/after table) updated
- [ ] Only in-scope files changed

## STOP conditions

Stop and report back (do not improvise) if:

- plans/006 has not landed (its tests are this plan's safety net — hard stop).
- Any characterization or live-coverage test needs MODIFICATION to pass —
  that means semantics changed; revert the step and report.
- The equality gate finds a diff between old and new `load_all_leads` output
  beyond per-call timestamps — enumerate the differing fields and stop.
- Memory of the enriched memo measurably exceeds ~150 MB RSS growth (check
  with `ps` before/after import+first call) — the 256 MB Fly VM constraint
  makes that a product decision.
- Step 4's sub-source-hit detection can't be made reliable from existing
  `cache.hit` flags — implement steps 2/3/5 only, record step 4 as BLOCKED
  in plans/README.md with what you found.

## Maintenance notes

- Weekly DB refresh changes the mtime → both memo layers invalidate
  naturally; nothing to do.
- If plans/008 (or any change) alters enrichment output, the memo is
  transparent — but the assembled-payload reuse in step 4 must keep matching
  the tail's semantics; reviewers of future tail changes should grep for
  `CACHE["assembled"]` and re-check the reuse conditions.
- Deferred relatives (see plans/README.md): leaner first-paint projection
  (PERF-05) — measure again after this lands before deciding; ETag/304 +
  static byte memo (PERF-07) — low impact.
