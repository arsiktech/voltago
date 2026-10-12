# App Store compliance draft

Status: engineering assessment only. Export, age rating, DSA trader status, content
rights, consumer terms, and privacy are owner certifications in App Store Connect, not
facts an agent can finalize.

## Release identity snapshot

Reconfirm these against the final archive, not only the project file:

- Bundle ID: `tech.arsik.volta`
- Marketing version: derive from final Xcode Release build settings; currently expected `1.0`
- Build: derive from the highest uploaded App Store Connect build; final value must be
  strictly greater and at least 2
- Device family: iPhone only
- Orientation: portrait only
- Account: no Volta account or login
- Kids category: not intended
- Paywall: `PaywallConfig.enabled` is currently `false`
- Installed name: must align/localize `CFBundleDisplayName` and the home-screen label with
  the final App Store name; current project values are not owner approval

## Export compliance

Apple requires a determination whenever an app uses, accesses, contains, implements, or
incorporates encryption, including encryption supplied by the operating system. See
[overview of export compliance](https://developer.apple.com/help/app-store-connect/manage-app-information/overview-of-export-compliance)
and [determine/upload documentation](https://developer.apple.com/help/app-store-connect/manage-app-information/determine-and-upload-app-encryption-documentation/).

### Engineering draft

- Volta uses system HTTPS/TLS through `URLSession` for the Release service and Apple
  system frameworks including StoreKit.
- No custom, proprietary, unpublished, or non-standard cryptographic algorithm is
  implemented in the inspected app source.
- No VPN, secure messaging, user-controlled encrypted storage, cryptocurrency wallet,
  or general cryptography feature is implemented.
- `ITSAppUsesNonExemptEncryption` is currently `false` in the Release Info.plist,
  representing the project's intended exempt/no-documentation answer.

**Draft App Store Connect posture:** system-provided standard encryption only; no
non-exempt encryption; no export document expected. This is not a legal determination.
The owner must answer Apple's current questionnaire for the final binary, distribution
territories, and entity. If Apple's flow requests documentation or the app adds any
cryptographic feature, stop and reassess before upload.

Owner certification:

- [ ] Final archive has `ITSAppUsesNonExemptEncryption = false`.
- [ ] Dependency and symbol review found no newly added crypto implementation.
- [ ] Production use remains ordinary HTTPS/Apple services only.
- [ ] Owner answered Apple's current export questionnaire and retained the result.
- [ ] Any required French/U.S./other documentation is cleared before distribution.

## Age rating

Apple calculates ratings from the current questionnaire and can assign region-specific
results. An Unrated app cannot be published. See [set an app age rating](https://developer.apple.com/help/app-store-connect/manage-app-information/set-an-app-age-rating/).

### Engineering draft: expected 4+

Based on the inspected app, the expected outcome is 4+ because Volta contains no game,
violence, sexual content, horror, profanity, gambling, loot boxes, simulated gambling,
alcohol/tobacco/drug promotion, contests, dating, or user-to-user communication.

Answer the final questionnaire from the submitted binary, with these cautions:

- Volta links to fixed charger sources and opens routes; it is not an arbitrary in-app
  web browser. Confirm Apple's then-current definition of unrestricted web access.
- Some station facts or photos can originate from public/community directories. Volta
  does not accept user uploads, but the owner must decide whether bounded third-party
  media changes any content descriptor.
- A subscription, if enabled, is an in-app purchase but does not itself make Volta a game
  or child-directed product.
- Choose **Not Applicable**, not Made for Kids, for the Kids age category unless the
  product and all future updates are deliberately redesigned for Apple's Kids rules.
- Do not override the calculated rating downward. Override upward only for a documented
  legal/product reason.

Owner gate:

- [ ] Complete every current App Store Connect age-rating question honestly.
- [ ] Record the calculated global and region-specific result.
- [ ] Confirm no new source media or web surface changes an answer.
- [ ] Keep Volta out of the Kids category.

## EU Digital Services Act trader status

Apple requires a trader-status declaration even if an app is not distributed in the EU,
and Apple cannot decide the status for the developer. If the owner is a trader and
distributes in the EU, Apple verifies and displays specified contact details. See
[Apple's DSA trader requirements](https://developer.apple.com/help/app-store-connect/manage-compliance-information/manage-european-union-digital-services-act-trader-requirements/).

Owner-only decision:

- [ ] Decide EU storefront availability.
- [ ] Self-assess trader/non-trader status with qualified advice if uncertain.
- [ ] If trader, provide and verify the required address/phone/email and payment details;
      confirm the information can be displayed publicly.
- [ ] If non-trader, understand the consumer-rights notice Apple displays.
- [ ] Set the app-specific status in App Information and retain evidence.

No value is preselected in this kit.

## App Privacy and privacy manifest

- Use the conservative draft in `app-privacy.md`; do not choose Data Not Collected.
- Host and link the completed `privacy.md` at a stable public HTTPS URL.
- Embed final public Privacy, Terms, and Support URLs using
  `VOLTA_PRIVACY_POLICY_URL`, `VOLTA_TERMS_URL`, and `VOLTA_SUPPORT_URL`; inspect
  `VoltaPrivacyPolicyURL`, `VoltaTermsURL`, and `VoltaSupportURL` in the final archive and
  tap all corresponding installed Settings/paywall entries.
- Verify `PrivacyInfo.xcprivacy` is inside the final archive and matches App Store
  Connect answers.
- Current required-reason API declarations are UserDefaults `CA92.1` and System Boot
  Time `35F9.1`; re-run a final source/binary audit against Apple's current list. See
  [required-reason API guidance](https://developer.apple.com/documentation/BundleResources/describing-use-of-required-reason-api).
- Tracking must remain false with no tracking domains unless the product changes and a
  full privacy/consent review is completed first.
- Complete documented due diligence for every third party receiving user data and make
  the App Review Guideline 5.1.1(i) same-or-equal-protection assurance only when true.
  Remove a provider/flow if that assurance cannot be substantiated.

## Content rights and attribution

Public technical access is not a redistribution license. The owner must complete
`source-rights-inventory.md` for all bundled and runtime sources, especially third-party
photos, before selecting a paid model or submitting. A rights gap is a release blocker,
not merely a review-notes issue.

Feníe is attribution/link-only in the submitted architecture. Its public station listing
requires a POST-backed web session, so the executable adapter and all direct or nested
Feníe-origin station evidence are excluded even though the owner previously attested to a
written redistribution permission. Rights permission and collection-method permission are
separate gates: Feníe data may return only after a compliant public GET surface and the retained
rights evidence are both reviewed. No Feníe media is copied.

## Agreements, tax, banking, pricing, and territories

If Volta Plus is enabled, the Account Holder must complete the relevant paid-app
agreement, tax, banking, subscription pricing, availability, and trial setup. These are
external, financial, and credentialed actions. This kit does not choose values or perform
them.
