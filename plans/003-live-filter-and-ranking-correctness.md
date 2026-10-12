# Plan 003: Make max_price honest for MapaREVE rows and make corroboration actually affect ranking

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [AGENTS.md](../AGENTS.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW–MED
- **Depends on**: plans/001 (verify script; can proceed without it using AGENTS.md §5 commands)
- **Category**: bug
- **Planned at**: commit `ceee003`, 2026-07-12

## Why this matters

Three verified defects in the live decision path:

1. The request's `max_price` is **never applied to MapaREVE rows** (the
   primary live source). Turning the "≤ €0.25" chip off sends
   `max_price=5.0`, and Gaia/Feníe/Electromaps rows up to €5.0 appear — but
   MapaREVE rows are still gated by a hardcoded 0.25 constant. The price
   filter silently behaves differently per source.
2. The cross-source **corroboration boost never reaches lead ranking** in the
   default national overview or in live mode without GPS: the score is
   computed during normalization, corroboration is attached afterwards, and
   the list is sorted by the stale score. The moat signal (N independent
   sources agree) is computed and discarded exactly where most users see leads.
3. The stale-cache "filter_fallback" path claims (engine honesty rule,
   `server.py:2656`: "stale broader fallback is re-filtered fail-closed") that
   current route filters are reapplied — but `fast_only` and `max_price` are
   never reapplied to the reused MapaREVE stations. Narrow trigger (filter
   change + upstream failure), but it violates the documented fail-closed
   contract — the app's core reliability promise.

## Current state

All excerpts verified on disk 2026-07-12.

- `server.py:117` — `PRICE_MAX_DEFAULT = 0.25`.
- `server.py:2408` (inside `transform_location`) —
  `is_cheap = price_min is not None and price_min <= PRICE_MAX_DEFAULT`
  (constant, not the request value; this field also feeds UI badges — do NOT
  change its meaning).
- `server.py:2661-2690` — `mapareve_payload(max_price, …)` takes `max_price`
  but never uses it; upstream query is bounded by
  `"energy_price_max": MAPAREVE_PRICE_QUERY_MAX` (default 0.30). The comment
  at 2680–2684 explains this upstream headroom is DELIBERATE (time-priced
  stations are parsed locally) — keep the upstream cap as is.
- `server.py:2731` (inside `fetch_mapareve`) — the keep-predicate:

```python
transformed = [x for x in transformed if x.get("is_free") or x.get("is_cheap")]
```

  This is the bug for defect 1: a €0.28 station fetched under the 0.30
  headroom is dropped even when the request allows €5.0.
- `server.py:3161` — `max_price` parsed from the query;
  `server.py:3172` — it is part of `cache_key`, so 0.25 vs 5.0 already cache
  separately (the fix makes that distinction real instead of redundant).
- `server.py:3280` — after cache/stale handling, stations are re-bounded by
  bbox only:

```python
stations = [st for st in stations if in_bbox(st.get("lat"), st.get("lon"), bbox)]
```

  Gaia (3287–3293), Feníe (3312–3318) and Electromaps (3383–3389) each apply
  four predicates — `in_bbox`, `available_only`, `fast_only`,
  `price_min is None or price_min <= max_price` — to their own rows before
  merging. The MapaREVE list never gets the last two (defect 3; for fresh
  fetches `fast_only` is pushed upstream via `power_min` at 2668/2677, so the
  gap bites on stale filter-fallback reuse and on `max_price` always).
- `server.py:3021-3023` (`overview_response`) — defect 2, site A:

```python
apply_corroboration(lead)
out.append(compact_station_for_ui(lead) if compact else lead)
    ...
out.sort(key=lambda s: -(s.get("recommendation_score") or 0))
```

- `server.py:3424-3429` (`live_response` leads loop) — defect 2, site B: the
  re-scoring `enrich_station(lead, live=False)` call sits INSIDE
  `if user_lat is not None and user_lon is not None:` — with no GPS the lead
  keeps its pre-corroboration score. (Contrast the stations loop at
  3404–3409 where `enrich_station(st, live=True)` runs unconditionally.)
- `server.py:840-876` — `recommendation_score(item)`: pure function of the
  item's fields; the corroboration term is at 871–875
  (`score += min(12, 4 * (n_fam - 1))` when `independent_sources >= 2`).
  Re-calling it is idempotent (it reads `item["score"]`, a different base
  field, and returns a value — it does not read `recommendation_score`).
- Repo conventions: stdlib only; every real bug fix gets a `D-###` row in
  `qa/defects.csv` with verification evidence, plus `qa/run-log.md` and
  `qa/coverage-summary.md` updates (AGENTS.md §6 — read the last rows of each
  and match the format exactly). New behavior gets a `TC-###` row in
  `qa/test-cases.csv`.
