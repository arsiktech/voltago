#!/usr/bin/env bash
# Compatibility entrypoint. All locking, exact-candidate gates, transactional
# promotion, receipts, commit allowlisting, and optional push behavior live in
# auto_refresh.py so scheduled and manual runs cannot drift apart.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${VOLTA_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/volta-pycache}"
exec python3 "$ROOT/scripts/auto_refresh.py" --root "$ROOT" "$@"
