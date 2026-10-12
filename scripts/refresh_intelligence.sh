#!/bin/zsh
# Compatibility entrypoint. All locking, exact-candidate gates, transactional
# promotion, receipts, commit allowlisting, and optional push behavior live in
# auto_refresh.py so scheduled and manual runs cannot drift apart.
set -eu

SCRIPT_DIR="${0:A:h}"
ROOT="${VOLTA_ROOT:-${SCRIPT_DIR:h}}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/volta-pycache}"
exec python3 "$ROOT/scripts/auto_refresh.py" --root "$ROOT" "$@"
