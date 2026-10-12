# Plan 005: Harden the server and image for unauthenticated public exposure

> **Historical completed plan.** Execution steps and STOP rules below are retired.
> Use [AGENTS.md](../AGENTS.md) and current code for new work; retain the product
> constraints and dated evidence recorded here. See [the index](README.md).

## Status

- **Priority**: P1 (before any further public exposure), P2 if the app stays LAN/tunnel-private
- **Effort**: M–L
- **Risk**: MED (rate limiting can throttle legitimate map panning if mistuned; CSP can break the map — both have explicit fallbacks below)
- **Depends on**: plans/004 (reuses its `_CACHE_LOCKS` locking convention for rate-limiter state)
- **Category**: security
- **Planned at**: commit `ceee003`, 2026-07-12

## Why this matters

The repo is deploy-ready for the public internet (Dockerfile binds
`HOST=0.0.0.0`, fly.toml, DEPLOY.md's tunnel path) with **no auth layer**, and
the security audit (2026-07-12) confirmed: one anonymous request can fan out
to ~12 upstream fetches with `force=true`/bbox/max_price permutations
defeating the single-slot cache entirely (abuse against MapaREVE/Gaia/Feníe
that can get the deploy IP banned — the exact risk DEPLOY.md warns about —
plus self-DoS on a 256 MB VM); `/api/geocode` is an open Nominatim relay with
a policy-noncompliant User-Agent and wildcard CORS; responses carry raw
upstream exception text; logs record IP + full query (search terms, exact
viewport) with no minimization; and the Docker image has two latent build
defects (unguarded `ZoneInfo("Europe/Madrid")` on a tzdata-less slim base can
crash-loop boot; `npm ci 2>/dev/null || npm install` silently abandons the
lockfile). Positives already verified and NOT in scope: path traversal is
correctly defended, outbound fetches are allowlisted/constant-host, no
secrets in the repo, npm audit clean.

## Current state

Verified on disk 2026-07-12.

- `server.py:3164` — `force = (params.get("force") or ["false"])[0].lower() == "true"`;
  `:3175-3180` — `cache_fresh = not force and …`; the live cache is ONE slot
  (`CACHE` dict with a single `key`/`payload`, write at `:3209`).
- `server.py:3161` — `max_price` is an unclamped float straight into
  `cache_key` (`:3172`).
- `server.py:2883-2886`:

```python
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOCODE_CACHE: Dict[str, Any] = {}
GEOCODE_TTL_SECONDS = int(os.environ.get("VOLTA_GEOCODE_TTL", "86400"))
GEOCODE_UA = "Volta EV-charging app (public geocode; contact via app owner)"
```

  (Nominatim's usage policy requires a way to actually contact the operator.)
- `server.py:3876-3891` (`send_json_bytes`) sets only Content-Type /
  Cache-Control / `Access-Control-Allow-Origin: *` / Vary (+Content-Encoding).
  `serve_static` (`:3998-4008`) similarly minimal. No CSP,
  X-Content-Type-Options, X-Frame-Options, or Referrer-Policy anywhere.
- `server.py:3225` — `err = f"{type(mapareve_error).__name__}: {mapareve_error}"`
  flows into client-visible `payload["warning"]` (also 3229, 3233-3236);
  `fetch_mapareve` returns raw per-page exception strings in
  `payload["errors"][:5]` (2715, 2740-2742).
- `server.py:3866-3867`:

```python
def log_message(self, fmt: str, *args: Any) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")
```

  (time-only, no date/TZ; `fmt % args` includes the full request line with
  query string → geocode search terms + exact bbox coordinates land in logs.)
- `server.py:30-33` guards only the zoneinfo IMPORT; `:120` calls
  `ZoneInfo("Europe/Madrid")` unguarded at module load. `Dockerfile:11` is
  `FROM python:3.12-slim` with no tzdata install.
- `Dockerfile:6` — `RUN npm ci 2>/dev/null || npm install`. `Dockerfile:3` —
  `FROM node:22-slim` (floating). `frontend/package.json` has no `engines`
  field (toolchain floor is node ^20.19 || >=22.12 per installed vite/oxlint).
- `main()` (`server.py:4015-4021`): `HOST` default `127.0.0.1`; the container
  sets `HOST=0.0.0.0` (`Dockerfile:17`).
- Conventions: stdlib only (`threading`, `time`, `ipaddress` are stdlib — ok);
  env knobs are `VOLTA_*` with sane defaults; warnings speak in the
  fail-closed voice ("nothing is presented as live"); AGENTS.md §8 lists env
  vars — new knobs must be added there; bug fixes get `D-###` ledger rows.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Offline verify | `./scripts/verify.sh` (or AGENTS §5 1–2) | exit 0 |
| New tests | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 tests/test_public_hardening.py -v` | all pass |
| Import smoke (tz guard) | `PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -c "import server; print(server.MADRID_TZ)"` | prints a tz, exit 0 |
| Container build (only if a builder exists) | `docker build -t volta-test .` or `container build …` | exit 0 |

## Scope

**In scope**:
- `server.py` — rate limiter + upstream-concurrency semaphore, `max_price`
  clamp/snap, geocode UA env + geocode rate limit, response headers, warning
  sanitization, `log_message` redaction/ISO timestamps, ZoneInfo guard.
- `Dockerfile` — tzdata, `npm ci`, (optional) pinned base tags.
- `frontend/package.json` — `engines` field only.
- `AGENTS.md` §8 env-var list — document the new `VOLTA_*` knobs.
- `tests/test_public_hardening.py` (create); ledger files.

**Out of scope** (do NOT touch):
- Authentication of any kind (product decision — this app is deliberately keyless).
- CORS lockdown beyond what step 4 says (the iOS app and local dev use
  non-browser or same-origin paths, but QA scripts may fetch cross-origin —
  verify before narrowing; see step 4).
- The fetch budget constants and cache TTLs.
- fly.toml (deploy config is the owner's).

## Git workflow

- `main`; logical commits per concern (rate limit / headers+warnings /
  logging / Docker). Push per AGENTS.md §8 unless told otherwise.

## Steps

### Step 1: Tests first (what can be tested offline)

`tests/test_public_hardening.py` (standalone unittest; live-server harness as
in plans/004 step 1, or mocked-socket Handler if that precedent exists):

1. Rate limit: with `VOLTA_RATE_LIMIT_RPM=5` (env or monkeypatched constant),
   the 6th `/api/chargers` request from one client inside a minute → HTTP 429
   with a JSON body and `Retry-After` header; localhost exemption per step 2
   design (then use a fake peer address via mock to trigger).
2. `max_price` clamp: request `max_price=abc` → default; `max_price=99` →
   clamped to 5.0; `max_price=0.27` → snapped to 2 decimals (0.27) — assert
   via the `meta.engine.viewport.cache_key` echo in the response.
3. Headers: any `/api/*` response carries `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`; the `/` HTML
   response carries the CSP chosen in step 4 (or none if step 4's fallback
   triggered — keep the assertion conditional on a module constant so the
   test documents reality).
4. Warning sanitization: patched `fetch_mapareve` raising
   `RuntimeError("secret-internal-detail")` → response warning contains
   `RuntimeError` (class name allowed) but NOT `secret-internal-detail`.
5. ZoneInfo guard: `python3 -c` subprocess with a monkeypatch is overkill —
   instead unit-test the new `_load_madrid_tz()` helper (step 6) by calling
   it with a patched `ZoneInfo` that raises; expect `timezone.utc` fallback.

**Verify**: tests fail today (429/headers/sanitization don't exist), pass after steps 2–6.

### Step 2: Token-bucket rate limiter + upstream semaphore

Stdlib only, reusing plans/004's lock convention:

- Module state: `RATE_BUCKETS: Dict[str, Tuple[float, float]]` (client → (tokens, last_ts))
  guarded by a dedicated lock; refill `VOLTA_RATE_LIMIT_RPM` (default 60) per
  minute, burst = RPM. Client identity: `self.client_address[0]`; honor
  `X-Forwarded-For`'s first hop ONLY when `VOLTA_TRUST_PROXY=true` (document:
  set it behind the Cloudflare tunnel / Fly proxy, never on a raw bind).
  Exempt loopback peers (`127.0.0.1`, `::1`) so local dev/QA is never throttled.
- Enforce in `_route_get` (the plans/004 extraction; if 004 hasn't landed,
  create the extraction here the same way) for `/api/chargers` and
  `/api/geocode` only: over-limit → `send_json(429, {...})` + `Retry-After`.
- Global upstream-fetch semaphore: `threading.BoundedSemaphore(int(os.environ.get("VOLTA_MAX_CONCURRENT_LIVE", "4")))`
  acquired (non-blockingly, ~2s timeout) around the
  `fetch_primary_sources_parallel` call in `live_response` (`:3186/:3195`) —
  on timeout, fall through to the existing stale-cache/fail-closed branches
  exactly as if the fetch failed (reuse that code path — do NOT invent a new
  error shape; the fail-closed machinery at 3223-3258 is the designed net).
- `force=true` (`:3164`): honor only for loopback/trusted-proxy-internal
  callers; for everyone else treat as false (comment why). Grep first:
  `grep -rn "force" frontend/src scripts/qa_*.py` — the SPA sends `force`
  from a user action and QA scripts may too; loopback exemption keeps QA
  working, and the SPA's public-deploy refresh degrades to the 60s TTL, which
  is the intended honest cadence.
- Bound the bucket dict (evict oldest beyond ~10k entries, under its lock).
- Clamp+snap `max_price` at parse (`:3161`): `min(max(value, 0.0), 5.0)`
  rounded to 2 decimals, so query-string permutations cannot mint unlimited
  cache keys.

**Verify**: step-1 tests 1–2 pass; `python3 tests/test_live_coverage.py` green
(the semaphore fallback must not change any stale-path outcome when
uncontended); run-log notes the chosen defaults.

### Step 3: Geocode policy compliance

- `GEOCODE_UA` (`:2886`): build from env —
  `os.environ.get("VOLTA_CONTACT", "")` appended when set, e.g.
  `Volta EV-charging app (+{contact})`. Do NOT invent a contact value; add
  `VOLTA_CONTACT` to AGENTS.md §8 with a note that Nominatim policy expects a
  real contact for public deployments (owner fills it at deploy time).
- Geocode-specific tighter bucket (default 10/min per client — city-jump is a
  human-paced action) via the same limiter with a per-route override.

**Verify**: `grep -n "VOLTA_CONTACT" server.py AGENTS.md` → both hit; geocode
429 test passes.

### Step 4: Response hardening headers

In `send_json_bytes` and `serve_static` add unconditionally:
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`X-Frame-Options: DENY`. For the HTML document response only (serve_static
when the resolved file is `index.html`), attempt a CSP:

```
default-src 'self'; img-src 'self' data: https:; connect-src 'self' https:;
style-src 'self' 'unsafe-inline'; script-src 'self'; worker-src 'self' blob:;
font-src 'self' data:; frame-ancestors 'none'
```

Then VERIFY it against the real app: build the SPA, run the server, load the
map view in a browser (or run
`node scripts/qa_cdn_blocked_ui.js` / `scripts/qa_mobile_ui.js` if the local
Chrome stack works) and check the console for CSP violations — MapLibre needs
`blob:` workers and tile hosts under `https:`; the fonts are self-hosted
(@fontsource). **If violations appear that you cannot resolve by adjusting
the directive list in two attempts, ship WITHOUT the CSP header** (keep
nosniff/XFO/Referrer-Policy) and record the exact violation text in the
run-log — partial hardening beats a broken map.

CORS: leave `Access-Control-Allow-Origin: *` UNLESS
`grep -rn "fetch(\|XMLHttpRequest" scripts/qa_*.js frontend/src` shows no
cross-origin consumer AND the owner's deploy notes don't rely on it; if you
do narrow it, gate by env (`VOLTA_CORS_ORIGIN`, default `*`) so nothing
breaks silently. (The relay-abuse concern is already mitigated by the rate
limiter; CORS narrowing is defense-in-depth, not the fix.)

**Verify**: step-1 test 3 passes; if CSP shipped, a manual/browser check of
the map view shows tiles + pins rendering and zero CSP console errors.

### Step 5: Sanitize client-visible upstream errors + logs

- Warnings: wherever exception text is interpolated into client payloads
  (grep `mapareve_error`, `payload["errors"]`, `f"{type(` in server.py —
  sites 3225/3229/3233/2715/2741), keep the exception CLASS name, drop the
  message: `err = type(mapareve_error).__name__`. Log the full string
  server-side at the same site via `print` in the existing log style. Check
  `tests/test_live_coverage.py` assertions on warning text first — they
  assert prefixes/flags, but confirm, and update only assertions that pinned
  the raw message.
- `log_message` (`:3866-3867`): ISO-8601 UTC timestamp
  (`datetime.now(timezone.utc).isoformat(timespec="seconds")`); for paths
  starting `/api/geocode` or `/api/chargers`, log the PATH only (strip the
  query string). Keep client IP (the rate limiter makes it operationally
  needed) — note in AGENTS.md §8 that public deploy logs contain IPs but not
  queries/coordinates.

**Verify**: step-1 test 4 passes; manually: run the server, hit
`/api/geocode?q=TESTQUERY`, confirm stdout shows the path without TESTQUERY.

### Step 6: Deploy-image correctness

- `server.py:120` → extract a guarded helper:

```python
def _load_madrid_tz():
    if not ZoneInfo:
        return timezone.utc
    try:
        return ZoneInfo("Europe/Madrid")
    except Exception:  # tzdata missing on slim images
        return timezone.utc
MADRID_TZ = _load_madrid_tz()
```

- `Dockerfile`: stage 2 add
  `RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*`
  (belt and suspenders with the guard: guard prevents crash-loop; tzdata
  keeps Madrid-local pricing windows CORRECT — UTC fallback would shift
  overnight-window boundaries by 1–2h, so the fallback alone is not enough
  for production) plus a build-time smoke line after COPY:
  `RUN python -c "import server"` — wait, COPY happens at image build where
  data files exist; ensure the import does not start the server (it doesn't —
  `main()` is under `__main__`) and tolerates the data files present. If the
  import pulls >2s or fails in the builder, drop the smoke line and note it.
- `Dockerfile:6` → `RUN npm ci` (delete the silent fallback).
- `frontend/package.json`: add `"engines": { "node": ">=22.12" }`.
- Optional (do if trivial): pin fuller base tags (`node:22-slim` →
  `node:22.<current>-slim`, `python:3.12-slim` → `python:3.12.<current>-slim`)
  — pick the versions from `docker pull`-resolved digests only if a builder
  is available; otherwise leave floating and note it.

**Verify**: `python3 -c "import server; print(server.MADRID_TZ)"` →
`Europe/Madrid`; step-1 test 5 passes; if a container builder exists:
image builds green and `container run`/`docker run` boots + `/api/health`
returns ok. If no builder: say so explicitly in the run-log — do not claim
the image was verified.

### Step 7: Ledger + docs

`D-###` rows (tzdata latent boot crash; lockfile-bypassing Dockerfile;
unsanitized upstream errors — the rate limiter is a hardening feature, not a
defect fix: give it a `TC-###`/feature note instead, matching how
features.csv records capabilities). Document every new env var
(`VOLTA_RATE_LIMIT_RPM`, `VOLTA_MAX_CONCURRENT_LIVE`, `VOLTA_TRUST_PROXY`,
`VOLTA_CONTACT`, `VOLTA_CORS_ORIGIN` if added) in AGENTS.md §8. Run-log +
coverage-summary updates; artifact integrity check.

**Verify**: `python3 scripts/qa_artifact_integrity.py` → ok;
`grep -c "VOLTA_RATE_LIMIT_RPM" AGENTS.md` → ≥1.

## Test plan

Step 1's five cases + the existing reliability suite
(`test_live_coverage.py` must stay green through steps 2 and 5 — it guards
the fail-closed branches the semaphore reuses). Browser check for CSP per
step 4 (honest skip-and-record if the Chrome stack is unavailable — per
AGENTS.md §5, never claim the browser tier ran when it didn't).

## Done criteria

- [ ] `python3 tests/test_public_hardening.py -v` → all pass
- [ ] Full offline verify green
- [ ] 6th rapid request from a non-loopback client → 429 (test-proven)
- [ ] `/api/*` responses carry nosniff/XFO/Referrer-Policy (curl -i shows them)
- [ ] Client-visible warnings contain exception class names only (no messages)
- [ ] Server log lines: ISO UTC date-time, no query strings on geocode/chargers
- [ ] `python3 -c "import server"` succeeds; Dockerfile has tzdata + bare `npm ci`
- [ ] `engines` in frontend/package.json; new env vars documented in AGENTS.md §8
- [ ] Ledger updated; only in-scope files changed

## STOP conditions

Stop and report back (do not improvise) if:

- Any `tests/test_live_coverage.py` case changes outcome — the semaphore/
  sanitization must not alter fail-closed semantics.
- The SPA's own request pattern (map pan/zoom bursts — count requests in
  `frontend/src/App.tsx`'s bbox-change handling before tuning) exceeds the
  default bucket in normal use; report the observed request rate instead of
  silently raising the limit.
- CSP cannot be made compatible within two directive adjustments (ship the
  fallback, record it — that IS an allowed outcome, not a failure).
- QA browser scripts turn out to fetch the API cross-origin and a CORS change
  broke them (revert the CORS step, keep the rest).

## Maintenance notes

- The rate limiter is per-process memory; if the app ever runs multi-instance
  (Fly scale >1), limits multiply per instance — fine for abuse-damping, not
  a strict quota. Note left here deliberately.
- If the owner later fills `VOLTA_CONTACT`, no code change is needed.
- Reviewer focus: no lock held across `urllib` calls; 429 body shape matches
  the app's JSON error voice; the force-param exemption logic
  (loopback/trusted) is easy to get backwards — read it twice.
- Follow-up explicitly deferred: ETag/304 for `index.html` and byte-cache for
  static reads (audit finding PERF-07, low impact — see plans/README.md).
