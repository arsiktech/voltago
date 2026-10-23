# Work references

Use the current user request, code, and relevant evidence to choose the next task.
These plans record earlier audits and implementation work; their dates and status
labels are historical observations. Reconcile the affected claims before resuming
work. [README.md](../README.md) is the current setup and verification entry point, and
[GOAL_CONTRACT.md](../GOAL_CONTRACT.md) describes product goals.

As of 2026-10-23, old instruction-file names, imports and launch presets mentioned
inside archived plans are historical provenance. They are not public dependencies.
Current engineering/source contracts are in [the backend guide](../docs/backend-engine.md);
execution guidance and live planning stay private. Dated receipts below remain unchanged.

## Topics to consult when relevant

| Reference | Topic | What to recheck |
| --- | --- | --- |
| [013](013-free-first-ecosystem.md) | Free registry and community evidence | Current UI coverage, source policy, and provider activation |

Do not replay a completed lane because an older table or toolchain note still
describes it as pending.

## Completed plan archive

Plans 001–010 and 012 were recorded as completed in the previous index. They stay
at their original paths so existing issue, test, and QA links remain useful.
Execution order, fixed file scopes, per-step test mandates, and stop-on-drift rules
inside these historical documents are retired. Preserve the product behavior and
source-policy decisions they explain; use current code and focused verification.

- [001 — Offline verification](001-one-command-offline-verify.md)
- [002 — Repository hygiene](002-repo-truth-and-hygiene-pass.md)
- [003 — Filtering and ranking](003-live-filter-and-ranking-correctness.md)
- [004 — Request resilience](004-request-path-resilience.md)
- [005 — Public deployment hardening](005-public-deploy-hardening.md)
- [006 — Parser and merge characterization](006-characterization-tests-merge-parsers-windows.md)
- [007 — Request performance](007-request-path-performance.md)
- [008 — Honesty and connector parity](008-honesty-rule-drift-realignment.md)
- [009 — Static shell retirement](009-retire-static-legacy-shell.md)
- [010 — Data refresh lifecycle](010-autonomous-data-lifecycle.md)
- [012 — Native route motion](012-native-route-motion-system.md)

The original July backlog and deferred ideas remain in Git history. Reassess an
idea only when it serves the active request; an old deferral is not an approval gate.
