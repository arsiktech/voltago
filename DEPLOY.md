# Deploying Volta Go — getting a real shareable link

## ⚡ Quick start — run it locally on this Mac
Two ways. Either gives you the full app at **http://127.0.0.1:8765**.

**A) Direct (simplest, best live data — uses your home IP):**
```bash
cd voltago
npm --prefix frontend install        # first time only
npm --prefix frontend run build      # build the UI
PORT=8765 python3 server.py          # open http://127.0.0.1:8765
```
Leave that terminal running. To stop: Ctrl-C. To update the leads DB later, see "Keeping data fresh".

**B) As a container (Apple `container`, already verified building/running):**
```bash
cd voltago
container system start                # first time only
container build -t volta-go .
container run --rm -p 8765:8765 volta-go   # open http://127.0.0.1:8765
```

To make it reachable from your phone / share a link, add a tunnel on top (Option A below):
`brew install cloudflared && cloudflared tunnel --url http://127.0.0.1:8765`.

---

Volta Go is one process: `server.py` (Python stdlib, no pip deps) serves the built React SPA
**and** the JSON API, fetching live data on demand. So "deploy" = run that one process on a
host with a public HTTPS URL.

> **Important — the upstream caveat.** Volta fetches MapaREVE (behind Imperva) and Gaia
> live. From **datacenter IPs** (Fly/Render/most VPS) these can be rate-limited or challenged,
> so live coverage may degrade in the cloud while the intel-DB leads still work. From a
> **residential IP** (your Mac / a Raspberry Pi at home) scraping is most reliable. Pick the
> option that matches how reliable you need the *live* layer to be.

The repo is already deploy-ready: `Dockerfile` (multi-stage build of the SPA + Python runtime),
`.dockerignore`, and `fly.toml`.

---

## Option A — Cloudflare Tunnel from a machine you keep on (most reliable live data) ⭐
Residential IP → sources don't block you. Free public HTTPS URL. Best for personal/family use.

```bash
# 1. Build the SPA and run the server locally
npm --prefix frontend run build
PORT=8765 python3 server.py        # serves http://127.0.0.1:8765

# 2. In another terminal, expose it (install: brew install cloudflared)
cloudflared tunnel --url http://127.0.0.1:8765
# → prints a public https://<random>.trycloudflare.com link (or set up a named tunnel for a stable domain)
```
For an always-on link, run both as services (launchd on macOS, or systemd on a Raspberry Pi) and
use a **named** Cloudflare tunnel mapped to a domain you own.

## Option B — Fly.io (easy public URL, datacenter IP)
```bash
brew install flyctl && fly auth login
fly deploy                           # deploys the existing Fly app configured in fly.toml
# The existing legacy Fly app ID still serves https://volta-vlc.fly.dev.
```
Same with any container host (Render, Railway, a VPS). Build the OCI image with **Apple's native
`container`** (github.com/apple/container) or Docker — the `Dockerfile` works with both:
```bash
# Apple container (macOS):
container system start                          # first run only
container build -t volta-go .
container run --rm -p 8765:8765 volta-go       # → http://localhost:8765
# Docker / Colima equivalent:
docker build -t volta-go . && docker run -p 8765:8765 volta-go
```
The OCI image deliberately defaults `VOLTA_COMMUNITY_WRITES_ENABLED=false`: an ephemeral
container must not accept check-ins/photos that disappear on replacement. To enable writes,
attach durable storage and opt in explicitly, including both storage paths, for example:

```bash
docker volume create volta-go-community
docker run -p 8765:8765 \
  -v volta-go-community:/var/lib/volta \
  -e VOLTA_COMMUNITY_WRITES_ENABLED=true \
  -e VOLTA_COMMUNITY_DB=/var/lib/volta/community.db \
  -e VOLTA_COMMUNITY_PHOTOS=/var/lib/volta/photos \
  volta-go
```

Use the equivalent durable-volume declaration on Render/Railway/another host. Setting the write
gate to `true` without both durable paths is an unsupported, fail-open deployment configuration.
If live coverage looks thin from the cloud, that's the Imperva/IP caveat above — the intel leads
still render; consider Option A for the live layer.

## Option C — keep it local
```bash
npm --prefix frontend run build && PORT=8765 python3 server.py   # http://127.0.0.1:8765
```

---

## Keeping data fresh
- **Live** (MapaREVE / Gaia) refreshes automatically per request (server cache ~60s; the
  client polls ~60s). No cron needed.
- **Feníe** is homepage attribution/link-only. Direct station fetching and Feníe-origin
  station evidence are excluded by policy.