- Test conventions: standalone stdlib unittest files; the pattern for testing
  `live_response` with patched fetchers is `tests/test_live_coverage.py`
  (it patches `server.fetch_mapareve` etc. with `unittest.mock.patch`) —
  model new tests on it.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Offline verify | `./scripts/verify.sh` (or AGENTS §5 steps 1–2) | exit 0, 219+ tests OK |
| Just the new tests | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_filter_ranking_correctness.py -v` | all pass |
| Compile | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -m py_compile server.py` | exit 0 |

## Scope

**In scope**:
- `server.py` — ONLY: the keep-predicate in `fetch_mapareve` (~2731), a new
  shared predicate applied in `live_response` (~3280 region), one line each in
  `overview_response` (~3021) and the live leads loop (~3424), and (if needed)
  a small helper.
- `tests/test_filter_ranking_correctness.py` (create)
- `qa/defects.csv`, `qa/test-cases.csv`, `qa/coverage-summary.md`,
  `qa/run-log.md` (ledger updates)

**Out of scope** (do NOT touch):
- `is_cheap` / `is_free` semantics in `transform_location` — UI badges and
  QA contracts read them; their meaning ("cheap" = under the app's 0.25 cap)
  must not change.
- `mapareve_payload`'s upstream `energy_price_max` headroom — documented
  deliberate.
- The cache-key format (3172) — plan 007 touches caching; don't pre-empt it.
- `frontend/`, `static/`, `ios/` — server-side fix only.
- `_normalize_lead` / `enrich_station` internals.

## Git workflow

- Work on `main`; one commit per defect (3 commits) or one combined — match
  history's short imperative style (e.g. `Apply request price cap to MapaREVE rows`).
  Push per AGENTS.md §8 unless the operator said otherwise.

## Steps

### Step 1: Write the failing tests first

Create `tests/test_filter_ranking_correctness.py` modeled on
`tests/test_live_coverage.py` (same import style, same `unittest.mock.patch`
approach, standalone `unittest.main()`). Cases:

1. **max_price honored for MapaREVE**: patch `server.fetch_mapareve` to
   return a payload whose `stations` include one row with
   `price_min=0.28, is_free=False, is_cheap=False` (build it via a realistic
   station dict — copy a fixture shape from test_live_coverage.py) plus one
   `price_min=0.20` row; patch gaia/fenie fetchers to empty. Call
   `server.live_response({"max_price": ["5.0"], "include_leads": ["false"],
   "bbox": [...]})` → both rows present. Call with `max_price=["0.25"]` →
   only the 0.20 row. NOTE: with the current code the first assertion FAILS
   because fetch_mapareve's keep-predicate drops the 0.28 row — that is the
   point. You will need to patch at the right level: patch the UPSTREAM
   (`server.fetch_mapareve`) only for the live_response-tail cases, and test
   `fetch_mapareve`'s own predicate separately by patching
   `urllib.request.urlopen` or by extracting the predicate (step 2) and unit
   testing it directly. Choose the extraction route — it's cleaner.
2. **Stale filter_fallback reapplies fast/price**: prime `server.CACHE` with
   a payload (key for `fast_only=False`) containing a non-fast station and a
   `price_min=0.28` station; make `fetch_mapareve` raise; request with
   `fast=["true"]`/`max_price=["0.25"]` → response carries
   `cache.filter_fallback == True` AND neither offending row. Model the
   CACHE-priming on `tests/test_live_coverage.py`'s stale-cache cases
   (lines ~240–290 exercise exactly this branch).
3. **Corroboration affects overview ordering**: two leads with equal base
   fields, one carrying `sources` from 3 distinct families, one single-source
   (family detection: `server.station_families` — reuse fixture shapes from
   `tests/test_corroboration.py`); patch `server.load_all_leads` to return
   them; call `overview_response` → the corroborated lead sorts first and its
   `recommendation_score` is strictly greater.
4. **Corroboration affects live leads without GPS**: same two leads via
   patched `load_all_leads`, empty live sources, `include_leads=["true"]`, NO
   lat/lon params → corroborated lead first in `payload["leads"]`, score reflects boost.

**Verify**: `python3 tests/test_filter_ranking_correctness.py -v` → the new
tests FAIL (assert the failure messages match the defects, not import errors).

### Step 2: Extract and apply a shared route-filter predicate

In `server.py`, add a small helper near the other pure helpers:

```python
def station_passes_route_filters(st: Dict[str, Any], *, max_price: float, fast_only: bool) -> bool:
    if fast_only and not st.get("fast"):
        return False
    price_min = safe_float(st.get("price_min"))
    return price_min is None or price_min <= max_price
```

Then:
- In `fetch_mapareve` (~2731), change the keep-predicate to
  `if x.get("is_free") or x.get("is_cheap") or (x.get("price_min") is not None and x["price_min"] <= max_price)`
  — i.e. free/cheap rows always survive (unchanged), and pricier rows survive
  up to the REQUEST cap. Rows with no parsed price stay dropped from
  MapaREVE (unknown price ≠ presentable; preserves current fail-closed
  behavior — state this in the commit message).
- In `live_response` immediately after the bbox re-filter (~3280), apply the
  helper to the MapaREVE `stations` list unconditionally:
  `stations = [st for st in stations if station_passes_route_filters(st, max_price=max_price, fast_only=fast_only)]`
  and count removals into `dropped["rejected_live"]` (or add a new
  `dropped["route_filtered"]` counter — check `meta.engine.counts` consumers:
  grep `dropped\[` and `route_filtered` in `scripts/qa_*.py` first; if nothing
  consumes the dict keys strictly, prefer the new explicit counter).
  This is a no-op for fresh fast-filtered fetches and closes the stale-reuse gap.

**Verify**: test cases 1–2 pass; `python3 -m unittest discover -s tests` →
all existing tests still pass (test_live_coverage exercises these branches
heavily — if one of its assertions breaks, READ it: if it asserted the buggy
behavior, update it and say so in the run-log; if it asserts something else,
your edit is wrong).

### Step 3: Re-score after corroboration (both sites)

- `overview_response` (~3021): after `apply_corroboration(lead)`, add
  `lead["recommendation_score"] = recommendation_score(lead)` BEFORE the
  compact/append line (compact_station_for_ui copies fields — score must be
  final first).
- `live_response` leads loop (~3424): after `apply_corroboration(lead)`, add
  the same unconditional re-score line (leave the distance/eta/enrich block
  inside the coords guard exactly as is; with coords, `enrich_station` runs
  after and remains authoritative — the explicit re-score is then redundant
  but harmless and keeps the invariant obvious).

**Verify**: test cases 3–4 pass; full suite green.

### Step 4: QA ledger

Three defect rows in `qa/defects.csv` (one per defect: severity Medium/High —
match the ledger's own severity vocabulary; repro = the failing test name
pre-fix; verification evidence = the exact passing-test output post-fix).
One or more `TC-###` rows in `qa/test-cases.csv` mapping to the new test
file. Update `qa/coverage-summary.md` counts and append the iteration to
`qa/run-log.md` with real command output. Read the last rows/entries of each
file FIRST and match their format exactly — `qa_artifact_integrity` checks
CSV shape and cross-references.

**Verify**: `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 scripts/qa_artifact_integrity.py`
→ ok (it validates the ledger shape; if it requires a running server or
regenerates a `*-latest.json`, follow its own output).

## Test plan

Covered by step 1 (4 regression cases, written to fail first). Existing
guards: `tests/test_live_coverage.py` (stale/fallback semantics),
`tests/test_corroboration.py` (corroboration invariant: it asserts
corroboration never sets live verification — your change must keep that
true; you are touching ranking only).

## Done criteria

- [ ] `python3 tests/test_filter_ranking_correctness.py -v` → all pass
- [ ] `python3 -m unittest discover -s tests` → OK (223+ tests)
- [ ] `./scripts/verify.sh` (or §5 1–2) → green
- [ ] `grep -n "is_free.*or x.get(\"is_cheap\")$" server.py` → no bare
      free/cheap-only keep-predicate remains in fetch_mapareve
- [ ] Defect rows + TC rows + coverage-summary + run-log updated; artifact
      integrity check passes
- [ ] Only files in Scope changed (`git status`)

## STOP conditions

Stop and report back (do not improvise) if:

- `tests/test_live_coverage.py` fails after step 2 in a way that is NOT
  "the test pinned the buggy behavior" — the stale-fallback semantics are the
  app's core promise; do not force them green.
- You find a consumer (frontend, QA script, iOS) that depends on MapaREVE
  rows being capped at 0.25 regardless of request (grep `is_cheap` and
  `price_min` in `frontend/src` and `scripts/qa_*.py` first) — the fix would
  then need a product decision.
- `meta.engine.counts` keys turn out to be asserted by a QA contract that
  breaks on the new counter.
- The corroboration invariant test (`test_corroboration.py`) fails for any reason.

## Maintenance notes

- Plan 007 (performance) restructures the enrich/caching tail; it must keep
  `station_passes_route_filters` applied to cached/stale payload reuse — call
  this out in that PR's review.
- If a per-source price semantics ever becomes deliberate (e.g. "MapaREVE is
  only ever the cheap layer"), encode it as an explicit constant + engine
  honesty-rule string, not an accidental predicate.
- The `recommendation_score` idempotency assumption (reads `score`, writes
  nothing) is load-bearing for the double-scoring in the coords path; if that
  function ever starts reading `recommendation_score`, revisit both call sites.
