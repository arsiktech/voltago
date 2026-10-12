#!/usr/bin/env bash
# scripts/verify.sh — one-command offline verification gate for Volta Go.
#
# Default (no args) runs the fast, fully-offline tiers documented in
# README.md: backend compile -> backend unittest -> frontend
# vitest (~10s total). `--full` additionally runs the frontend lint and the
# frontend production build (tsc -b typecheck + Vite build).
#
# This script deliberately does NOT run the browser DOM contracts or the
# external canaries orchestrated by scripts/qa_regression_suite.py — those
# need a running server. Their status is always reported as NOT RUN below;
# never silently implied as covered. See README.md for the running-server checks.
#
# Any failing step aborts the script immediately with that step's non-zero
# exit code — no swallowed failures (unlike the historical
# `for t in tests/test_*.py; do ... || break; done` loop, which leaves $? at
# 0 even after a failing test file).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FULL=0
case "${1:-}" in
  "") ;;
  --full) FULL=1 ;;
  *)
    echo "Usage: $0 [--full]" >&2
    exit 2
    ;;
esac

export PYTHONPYCACHEPREFIX=/tmp/volta-pycache

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "== backend: py_compile =="
python3 -m py_compile server.py scripts/*.py
echo "py_compile OK"
echo

echo "== backend: unittest discover =="
UNITTEST_LOG="$WORKDIR/unittest.log"
python3 -m unittest discover -s tests 2>&1 | tee "$UNITTEST_LOG"
echo

echo "== frontend: vitest =="
VITEST_LOG="$WORKDIR/vitest.log"
npm --prefix frontend run test 2>&1 | tee "$VITEST_LOG"
echo

LINT_NOTE=""
BUILD_NOTE=""
if [ "$FULL" -eq 1 ]; then
  echo "== full: frontend lint =="
  npm --prefix frontend run lint
  LINT_NOTE=", lint OK"
  echo

  echo "== full: frontend build =="
  npm --prefix frontend run build
  BUILD_NOTE=", build OK"
  echo
fi

# Preserve runner summaries, including skips and expected failures. Do not
# turn the number of tests run into a count of passing tests. If a runner's
# output format shifts, refer to its output above; exit codes remain the gate.
UNITTEST_LINE="$(grep -E '^Ran [0-9]+ test' "$UNITTEST_LOG" | tail -1 || true)"
UNITTEST_RESULT="$(grep -E '^OK($|[[:space:]]+\()' "$UNITTEST_LOG" | tail -1 || true)"
BACKEND_TESTS="unittest: ${UNITTEST_LINE:-see output above}; ${UNITTEST_RESULT:-see runner result above}"

VITEST_LINE="$(grep -E '^[[:space:]]*Tests[[:space:]]+' "$VITEST_LOG" | tail -1 || true)"
FRONTEND_TESTS="vitest: ${VITEST_LINE:-see output above}"

echo "== VERIFY OK (offline tiers) =="
echo "backend:  py_compile OK, ${BACKEND_TESTS}"
echo "frontend: ${FRONTEND_TESTS}${LINT_NOTE}${BUILD_NOTE}"
echo "NOT RUN:  browser DOM contracts + external canaries"
echo "          (needs a running server: scripts/qa_regression_suite.py --base-url ...)"