- **Intel DB** (`data/free_intelligence.json`, the leads layer) is a static snapshot. Audit a
  real build without changing the canonical DB or Git state with:
  ```bash
  python3 scripts/auto_refresh.py --dry-run
  ```
  This builds once under ignored `data/cache/auto-refresh/`, requires delta `ok=true` with
  `missing_ids=0`, and makes the canary audit that exact candidate and baseline by SHA-256.
  Its mode-0600 evidence is `data/cache/auto-refresh/dry-run-last.json`; it never replaces
  the last real-run receipt, fetches Git, or changes local/remote refs.

## Unattended operation

The recommended topology is an always-on home/residential host: one dedicated clean clone on
`main`, the Volta server supervised locally, weekly `auto_refresh.py`, a 30-minute health probe,
and the Cloudflare Tunnel from Option A. Fly/container hosting remains a useful fallback, with
the datacenter-IP live-source caveat above.

Community mutations require durable SQLite and photo storage. They are enabled by default for
the persistent home deployment. Set `VOLTA_COMMUNITY_WRITES_ENABLED=false` on any ephemeral
container deployment; the checked-in Fly configuration does this and every community POST then
fails closed with `503 community_writes_disabled` while read-only evidence remains available.

Do not run the real refresher from a working development/Syncthing checkout. Prepare a dedicated
clone, build the SPA if it will also serve traffic, and add the ignored marker:

```bash
git clone https://github.com/arsiktech/voltago.git /PATH/TO/VOLTA-DEDICATED-CHECKOUT
cd /PATH/TO/VOLTA-DEDICATED-CHECKOUT
touch .volta-auto-refresh-dedicated
npm --prefix frontend ci && npm --prefix frontend run build
python3 scripts/auto_refresh.py --dry-run
```

A real run refuses anything except that marker, exact Git root, `main`, and a completely
clean checkout. Before capturing its transaction HEAD it fetches `origin/main`. An up-to-date
checkout proceeds; a behind checkout advances only with `--ff-only`, then reexecutes the
fetched `scripts/auto_refresh.py` while retaining the same process lock so old orchestration
cannot run against newly fetched gates. A dirty-after-fetch race, fetch failure, divergent
history, or remote rewind fails closed and notifies without building. It then rebuilds once,
gates the exact DB/history bytes, and revalidates baseline artifacts and Git state. It then
starts the four-artifact rollback
transaction by installing the gated DB/history, generates both current reports from that
promoted canonical DB, replaces the reports, and revalidates/stages only the following set.
Every pre-commit failure restores all four files with fsync-backed rollback:

- `data/free_intelligence.json`
- `qa/ingestion-canary-history.jsonl`
- `qa/free-db-completeness-latest.json`
- `qa/valencia-source-audit-latest.json`

It verifies working-tree, index, commit-parent, changed-path, and committed-blob identities
before treating the local `data: weekly intelligence refresh (...)` commit as trusted. Any
pre-commit escape restores the complete artifact set; verified commits remove their temporary
rollback snapshots even when publication fails. Push is deliberately opt-in:

```bash
python3 scripts/auto_refresh.py                 # local commit only
VOLTA_REFRESH_PUSH=true python3 scripts/auto_refresh.py  # push origin/main, never force
```

If push fails, the verified local commit is preserved and bound to the separate mode-0600
`data/cache/auto-refresh/pending-push.json`. A later `--push` run accepts exactly that one
local-ahead commit only after rechecking its parent, allowlisted paths, and artifact hashes;
it publishes the pinned commit before starting another build. Arbitrary local-only commits
remain a hard sync failure, and a no-push or repeatedly failed retry cannot overwrite the
pending proof. Successful publication removes it. Every real attempt writes mode-0600
`data/cache/auto-refresh/last.json`; the receipt records candidate/baseline/canonical hashes,
sync/gate results, rollback paths, commit, and push outcome. Overlapping schedules exit 75 without
overwriting the active run's receipt. `scripts/refresh_intelligence.sh` is only a compatibility
shim to this same orchestrator.

Set `VOLTA_NOTIFY_CMD` to an external command prefix if failures should be delivered elsewhere.
It is parsed as argv (no shell), and one short failure message is appended; keep credentials in
the host's protected environment, never in this repo. `VOLTA_HEALTH_BASE_URL` changes the health
probe origin. The probe requests exactly `/api/health` without redirects and independently checks
the local DB's timestamp/count/errors plus, when `--receipt` is supplied, a completed real-run
receipt whose canonical hash matches.

Disabled, placeholder-based templates are in `deploy/launchd/` and `deploy/systemd/`. Replace
`/PATH/TO/VOLTA-DEDICATED-CHECKOUT`, inspect the environment/log destinations, run one real refresh
so the receipt exists, and lint before activation:

