# Verification, QA receipts and PR gates

Read when: a change touches source, tests, data or the built frontend and the source-bound QA receipts need refreshing, or a PR fails `test_current_artifacts_pass_integrity`. The authoritative command sequence is the "Refresh source-bound QA receipts" section of `README.md`; this file adds the traps.

## Rules

- The regression hash covers source, tests, configuration, workflow files, canonical data and the built frontend (`frontend/dist`). Finish edits and build first, then record evidence. Any later change invalidates the receipts and CI fails `test_current_artifacts_pass_integrity` (in `tests/test_artifact_integrity.py`) with "input_sha256 does not match". Refresh receipts with the README sequence and commit them on the same branch. Never edit a receipt, weaken a behavioral check, or suppress an integrity failure.
- Validators have endpoint contracts. The source-bound regression expects the app at `http://127.0.0.1:8773`; the manual field packet has its own `field_test.app_url` contract. Read each validator's expected endpoint before generating, and generate each against the endpoint it requires instead of rebuilding records for an incompatible URL.
- Bootstrap receipts (`--bootstrap-artifacts --skip-browser`) are explicitly partial evidence. Preserve per-stage exit codes and result JSON; a runner that continues after a failure to collect diagnostics exits zero when orchestration finishes, not when every gate passed. Preserve failed check ids. Do not describe a partial run as green.
- `manual-proof-status` (TC-051 field test, TC-056 VoiceOver) fails by design until authentic manual evidence exists. Do not mark them passed.
- If browser checks such as `cdn-blocked` or `source-detail-a11y` fail in a change that touches no frontend or server code, rerun each script alone twice, then rerun the full suite for the receipt, and disclose the first failing run in the PR instead of hiding it.

## Practical setup

- Build the frontend first; start `PORT=8773 python3 -u server.py` as a background process; snapshot `qa/` before the bootstrap (the README sequence copies it to a temp dir).
- The full `scripts/qa_regression_suite.py` run exceeds typical foreground tool timeouts. Run the final gates as one background job with per-step logs. Stream long collection and QA output into per-stage log files with unbuffered Python (`python3 -u`), and capture the final structured result separately; buffering until exit hides progress and discards timeout diagnostics.
- When lockfiles and `frontend/` match another checkout, cloning its `node_modules` (`cp -cR` on macOS) saves a reinstall.
- Qualify the toolchain before installing: read the required Node and npm versions (`.node-version`, `package.json` engines), then check `node --version`, `npm --version` and their resolved executables in the exact build environment. A pinned Node distribution may bundle an older npm and shadow a compatible one; verify both, use the validated npm for `ci --engine-strict`, and do not disable engine checks. If a script invokes bare `npm`, qualify its child-process PATH too.
- For headless browser failures, compare exact failures on the unchanged source with the same browser. GPU driver warnings are distinct from app errors. Keep the original result when you extend an observation budget; report the measured transition and the configured budget.

## When the full local unittest run fails

Run the same failing modules on unchanged `origin/main` (a detached worktree) before attributing them to your change. Network-dependent and timing tests can fail on a laptop regardless. A test can pass in one full run and hang alone, so repeat each extra failure several times in isolation on both trees with a per-run cap, as one background job. Failing on both trees means pre-existing; say so with counts. The artifact-integrity test and some `tests/test_public_hardening.py` tests can fail locally for environmental reasons; confirm against main rather than assuming.

## Scheduler contract tests

Before adding or changing a scheduler unit (`deploy/systemd/*`, `deploy/launchd/*`), read the existing scheduler-contract tests as well as the new unit's tests. Exact service and timer counts and marker expectations can encode the old topology. Extend the expected inventory for the approved job while preserving the original jobs' guards, then run that focused contract before the full suite.

## Freeze before expensive runs

Finish affected source, tests, scheduler contracts and docs before freezing a candidate for ingestion or full regression. Keep further repairs in a separate checkout while a bound run is active; editing its inputs mid-run invalidates evidence. Run focused integration checks before regenerating final QA records and reuse unchanged valid results. Bind final evidence to source, data, binary and config identity.

## Launch planning across branches

Reconcile `git worktree list`, the active branch and the plans index (`plans/README.md`) before defining launch scope; foundations may exist on separate branches, and a Done tracker item is not proof of inclusion in the active app or production. Do not silently replace the full pilot with a reduced MVP. If several people work in parallel, one owner holds shared backend contracts, native integration and the String Catalog, canonical QA receipts, production, and Apple writes.
