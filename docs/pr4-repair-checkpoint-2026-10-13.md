# Source-coverage PR repair checkpoint — 2026-10-13

This checkpoint repairs acquisition and provenance defects in PR #4. It does not
approve merging or deploying the entire Spain coverage branch.

## Corrected behavior

- Place to Plug zones retain the artifact of their own page, including when a
  duplicate zone replaces an earlier copy. A completed row minimum cannot turn an
  interrupted walk into success: missing pages, exhausted budgets, malformed data,
  empty pages claiming a next page, fetch failures and the page ceiling all prevent
  publishing partial rows.
- PlugShare collection, detail-cache ingestion and authorization-header construction
  fail before I/O. The embedded credential and its test literals are removed from
  the current tree. External location IDs are validated before cache-path helpers.
  Historical Git commits were not rewritten.
- Electromaps, Chargemap and Consum exclusions match current main. PlugShare has an
  explicit exclusion. Acquisition entry points and active adapter registration agree
  with the policy; a cached payload cannot re-enable an excluded collector.
- Source policy applies before merging price, hardware or identities. Existing mixed
  rows are rejected when an excluded origin occurs in secondary sources, merged
  source families, stable identifiers or full contributor evidence, even when the
  primary source is allowed. Without retained source facts, removing the primary
  label is insufficient to reconstruct an allowed row.
- Only MapaREVE and Feníe retain POST exceptions. Repsol and Place to Plug acquisition
  are dormant before cache/network access; offline parser and pagination fixtures
  remain available. Public API, source documentation and application copy no longer
  claim the excluded directories are active.

## Validation scope

The Python tests cover both default policy rejection and dormant parser behavior.
Tests explicitly replacing policy guards use synthetic fixture inputs and mocked I/O;
separate tests prove that the default entry points cannot fetch or read cached data.
Validation: Python unittest discovery ran 980 tests with 6 skips; 225 frontend tests
passed, the production frontend build passed, and lint completed with the existing
render-ref warning in `App.tsx`. Python compilation and `git diff --check` passed.
No live collection, production data promotion or native application rollout was
performed for this repair.

## Remaining merge and release blockers

The branch's station identity migration and old native/community clients still need
an end-to-end compatibility solution. Alias-based delta accounting alone does not
prove that historical favorites, check-ins or photos resolve correctly through
multiple refreshes. Mixed-operator merge continuity and reused/moved IDs require the
separate identity work; this patch does not certify them.

A replacement candidate must be built from permitted source contributions and pass
full delta/canary checks before any promotion. Prior PR counts, which included now
excluded sources, are not the count of a releasable candidate. Main has also advanced
since this branch diverged; the current MCP and worldwide runtime must be preserved
when integrating useful pieces.