```bash
# macOS preparation (Disabled=true is overridden only by the explicit enable records below)
plutil -lint deploy/launchd/*.plist
cp deploy/launchd/com.volta.{refresh,health}.plist ~/Library/LaunchAgents/
# Explicit enable switch:
launchctl enable gui/$(id -u)/com.volta.refresh
launchctl enable gui/$(id -u)/com.volta.health
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.volta.refresh.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.volta.health.plist
# Prove the loaded targets, then exercise each once and inspect its receipt/logs:
launchctl print gui/$(id -u)/com.volta.refresh
launchctl print gui/$(id -u)/com.volta.health
launchctl kickstart -k gui/$(id -u)/com.volta.refresh
launchctl kickstart -k gui/$(id -u)/com.volta.health

# Linux user units
mkdir -p ~/.config/systemd/user
cp deploy/systemd/volta-* ~/.config/systemd/user/
systemctl --user daemon-reload
# Explicit enable switch:
systemctl --user enable --now volta-server.service volta-refresh.timer volta-health.timer
```

The repository ships no installed job and does not run any of these enable commands automatically.

## Tunable env vars
`PORT`, `HOST`, `VOLTA_CACHE_TTL`, `VOLTA_MAPAREVE_PRICE_QUERY_MAX` (default 0.30),
`VOLTA_MAPAREVE_MAX_PAGES`, `VOLTA_MAPAREVE_PAGE_CONCURRENCY` (default 4 pages fetched at once after page 1), `VOLTA_GAIA_JUMP_URLS` (comma-separated Gaia
jump URLs to live-parse), `VOLTA_REFRESH_PUSH` (default false), `VOLTA_NOTIFY_CMD`, and
`VOLTA_HEALTH_BASE_URL`. See `AGENTS.md` for the full runtime list.

## Gated runtime data refresh

`scripts/runtime_refresh.py --state-dir /ABSOLUTE/RUNTIME/STATE` builds charger data without changing Git, pushing commits or restarting the server. Keep this state directory outside the checkout. The command reuses the existing delta, exact-candidate canary and descriptive-report generators. It requires `ok=true`, zero missing IDs, unchanged source/baseline bytes and matching candidate hashes. A failed candidate remains under `runs/` and never replaces the previous runtime data.

A successful run switches one `current` symlink containing the dataset, canary history and both reports. Previous bundles remain available for rollback. Set `VOLTA_INTELLIGENCE_DB=/ABSOLUTE/RUNTIME/STATE/current/free_intelligence.json` on the web service. The server resolves each bundle and invalidates its caches by resolved path and modification time. A code update cannot reset this runtime dataset. Before enabling the override, run `python3 scripts/runtime_refresh.py --state-dir /ABSOLUTE/RUNTIME/STATE --initialize`. It copies a complete bundle from the existing deployed data and reports, preserving the dataset bytes and original generated date, and refuses to overwrite an existing runtime bundle. Never claim initialization is a fresh collection.

The templates in `deploy/systemd/volta-data-refresh.{service,timer}` run separately from the older code-update timer. Their host-local environment file is `~/.config/volta/data-refresh.env`, containing `VOLTA_REFRESH_STATE_DIR` and an explicitly verified `VOLTA_ALERT_TARGET`. The installed templates run every six hours, with up to five minutes of jitter, a 30-minute total deadline and resource limits. The application cache/collection policy still determines individual source cadence.

The stop hook records failures, including timeout exits, and uses the host's existing Hermes CLI to send one short alert after three consecutive failed runs. A success clears the failure streak silently. It never retries an ambiguous delivery automatically. Test the alert branch with a mocked sender before arming; a configured target is not proof of delivered notification.

Verify the first real service run, exact public `/api/intel` recordset hash, preserved community storage, a finite next timer firing, and negative source-policy tests. Enabled scheduling is not proof that every upstream answered. Keep failed-source warnings and held candidates visible; do not relax delta or canary gates merely to publish a new timestamp.

Runtime retention keeps the 12 newest completed runs plus the active bundle and one previous successful rollback bundle. Only recognizably owned completed-run directories are pruned; symlinks, unreceipted runs and unknown files are retained. Fetch caches remain under the checkout’s ignored `data/cache`, not tracked data. Runtime receipts include collector errors and cache/fallback totals. Feníe uses a 24-hour fetch cache and refuses a failed-refresh fallback older than seven days.

Before the unchanged delta/canary gates, runtime collection preserves an existing application ID only when a unique stable upstream station identifier matches in both datasets within 250 metres. Exact OSM nodes, official station pages, source-issued IDs and Lidl official address queries can establish identity; names or proximity alone cannot. Conflicting matches stay unresolved and block publication. Fresh coordinates, prices, status and provenance are retained, with an identity-reconciliation report. No old station or fact is recreated to satisfy the zero-missing-ID gate.
