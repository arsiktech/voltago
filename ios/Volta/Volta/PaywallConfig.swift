import Foundation

/// Feature gate for the "Volta Plus" subscription paywall (GOAL_CONTRACT.md Section 6).
///
/// DEFAULT DISABLED: while `enabled == false`, the ENTIRE app is free and fully usable --
/// deliberate for the TestFlight friends-and-family test period (GOAL_CONTRACT.md Section 0
/// target 3 / Section 6 "Gating"). Only the owner flips this to `true`, and only after real
/// App Store Connect subscription products/pricing exist (Section 7, a human-gated step) --
/// never as part of an autonomous iteration.
///
/// SAFETY-HONESTY IS NEVER GATED BY THIS FLAG. Live/stale/not-live labeling, staleness
/// warnings, fail-closed behavior, and every other honesty-contract surface
/// in docs/backend-engine.md -- decision pills,
/// freshness facts, degraded-coverage banners -- must stay byte-identical for paying and
/// non-paying users in BOTH states of this flag. Keeping that contract intact is the P4.2
/// paywall-UI unit's job (it must never wrap a warning/decision/freshness label in a
/// `PaywallConfig.enabled` check); this file only defines the switch itself and deliberately
/// contains no gating logic of its own.
enum PaywallConfig {
    static let enabled = false
}
