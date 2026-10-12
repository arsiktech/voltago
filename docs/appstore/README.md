# Volta Go App Store release kit

Status: **pre-publication templates, not release-ready, not submitted, and not legal
advice**. Last reconciled with the repository on 2026-07-26. Code revisions, archive
evidence, owner decisions, hosted pages, and human gates remain open.

This directory contains the copy and evidence templates needed for an App Store
submission. It deliberately does not contain credentials, App Store Connect IDs,
signing material, an invented operator identity, or a claim that Volta has permission
to republish every public source. The owner must close the gates below before any page
is hosted or any build is submitted.

## Documents

- `metadata.md` — en/es/ru App Store metadata drafts and measured limits.
- `app-privacy.md` — conservative App Store Connect privacy-answer draft.
- `privacy.md` — pre-publication privacy-notice template with owner placeholders.
- `support.md` — pre-publication support/data-request template.
- `terms.md` — pre-publication terms template.
- `review-notes.md` — App Review notes and reviewer test path.
- `source-rights-inventory.md` — source-by-source rights certification gate.
- `compliance.md` — export, age-rating, DSA/trader, encryption, and account notes.
- `subscriptions.md` — Volta Plus product/localization/review draft.
- `screenshots.md` — current iPhone screenshot plan and evidence manifest.
- `../../RELEASE_RUNBOOK.md` — ordered human release procedure.

## Documentation work represented here

- Draft copy is grounded in inspected repository behavior, including the automatic
  one-location request at launch after permission has already been granted and another
  request when the locate control is tapped.
- Metadata fields are below Apple's current limits.
- The privacy draft does **not** use the incorrect “data not collected” answer.
- StoreKit prices are not proposed or hardcoded; the only numbers documented are
  explicitly labeled as local test-configuration values.
- Screenshot slots reflect the current iPhone-only, portrait-only release target.
- Public/open access is kept separate from redistribution rights.
- Required Release legal-link settings are documented as
  `VOLTA_PRIVACY_POLICY_URL`, `VOLTA_TERMS_URL`, and `VOLTA_SUPPORT_URL`, resolving in
  the archive to `VoltaPrivacyPolicyURL`, `VoltaTermsURL`, and `VoltaSupportURL`.

These statements describe the documentation target. They do not prove that the final
implementation, archive, hosted pages, or installed links pass.

## Owner gates — must all be closed

- [ ] Choose a final app name and confirm availability in the existing App Store
      Connect record; do not create a duplicate app.
- [ ] Supply the operator/controller legal identity and real support contact.
- [ ] Choose and activate a stable public HTTPS Volta endpoint; record its operator,
      subprocessors, regions, log retention, cache retention, and deletion process.
- [ ] Host final Privacy, Support, and Terms pages at stable HTTPS URLs.
- [ ] Finish and verify the fail-closed hosted-link implementation; embed all three final
      URLs, inspect their archive values, then tap every Settings link and every paywall
      link present in the selected release branch.
- [ ] Align/localize the installed `CFBundleDisplayName` and home-screen label with the
      final App Store name; rebuild, install, archive, and recapture screenshots afterward.
- [ ] Certify each source and photo row in `source-rights-inventory.md`, obtaining
      permission or removing content when public access does not grant reuse rights.
- [ ] Confirm the submitted candidate retains Feníe as attribution/link-only. The historical
      owner-retained permission does not override Volta's GET-only collection boundary; no
      Feníe-origin station evidence may be bundled unless a compliant public GET surface is
      separately reviewed and reintroduced through the source-policy gate.
- [ ] Complete provider privacy due diligence and substantiate Apple's Guideline 5.1.1(i)
      same-or-equal-protection statement. Remove any provider/flow that cannot support it.
- [ ] Make the DSA trader-status self-assessment and provide any required verified
      public contact information.
- [ ] Approve native-speaker review of Spanish and Russian copy.
- [ ] Decide with appropriate advice whether hosted Privacy, Support, and Terms pages
      require Spanish/Russian versions; the pre-publication templates here are English.
