# Volta Plus App Store Connect draft

Status: **pre-publication template; not App Store Connect-ready or release-ready**. Local
StoreKit code/configuration exists, but final implementation evidence, production
products, prices, availability, agreements, hosted links, and paywall enablement remain
open gates. Do not create duplicates: inspect the existing app and subscriptions first.

Apple's subscription fields and localization flow are documented in
[auto-renewable subscription information](https://developer.apple.com/help/app-store-connect/reference/in-app-purchases-and-subscriptions/auto-renewable-subscription-information)
and [offer auto-renewable subscriptions](https://developer.apple.com/help/app-store-connect/manage-subscriptions/offer-auto-renewable-subscriptions/).
The product must also comply with [App Review Guideline 3.1.2](https://developer.apple.com/app-store/review/guidelines/#subscriptions).

## Repository truth

| Item | Current value |
| --- | --- |
| Subscription group working/reference name | `Volta Plus` |
| Monthly product ID | `volta.plus.monthly` |
| Yearly product ID | `volta.plus.yearly` |
| Monthly duration | 1 month |
| Yearly duration | 1 year |
| Access | Same complete Volta Plus entitlement; duration alternatives |
| Account | None; entitlement follows the Apple ID / StoreKit ledger |
| Pricing in app code | None; all display price/currency/period values come from StoreKit |
| Paywall feature flag | `PaywallConfig.enabled = false` |
| Intended introductory offer in repo contract/local config | 7-day free trial, subject to StoreKit eligibility |

The committed local `VoltaTests/Volta.storekit` file currently uses a USA test storefront
with test values `4.99` monthly and `39.99` yearly and a one-week free introductory offer.
Those values are **Simulator/test fixtures only**, not a production price proposal and not
authority to copy them into App Store Connect. The owner must select real prices,
territories, tax treatment, and offer availability.

## Product creation/reconciliation

1. Open the existing App Store Connect app for bundle ID `tech.arsik.volta`.
2. Under Monetization → Subscriptions, search for the group and exact product IDs above.
3. If they already exist, reconcile their state; product IDs cannot be casually recreated.
4. If they do not exist, create one group and the two exact product IDs only after the
   owner has approved pricing, free/paid split, agreements, and release plan.
5. Configure durations, levels/crossgrade behavior, territories, prices, tax category,
   grace-period policy, and the introductory offer according to Apple's current UI.
6. Do not make the products available or enable the in-app paywall until the complete
   sandbox/TestFlight test matrix passes.

## Localized subscription-group copy

App Store Connect's group localization uses a display name; do not invent or paste a
group-description field.

| Locale | Display name | Characters |
| --- | --- | ---: |
| English | `Volta Plus` | 10 |
| Spanish | `Volta Plus` | 10 |
| Russian | `Volta Plus` | 10 |

Spanish and Russian require native-speaker approval.

## Localized product copy

### Monthly — `volta.plus.monthly`

| Locale | Display name | Name chars | Product description | Description chars |
| --- | --- | ---: | --- | ---: |
| English | `Volta Plus Monthly` | 18 | `Full charger access. Renews monthly.` | 36 |
| Spanish | `Volta Plus mensual` | 18 | `Acceso completo. Renovación mensual.` | 36 |
| Russian | `Volta Plus на месяц` | 19 | `Полный доступ. Продление ежемесячно.` | 36 |

### Yearly — `volta.plus.yearly`

| Locale | Display name | Name chars | Product description | Description chars |
| --- | --- | ---: | --- | ---: |
| English | `Volta Plus Yearly` | 17 | `Full charger access. Renews yearly.` | 35 |
| Spanish | `Volta Plus anual` | 16 | `Acceso completo. Renovación anual.` | 34 |
| Russian | `Volta Plus на год` | 17 | `Полный доступ. Продление ежегодно.` | 34 |

Every product display name must be 2–30 Unicode characters and every product description
must be at most 45 characters. The measured values above must be recomputed after any
copy edit. Spanish and Russian still require native-speaker approval.

Do not insert a numeric price or promise a trial into these descriptions until the exact
App Store Connect configuration is approved. The production paywall will show eligibility,
period, and localized price directly from StoreKit.

## Subscription App Review notes

```text
Volta Plus provides the same complete charger decision experience through two duration alternatives in one subscription group: one month and one year. The app does not hardcode price, currency, billing period, trial eligibility, trial duration, or savings. It reads them from StoreKit Product metadata.

For a user without a verified entitlement, the production paywall shows both plans, the localized StoreKit price and period, any introductory offer for which StoreKit reports the user eligible, renewal/cancellation disclosure, Restore Purchases, Terms, and Privacy. Purchase access is granted only after a verified StoreKit transaction appears in current entitlements.

There is no Volta account or login. Apple handles payment. Restore Purchases checks the same Apple ID. If entitlement changes while a driver is already in the charger experience, the current process remains usable so the app does not replace an active route with a paywall; the next cold launch enforces the current entitlement.

Product IDs:
- volta.plus.monthly
- volta.plus.yearly
```

Only use those notes if the submitted build has the paywall enabled and the reviewer can
reach it. Otherwise use the disabled-paywall branch in `review-notes.md`.

## Subscription review screenshot

Capture a real screenshot from the exact submitted build and App Store sandbox/product
configuration showing:

- Volta Plus value proposition;
- monthly and yearly choices;
- real localized StoreKit prices and periods;
- the actual eligible-offer state;
- renewal/cancellation disclosure;
- Restore Purchases; and
- Terms and Privacy links.

The screenshot and installed paywall must open the exact final public destinations
embedded through `VOLTA_TERMS_URL` and `VOLTA_PRIVACY_POLICY_URL`. Settings must likewise
open those destinations and the Support page embedded through `VOLTA_SUPPORT_URL`. Test
every link from the installed final Release build; a visible label is not evidence that
the destination is correct.

Do not upload a screenshot using the local `4.99`/`39.99` fixture unless those happen to
be the owner-approved App Store prices for that storefront. Do not use a DEBUG-only forced
paywall or appearance override as production review evidence.

## Enablement gate

- [ ] Existing ASC app and product state reconciled; no duplicate created.
- [ ] Paid Apps agreement, tax, and banking complete.
- [ ] Owner approved free-versus-paid split, prices, territories, and 7-day offer.
- [ ] en/es/ru subscription copy approved.
- [ ] Hosted Terms, Privacy, and Support URLs live.
- [ ] Final archive contains `VoltaTermsURL`, `VoltaPrivacyPolicyURL`, and
      `VoltaSupportURL` equal to the approved public 200/no-login/no-placeholder pages.
- [ ] Every Settings link and every link on the enabled production paywall was tapped from
      the installed candidate and opened its matching final page.
- [ ] Content-rights inventory supports the chosen paid use.
- [ ] Sandbox purchase, eligible/ineligible offer, cancellation, pending, expiry, refund/
      revocation, billing retry/grace, restore, offline/catalog-unavailable, and cross-device
      behavior tested against ASC products.
- [ ] Paywall flag intentionally enabled in a new build; all release tests rerun.
- [ ] App metadata, screenshots, privacy answers, IAP review notes, and binary agree.
- [ ] Submitted build number is higher than every existing uploaded build.
