# Volta Go product goals

Use this document for product and release work. The current user request sets the
scope; [AGENTS.md](AGENTS.md) supplies the working rules. Choose implementation
order from current code, dependencies, and evidence. The former Claude phase loop,
fixed agent roles, and blanket per-step gates are retired.

## 0. Mission

Help a driver answer: "Can I charge here now, cheaply, without a parking
surprise?" Put trustworthy free options first, cheap options next, and show how to
use the charger. Coverage grows through verifiable sources; uncertainty stays visible.
The directory can cover countries worldwide; regional live integrations, native
currencies, access restrictions and source-specific gaps stay explicit.

## 1. Product foundations

- Preserve live/official/directory/community/cached evidence boundaries and separate
  charging-price and parking-cost provenance. Public upstream GET surfaces only.
- Python remains stdlib-only; iOS uses native frameworks only; frontend dependencies
  should earn their place. Both clients share the same station-truth contract.
- Keep the complete app free while `PaywallConfig.enabled` is false. Paid value,
  if activated later, is the curated, corroborated experience. Safety warnings,
  staleness labels, and fail-closed behavior remain identical for every user.
- Local community content and private evidence stay out of Git. Preserve source
  exclusions and the candidate/delta/canary promotion path.

## 2. Context on demand

[plans/README.md](plans/README.md) indexes relevant work and historical plans.
Use the affected client/source documentation for implementation. For a release,
read [RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md), [DEPLOY.md](DEPLOY.md), and
[docs/appstore/README.md](docs/appstore/README.md). Dated status and reports require
reconciliation; they do not establish the current deployed build or remaining work.

## 3. Completion evidence

A local task is complete when its requested behavior is implemented, the relevant
checks pass, and any limitations are explicit. Update affected docs and QA records.
For an integrated release candidate, verify the full offline suite, relevant
browser/API contracts, matching native build/tests, and source/data integrity.

A public release additionally needs current deployment and Apple evidence, reviewed
legal/source-rights material, authentic `TC-051` field and `TC-056` native VoiceOver
proof, and the authorized release action. A blocked external step leaves release
completion open while independent implementation can continue. Never manufacture
receipts or weaken tests to turn an incomplete release green.

## 4. Work priorities

- Maintain backend correctness, bounded network/cache behavior, and automated data
  refresh before exposing new source claims or expanding live coverage.
- Refine the map, list, and station detail around routing decisions; preserve useful
  source-backed leads and clear degraded/offline states on web and native clients.
- Finish the free-first inventory and community experience through independently
  verifiable slices. New integrations need current source-policy evidence.
- Prepare native distribution and public release from an identified source/data
  candidate. Use current gaps rather than replaying completed July plans.
- Keep subscription activation, new platform surfaces, and unrelated product
  expansion outside the launch path unless the user requests them.

## 5. Experience quality

- Clear hierarchy and consistent spacing; provenance, evidence age, route status,
  parking conditions, and usage instructions stay readable.
- Light/dark appearance, Dynamic Type through accessibility XXL, minimum 44pt touch
  targets, useful VoiceOver labels/traversal, and a complete Reduce Motion path.
- Purposeful, interruptible motion and sparse haptics: light selection feedback and
  route-ready confirmation without delaying the primary action.
- Target cold launch to first meaningful content below 1.5 seconds in the Simulator;
  measure it. Keep map/list interaction smooth and avoid jumps on live refresh.
- Design loading, empty, error, offline, stale, and degraded states. Keep concise
  driver-facing copy localized in en/es/ru; distinguish drafts from reviewed translations.
- For visual changes, inspect the affected rendered journey and relevant accessibility
  states. Store evidence needed to substantiate the claim.

## 6. Subscription design (disabled by default)

Use StoreKit 2 and existing products `volta.plus.monthly` / `volta.plus.yearly`.
The intended introductory offer is a seven-day trial, subject to approved App Store
Connect configuration and eligibility. Render prices, currencies, periods, offers,
and savings from real `Product` metadata; do not invent catalog values.

The paywall explains value, renewal terms, monthly/yearly choices, Restore,
Terms, and Privacy. Use `Transaction.currentEntitlements`; a lapse does not
interrupt an active route, and the next launch resolves access. The app stays
account-less, with purchases tied to the Apple ID.

Keep `PaywallConfig.enabled` false until product configuration, pricing, agreements,
legal hosting, and release prerequisites are approved. Native StoreKit tests cover
purchase, trial/offer display, expiry, restore, and entitlement behavior with the
local test configuration. Production archives exclude debug StoreKit fixtures.

## 7. External actions

Follow the existing authorization in the user conversation and the boundary in
[AGENTS.md](AGENTS.md). Prepare concrete artifacts before requesting any missing
approval. Keep signing, upload, processing, Internal group assignment, installation,
App Review, and public availability separate. Pricing, agreements, banking/tax,
legal certifications, credentials, spend, and production changes need the appropriate
owner authorization; local preparation and read-only checks can proceed.
