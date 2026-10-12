# Module: QA Regression And Completion Evidence

The QA layer is a product subsystem. It encodes whether Volta remains safe for a
driver, not just whether functions compile.

## Responsibilities

- Compile Python and JavaScript surfaces.
- Run dependency-free Python unit tests and Vitest frontend tests.
- Smoke test a running server and SPA/PWA shell.
- Run browser contracts for mobile UI, focus, accessibility, failure, refresh,
  link handoff, and driver recovery flows.
- Run live/source canaries and source-link samples when requested.
- Keep manual field-test and VoiceOver gates explicit.
- Validate QA artifact shape and completion state.

## Key Files

- [scripts/qa_regression_suite.py](../../../scripts/qa_regression_suite.py) -
  master runner and report writer.
- [tests](../../../tests) - Python `unittest` suite.
- [frontend/src/lib/format.test.ts](../../../frontend/src/lib/format.test.ts) -
  Vitest tests for honesty-critical frontend logic.
- [scripts/qa_*.py](../../../scripts) - Python smoke, artifact, canary, and
  completion checks.
- [scripts/qa_*.js](../../../scripts) - Playwright/Chrome browser contracts.
- [qa/regression-suite-latest.json](../../../qa/regression-suite-latest.json) -
  latest suite evidence.
- [qa/completion-audit-latest.json](../../../qa/completion-audit-latest.json) -
  completion audit evidence.

## Public API

- `suite_checks(base_url, node, include_browser, include_external) -> list[Check]`
- `run_check(check, base_env) -> dict`
- `scripts/qa_regression_suite.py --base-url URL [--skip-browser] [--include-external]`
- `python3 -m unittest discover -s tests -v`
- `npm --prefix frontend run test`

## Internal Structure

The regression suite defines a fixed list of checks, runs each in a subprocess
with timeout, captures stdout/stderr, truncates large output, and writes a JSON
report. Browser checks are optional but explicit. External checks are opt-in and
include live-source canary, ingestion canary, source-link sample, and history
analysis.

## Dependencies

- **Uses:** Python stdlib, Node, Playwright library, system Chrome for browser
  contracts, running local Volta server for API/browser checks.
- **Used by:** definition of done, defect verification, completion audit.

## Notable Patterns / Gotchas

- The completion audit is expected to remain incomplete until physical field
  test and native VoiceOver evidence are supplied.
- Browser checks need `VOLTA_QA_NODE`, `NODE_PATH`, and `VOLTA_QA_CHROME` to
  match this Mac's runtime.
- If browser checks are skipped, the report must say so.
- Real bug fixes require `qa/defects.csv` plus run-log/coverage updates.
