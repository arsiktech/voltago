# Plan 013 — Free-first ecosystem: verified free registry + community layer

> **Dated work reference.** Reconcile this plan with current code and evidence.
> Use [current engineering contracts](../docs/backend-engine.md) and the active
> task scope. Historical execution sequences and stop-on-drift instructions do
> not block independent current work. Old guide references below are provenance.

Priority: P1 (owner directive 2026-07-17) · Effort: L (split into independent slices)
Depends on: 001 (verify), 005 (public hardening — rate-limiter/token conventions), 010 (refresh lifecycle)

## Owner directive (2026-07-17, verbatim intent)

Focus the product on a **database of FREE, verified chargers** ("база ТОЛЬКО
БЕСПЛАТНЫХ проверенных зарядок"), Spain first, kept current automatically.
Track the networks a real Tesla driver uses to find free charging
(LetMeCharge, Electromaps, EuroCharger, Fenie Recarga, MELIB, Iberdrola,
Acciona Recarga VE, GIC) via every path that exists — direct where public,
indirect (official open data / national access point) where not. Turn the app
into a living ecosystem: photos of chargers, user check-ins ("did charging
actually work?"), user-suggested new points and descriptions.

## Non-negotiables inherited

- All AGENTS.md §2 Prime Directives, especially: **public/GET-only sources**
  (apps without a public surface stay attributed/linked, never scraped) and
  **data honesty** (community evidence is its own tier; it NEVER becomes live
  authority; corroboration raises existence/price confidence only).
- Zero third-party Python deps. `sqlite3` is stdlib and allowed.
- Fail closed: community evidence may *downgrade or annotate* a decision,
  never upgrade one to "Go now".
- Source-rights posture from `docs/source-research-2026-07-15-national.md`
  stands (Electromaps LINK-ONLY, Chargemap SKIP, Acciona platform no-ingest,
  …) unless a NEW dated review for a NEW surface says otherwise.

## Slice A — Free registry lens (server.py, no new data path)

1. `free_only=true` on `/api/chargers`: after enrichment, keep only rows whose
   charge claim is genuinely free (`is_free` / `price_min == 0` with free
   evidence; a missing price is unknown, never free).
2. New `GET /api/free-registry`: the whole-Spain free set (small; subset of the
   canonical 650) + `stats` (counts by verification level / by source family /
   community totals) + optional `bbox` narrowing. In-memory 60 s byte cache,
   gzip, rate-limited like `/api/chargers`.
3. `free_verification` ladder attached per free row (presentation-honest,
   computed from existing blocks + community store):
   - `live` — a live source shows the station now (mirrors `verification.live_verified`)
   - `community` — successful check-in within `VOLTA_COMMUNITY_FRESH_DAYS` (default 60)
   - `corroborated` — ≥2 independent source families agree
   - `reported` — single public source
   Invariant: this ladder never mutates `verification.live_verified`.

## Slice B — Community evidence store (`community.py`, new module)

SQLite at `data/community.db` (WAL, schema-versioned via `user_version`),
photos on disk `data/community/photos/{id}.{ext}` — both **gitignored** (user
content never enters git; Syncthing carries it between owner machines).

Tables: `checkins(station_id, verdict∈{charged,failed,occupied,offline,blocked},
note≤280, created_at, client)`, `photos(station_id, ext, size, status, created_at,
client)`, `suggestions(name, lat, lon, note, connectors, status∈{pending,accepted,rejected},
created_at, client)`, `meta(key, value)` (stable random salt minted on first boot).
`client` = salted SHA-256 of the client IP — never the raw IP (log-minimization ethos).

Endpoints (JSON; writes get a tight rate bucket + per-client daily caps;
if `VOLTA_COMMUNITY_TOKEN` is set, writes additionally require header
`X-Volta-Community`):

- `POST /api/community/checkin` `{station_id, verdict, note?}`
- `POST /api/community/photo?station_id=…` — raw `image/*` body (magic-byte
  validated JPEG/PNG/WebP, ≤ `VOLTA_PHOTO_MAX_BYTES`, default 4 MB)
- `GET  /api/community/photo/{id}` — serves the file, immutable cache headers
- `POST /api/community/station` `{name, lat, lon, note?, connectors?}` —
  suggestion lands as `pending`; it is visible in the registry's community
  section but is NOT merged into the map until moderated
- `GET  /api/community/summary?ids=a,b,c`
- `POST /api/community/moderate` — only when `VOLTA_ADMIN_TOKEN` is set
  (accept/reject suggestions, remove photos/check-ins)

Enrichment: stations gain a `community` block `{checkins:{…counts, last_success_at,
last_failure_at}, photos:[urls], recent_success, recent_failure}`. Decision
adjustment is **fail-closed only**: net-negative recent evidence downgrades a
"go" to "check" with an explicit reason; success never upgrades anything.
Accepted suggestions surface as community-tier leads ("Community lead" —
existing verification level) in `/api/free-registry` only.

## Slice C — Source expansion for free+live (research-gated)

For each of the 8 owner-named apps plus Spain's official routes, record a dated
verdict (public surface? rights? live status? free representation?) in
`docs/source-research-2026-07-17-free-first.md`, then implement ONLY the
rights-clean paths as adapters behind the existing non-destructive ingest +
delta/canary gates. Expected shape (verify before building):

- **DGT National Access Point** (AFIR duty: operators MUST publish static +
  dynamic charge-point data) — the lawful "обходной путь" to national live
  status for the big CPOs (Iberdrola, Acciona, Fenie/…, Endesa X…). Access
  mechanics to verify (registration, format, cadence) before an adapter.
- Fenie Recarga / MELIB / GIC / Iberdrola public maps — per-app verdicts.
- LetMeCharge / EuroCharger — no public surface per existing probes → stay
  ATTRIBUTE/LINK-ONLY; Electromaps stays LINK-ONLY absent written permission.
- Every app keeps a first-class **handoff link** (deep link/app URL) on rows
  where it is the operator — attribution instead of scraping.

## STOP conditions

- Any source implementation that would require auth, tokens, POST-only bulk
  endpoints, or ToS-prohibited extraction → record as LINK-ONLY / OWNER-LEGAL-
  DECISION instead of implementing.
- Community moderation actions beyond the owner-token path (no public deletes).
- Do not commit `data/community.db`, photo files, or any file that already
  carries unrelated uncommitted changes from the in-flight TestFlight session.

## Verification contract

- New stdlib tests: `tests/test_community.py` (store + validation + caps +
  moderation), `tests/test_free_registry.py` (lens filter, ladder honesty
  invariants, endpoint round-trip on an ephemeral server).
- `./scripts/verify.sh` stays green; browser tier unaffected (additive API).
- QA ledger: new F-row (community ecosystem), TC rows for check-in/photo/
  suggestion/free-registry; run-log entry with real command output.