- [ ] Decide App Store price points, territories, trial, and free-versus-paid release
      behavior; keep `PaywallConfig.enabled` off until that decision and its tests pass.
- [ ] Complete App Store Connect privacy, export, age-rating, agreements, tax/banking,
      signing, upload, TestFlight, and review steps.
- [ ] Complete manual TC-051 physical field evidence and TC-056 native VoiceOver
      traversal; automated results do not close those gates.

## Final-submission placeholder gate

Do not run a simple bracket grep only on one template. Before hosting **and** again before
pasting into App Store Connect, export every final text artifact into one temporary
directory: Privacy, Support, Terms, all metadata locales, App Review notes, subscription
localizations, App Privacy/export/age/DSA answers, attribution/source statements, and
screenshot captions/manifest. Do not include these instructional source templates as the
final artifacts.

Run this command in `zsh`; it scans every text-like file recursively, including multiline
owner instructions and common template tokens:

```bash
(
SUBMISSION_DIR=/tmp/volta-submission-final
count="$(find "$SUBMISSION_DIR" -type f \( -name '*.txt' -o -name '*.md' -o \
  -name '*.html' -o -name '*.json' -o -name '*.csv' \) | wc -l | tr -d ' ')"
[ "$count" -gt 0 ] || { echo 'BLOCKED: no final submission files found'; exit 1; }

find "$SUBMISSION_DIR" -type f \( -name '*.txt' -o -name '*.md' -o \
  -name '*.html' -o -name '*.json' -o -name '*.csv' \) -print0 |
  xargs -0 perl -0777 -ne '
    while (/(\[(?=[^\]]*(?:OWNER|REQUIRED|FILL|ACTUAL|RECOMPUTE|FINAL|INSERT|CONTACT|EMAIL|URL|DOMAIN|HOST|ADDRESS|DATE|BUILD|REGION|RETENTION|TELEPHONE|LEGAL|SUPPORT|PRIVACY|PUBLIC|STABLE|GIT COMMIT|HONEST))[^\]]+\]|\{\{.*?\}\}|\$\{.*?\}|<%.*?%>)/gs) {
      ($hit = $&) =~ s/\s+/ /g; print "$ARGV: placeholder: $hit\n"; $bad = 1;
    }
    while (/\b(?:PRE-PUBLICATION TEMPLATE|NOT (?:PASTE-|RELEASE-)?READY|DO NOT (?:PUBLISH|SUBMIT|PASTE)|OWNER(?:-GATED| REVIEW| MUST|:)|NATIVE-SPEAKER (?:APPROVAL|REVIEW)|QUALIFIED LEGAL REVIEW|MANUAL REVIEW(?: REQUIRED)?|PLACEHOLDER|RECOMPUTE|TODO|TBD|DRAFT|PLANNED|UNVERIFIED|BLOCKED)(?=\W|$)/gis) {
      ($hit = $&) =~ s/\s+/ /g; print "$ARGV: instruction: $hit\n"; $bad = 1;
    }
    END { exit($bad ? 1 : 0) }
  '
)
```

Expected: no output and exit 0. Any match blocks hosting/pasting until reviewed and
removed from the final artifact. This lexical gate supplements, rather than replaces,
manual review and destination checks.

Apple references used by the kit:

- [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [App information fields](https://developer.apple.com/help/app-store-connect/reference/app-information/app-information)
- [Platform-version metadata](https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information)
- [Screenshot specifications](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications)
- [App Privacy Details](https://developer.apple.com/app-store/app-privacy-details/)
- [Export compliance](https://developer.apple.com/help/app-store-connect/manage-app-information/overview-of-export-compliance)
- [Age-rating setup](https://developer.apple.com/help/app-store-connect/manage-app-information/set-an-app-age-rating/)
- [EU DSA trader requirements](https://developer.apple.com/help/app-store-connect/manage-compliance-information/manage-european-union-digital-services-act-trader-requirements/)
