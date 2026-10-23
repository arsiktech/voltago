# Module: Deploy And Runtime

Volta Go deploys as one Python process serving both API and built SPA. The most
reliable live-data deployment is local/residential IP plus a tunnel; cloud
container hosting is easier but can degrade upstream scraping.

## Responsibilities

- Build the React SPA.
- Run the Python stdlib server.
- Package a multi-stage Docker/container image.
- Provide Fly.io config for a Madrid-region public deployment.
- Document local tunnel and weekly intelligence-refresh options.

## Key Files

- [DEPLOY.md](../../../DEPLOY.md) - deployment guide.
- [Dockerfile](../../../Dockerfile) - multi-stage frontend build and Python
  runtime.
- [fly.toml](../../../fly.toml) - Fly.io app config.
- [.dockerignore](../../../.dockerignore) - excludes dev/docs/QA/cache artifacts
  from container image.
- [README.md](../../../README.md#run-locally) - frontend and production-style
  local server commands.
- [frontend/vite.config.ts](../../../frontend/vite.config.ts) - dev proxy and
  build chunking.

## Public API

Runtime commands:

```bash
npm --prefix frontend run build
PORT=8773 PYTHONPYCACHEPREFIX=/tmp/volta-pycache python3 -u server.py
```

Container:

```bash
container build -t volta-go .
container run --rm -p 8765:8765 volta-go
```

Fly:

```bash
fly launch --copy-config --now
fly deploy
```

## Internal Structure

The Dockerfile builds the SPA with Node, then copies `server.py`, the source-policy
module and data, and built `frontend/dist` into a Python 3.12 slim runtime. The
runtime uses no pip dependencies. `.dockerignore` excludes local QA,
documentation, tests, caches, probes, and node modules.

## Dependencies

- **Uses:** Node for build stage, Python runtime, optional Cloudflare Tunnel,
  optional Fly.io.
- **Used by:** local browser app, iOS client backend, shareable deployment.

## Notable Patterns / Gotchas

- Datacenter IPs may get challenged/rate-limited by live sources; local tunnel
  from the Mac is often better for live coverage.
- Bind `HOST=0.0.0.0` for phone/device access; loopback only works from the same
  machine or simulator.
- The weekly refresh script can commit and push data changes; do not run that as
  an accidental side effect during ordinary code work.
