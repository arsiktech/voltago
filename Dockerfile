# Volta Go — single-image deploy: build the React SPA, serve it + the API from Python stdlib.
# Stage 1: build the frontend
FROM node:26.10.0-trixie-slim AS web
WORKDIR /web
RUN npm install --global npm@12.2.0
COPY frontend/package.json frontend/package-lock.json* ./
# Bare `npm ci` (was `npm ci 2>/dev/null || npm install`, which silently abandoned the
# lockfile -- and its exact-version guarantees -- on ANY npm ci failure, masking real
# lockfile drift as if it were a missing-lockfile first run). Fail the build instead.
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: runtime (Python stdlib only — no pip deps)
FROM python:3.14.8-slim-trixie
WORKDIR /app
# Install the IANA timezone database explicitly. server.py's _load_madrid_tz() guard
# prevents a crash-loop on that (falls back to UTC), but UTC would silently shift
# overnight/off-peak pricing-window boundaries by 1-2h -- the guard is a safety net,
# this install is the real fix for correct Madrid-local pricing windows in production.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY server.py agent_mcp.py community.py station_truth.py google_maps.py vehicles.py ./
COPY scripts/global_source_catalog.py ./scripts/global_source_catalog.py
COPY scripts/source_policy.py ./scripts/source_policy.py
COPY scripts/json_recordset.py ./scripts/json_recordset.py
COPY scripts/intelligence_index.py ./scripts/intelligence_index.py
COPY scripts/source_transport.py ./scripts/source_transport.py
COPY scripts/public_live_status.py ./scripts/public_live_status.py
COPY data/free_intelligence.json data/seed_leads.json data/source_policy_exclusions.json data/google_maps_links.json ./data/
COPY --from=web /web/dist ./frontend/dist
# Build-time import smoke: catches a syntax/import error in the image itself (not just
# on the CI host) before it ships. main() only runs under __main__, so this does not
# start the server; data/ and frontend/dist are already COPYed above so the module-load
# path (which reads free_intelligence.json size, resolves web_root(), etc.) is exercised
# for real.
RUN python -c "import server"
ENV HOST=0.0.0.0 \
    PORT=8765 \
    VOLTA_COMMUNITY_WRITES_ENABLED=false \
    PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "-u", "server.py"]
