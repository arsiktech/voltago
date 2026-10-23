# Plan 004: A bad timestamp or a race must never kill a request — guard, coerce, and lock

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [current contracts](../docs/backend-engine.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (adds locking to the hot path — must never hold a lock across a network fetch)
- **Depends on**: plans/001 (verify script; optional), best executed after plans/003 (both edit server.py — avoid overlapping diffs)
- **Category**: bug
- **Planned at**: commit `ceee003`, 2026-07-12

## Why this matters

Three verified reliability defects in a server that runs `ThreadingHTTPServer`
(one thread per connection, `server.py:4018`) with **zero** synchronization
(`grep -n "threading\|Lock" server.py` → no matches):

1. **No top-level exception guard**: `do_GET` (`server.py:3926-3961`) calls
   `live_response` / `geocode_search` / `load_intelligence` bare. Any uncaught
   exception aborts the connection mid-response (BaseHTTPRequestHandler emits
   no error reply from handler exceptions). The SPA then treats it as
   "live source down" and silently falls back to leads
   (`frontend/src/lib/format.ts`, `chooseChargersData`) — masking real bugs.
2. **Naive timestamps crash arithmetic**: `parse_iso` (`server.py:248-259`)
   returns a timezone-NAIVE datetime for offset-less inputs;
   `age_minutes` (`server.py:262-266`) subtracts it from an AWARE
   `datetime.now(timezone.utc)` → `TypeError`. Reachable from external feed
   data: `live_response` calls `age_minutes(l.get("last_seen"))` at
   `server.py:3364` with no try/except (one malformed Electromaps lead
   timestamp = dead request, via defect 1), and inside `fetch_gaia_live` a
   naive feed timestamp silently drops the whole Gaia source for the request.
3. **Unlocked check-then-act on 7 module-level caches** mutated by concurrent
   request threads: `CACHE` (read-decide 3175-3184 / write 3209 / stale-read
   3226-3245), `GAIA_CACHE`, `FENIE_CACHE`, `ELECTROMAPS_CACHE` (write+evict
   2124-2127, mutated by an 8-worker `ThreadPoolExecutor` within one request),
   `GEOCODE_CACHE` (write+evict 2932-2935), `OVERVIEW_BYTES_CACHE`
   (3062-3068), `STATIC_GZ_CACHE` (3988-3996, `clear()` under load at 3993).
   The eviction loops (`for k in list(D.keys())[:N]: D.pop(k)`) can raise
   `RuntimeError: dictionary changed size during iteration` under concurrent
   insert, and interleaved CACHE read/update can serve one filter-set's
   payload under another's key.

## Current state

Excerpts verified on disk 2026-07-12.

- `server.py:262-266`:

```python
def age_minutes(value: Any) -> Optional[int]:
    parsed = parse_iso(value)
    if not parsed:
        return None
    return max(0, round((datetime.now(timezone.utc) - parsed).total_seconds() / 60))
```

- `server.py:3926-3938` (routing head; no try/except anywhere in do_GET):

```python
def do_GET(self) -> None:
    parsed = urllib.parse.urlparse(self.path)
    path = parsed.path
    params = urllib.parse.parse_qs(parsed.query)
    if path == "/api/chargers":
        # Hot path: the default national overview (no bbox) is served from the bytes cache.
        if (params.get("overview") or ["false"])[0].lower() == "true" and not (params.get("bbox") or [""])[0]:
            ...
            return self.send_json_bytes(status, body, gz)
        status, payload = live_response(params)
        return self.send_json(status, payload)
```

- `server.py:2124-2127` (the eviction pattern, repeated for GEOCODE_CACHE at
  2932-2935):

```python
ELECTROMAPS_CACHE[loc_id] = {"ts": now, "detail": detail}
if len(ELECTROMAPS_CACHE) > 2000:
    for k in list(ELECTROMAPS_CACHE.keys())[:400]:
        ELECTROMAPS_CACHE.pop(k, None)
```

- `server.py:3993-3995` (`STATIC_GZ_CACHE`): `if len(...) > 48: STATIC_GZ_CACHE.clear()` then insert.
- `fetch_primary_sources_parallel` (`server.py:3091-3132`) runs gaia/fenie
  (+mapareve on miss) in a `ThreadPoolExecutor`; each fetcher does its own
  cache read/write — so cache mutation already happens on multiple threads
  even for a single request.
- The deep-copy discipline is good (payloads are deep-copied before mutation:
  3183, 3209-3210); the gap is dict-level read-modify-write atomicity, not
  shared-row mutation.
- Existing concurrency test: `tests/test_nearby_backend.py` has a `Barrier`
  test for parallel fetch fan-out only — nothing exercises cache mutation.
- Conventions: stdlib only (`threading` is stdlib — allowed); bug fixes get
  `D-###` ledger rows + run-log/coverage updates (AGENTS.md §6); test pattern
  exemplar: `tests/test_live_coverage.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Offline verify | `./scripts/verify.sh` (or AGENTS §5 1–2) | exit 0 |
| New tests only | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_request_resilience.py -v` | all pass |
| Compile | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -m py_compile server.py` | exit 0 |

## Scope

**In scope**:
- `server.py` — `age_minutes`, a `_route_get` extraction + guard in `Handler`,
  a module-level lock table + minimal `with` blocks around the 7 cache
  read/modify/write sites, bounded-eviction tidy-ups.
- `tests/test_request_resilience.py` (create)
- `qa/defects.csv`, `qa/test-cases.csv`, `qa/coverage-summary.md`, `qa/run-log.md`

**Out of scope** (do NOT touch):
- What the caches STORE or their TTL semantics (plan 007's territory).
- `fetch_*` network logic, budgets, or the ThreadPoolExecutor structure.
- Rate limiting / security headers (plans/005).
- `parse_iso` return-shape for VALID offset-bearing inputs (other callers
  rely on it; coerce in `age_minutes` only — see step 2 rationale).

## Git workflow

- `main`; 2–3 imperative commits (guard / tz coercion / locks). Push per
  AGENTS.md §8 unless the operator said otherwise.

## Steps

### Step 1: Failing tests first

Create `tests/test_request_resilience.py` (standalone unittest, model imports
on `tests/test_live_coverage.py`):

1. `age_minutes("2026-07-01T10:00:00")` (naive) → returns an int, no raise.
   (Currently raises TypeError — run it to confirm before fixing.)
2. Handler 500-guard: build the smallest real-server harness — check whether
   an existing test starts a live `ThreadingHTTPServer`; if none does, use
   `unittest.mock.patch("server.live_response", side_effect=RuntimeError("boom"))`
   and drive `Handler` via `http.server` test idiom: start
   `ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)` in a thread,
   `urllib.request.urlopen(".../api/chargers?...")` → expect HTTP 500 with a
   JSON body `{"error": ...}` and NO exception text/traceback in the body;
   server thread shut down in tearDown.
3. Concurrent cache stress: patch the three fetchers to return canned
   payloads with tiny sleeps; run ~16 threads × ~30 iterations calling
   `server.live_response` with two alternating filter sets (different
   `cache_key`s); assert (a) no exceptions, (b) every returned payload's
   station set matches its requested filter set (catches cross-key bleed).
   Also hammer `server.geocode_search` (patched urlopen) past the 500-entry
   eviction threshold from 8 threads → no RuntimeError.

**Verify**: cases 1 and 3(geocode-eviction) FAIL or error today; case 2 fails
(connection aborted, no 500). Record the actual failure output.

### Step 2: Coerce naive datetimes in `age_minutes`

```python
parsed = parse_iso(value)
if not parsed:
    return None
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
```

Rationale for coercing here (not in `parse_iso`): `parse_iso` feeds
`pricing_window_is_current`-adjacent code and Gaia freshness logic where a
naive value is currently either impossible or locally handled; changing its
return type for all callers is a bigger blast radius than this defect needs.
Grep every `parse_iso(` call site (there are ~4) and confirm none other does
aware-vs-naive arithmetic; if one does, note it in the run-log as follow-up.

**Verify**: test case 1 passes; full suite green.

### Step 3: Top-level guard in the Handler

Extract the body of `do_GET` into `def _route_get(self) -> None:` unchanged,
then:

```python
def do_GET(self) -> None:
    try:
        self._route_get()
    except BrokenPipeError:
        return
    except Exception:
        self.log_message("%s", f"UNHANDLED {traceback.format_exc(limit=8)}")
        try:
            self.send_json(500, {"error": "internal_error",
                                 "warning": "Internal error; nothing is presented as live."})
        except Exception:
            return
```

Notes: `traceback` is already imported (`server.py:22`). The inner try/except
covers the headers-already-sent case. The client body carries NO exception
detail (that stays server-side) — consistent with the fail-closed voice of
existing warnings. Apply the same wrap to `do_HEAD` (3897) with a bare
`self.send_error(500)` fallback.

**Verify**: test case 2 passes (500 + JSON body, no traceback text in body);
`python3 tests/test_live_coverage.py` still green.

### Step 4: Lock the cache read/modify/write sites

Add near the cache definitions:

```python
_CACHE_LOCKS: Dict[str, threading.Lock] = {
    name: threading.Lock()
    for name in ("live", "gaia", "fenie", "electromaps", "geocode", "overview", "static_gz")
}
```

(`import threading` at top.) Then wrap ONLY dict access — never a fetch:

- `live_response`: the `cache_fresh` evaluation + `deepcopy(CACHE["payload"])`
  reads (3175-3193, 3226-3245) and the `CACHE.update(...)` write (3209) each
  get a `with _CACHE_LOCKS["live"]:` block. The upstream fetch between read
  and write stays OUTSIDE the lock — accept the benign "two threads both
  miss and both fetch" race (idempotent, bounded by upstream 60s cadence);
  what the lock removes is torn read/write and key/payload mismatch. Pattern:
  read-and-copy under lock → fetch outside → write under lock.
- `_electromaps_detail` (2115-2127): cached-read under lock; fetch outside;
  insert+evict under lock.
- `geocode_search` (2899-2935): same pattern.
- `GAIA_CACHE` / `FENIE_CACHE`: grep their read/write sites inside
  `fetch_gaia_live` (~1600-1700) and `fetch_fenie_live` (~1952-2030); same
  pattern (read-copy under lock, fetch outside, store under lock).
- `overview_response_bytes` (3062-3068) and the `STATIC_GZ_CACHE` block
  (3988-3996): wrap get and set; for STATIC_GZ keep the `clear()` inside the
  lock. gzip compression may stay outside the lock (CPU-only, idempotent).

Keep each critical section a few lines — if you find yourself wrapping a
network call, stop and restructure.

**Verify**: test case 3 passes repeatedly
(`for i in 1 2 3; do python3 tests/test_request_resilience.py; done` — races
are probabilistic; three green runs required); full suite green;
`python3 -m py_compile server.py` exit 0.

### Step 5: QA ledger

`D-###` rows for: naive-timestamp TypeError, unguarded handler, unlocked
cache eviction races (one row each, severity per ledger vocabulary; evidence
= failing-then-passing test output). `TC-###` rows for the new tests. Update
coverage-summary counts; append run-log iteration with real outputs.

**Verify**: `python3 scripts/qa_artifact_integrity.py` → ok.

## Test plan

Step 1's three cases are the regression suite for this plan. Existing guards
that must stay green: `tests/test_live_coverage.py` (stale-cache semantics —
the lock refactor must not change any branch outcome),
`tests/test_nearby_backend.py` (parallel fetch fan-out).

## Done criteria

- [ ] `python3 tests/test_request_resilience.py -v` → pass, three consecutive runs
- [ ] `python3 -m unittest discover -s tests` → OK (222+)
- [ ] `grep -c "threading.Lock" server.py` → ≥ 1; every eviction loop is inside a `with` block
- [ ] A patched-to-raise `live_response` yields HTTP 500 JSON without exception detail in the body
- [ ] Ledger rows + run-log + coverage-summary updated; artifact integrity ok
- [ ] Only files in Scope changed

## STOP conditions

Stop and report back (do not improvise) if:

- Any existing stale-cache test changes outcome after locking — the locks
  must be behavior-neutral; a flipped branch means a lock is held across
  logic it shouldn't be.
- The stress test deadlocks or slows the suite by >10s — a lock is wrapping
  a fetch; restructure per step 4's pattern, and if that fails, report.
- You cannot get a listening test server to bind (sandboxed env) for case 2 —
  fall back to invoking `Handler` methods with a mocked socket ONLY if an
  existing test already does that; otherwise report the environment limit.
- plans/003 is mid-flight with uncommitted server.py edits.

## Maintenance notes

- Plan 005 (rate limiting) and plan 007 (payload caching) will add their own
  shared state — they must reuse `_CACHE_LOCKS` / the same pattern, not
  invent a second convention.
- The "both threads fetch on simultaneous miss" race is accepted and
  documented here; if upstream rate-limits ever make it costly, add a
  per-key in-flight event (do NOT serialize fetches globally).
- Reviewer focus: no `with` block containing `urllib` calls; `deepcopy`
  inside read-locks kept small (payloads are MBs — copying under lock is the
  cost of consistency; plan 007 shrinks what's stored).
