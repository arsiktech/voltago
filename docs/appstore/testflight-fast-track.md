# Volta Go TestFlight fast track

Goal: a processed Volta Go build that installs from TestFlight on a real iPhone. This is
not the App Store launch gate and does not wait for App Store screenshots, subscriptions,
DSA/storefront decisions, TC-051, or TC-056.

## State model

1. `REPO_READY`: the free/paywall-off candidate is frozen and its build outranks the last known
   Internal Testing assignment. This does not claim a prior device install.
2. `ARCHIVE_INPUTS_READY`: the highest current App Store Connect build was checked within the
   last two hours and the candidate still outranks it.
3. `ARCHIVE_READY`: an actual signed archive was inspected and contains the exact candidate,
   bundle ID, production service URL, and hosted-link URLs.
4. `UPLOAD_ACCEPTED`: Xcode Organizer validated and uploaded the signed archive.
5. `TESTFLIGHT_READY`: Apple processed the build and it is assigned to an Internal Testing group.
6. `FRIENDS_FAMILY_INPUTS_READY`: the signed archive exists and distinct public TestFlight-beta
   Privacy, Terms, and Support pages are live. This is an external-beta metadata gate, not a
   certification that full App Store legal/operator publication requirements are complete.
7. `FRIENDS_FAMILY_READY`: an External Testing group contains the build, TestFlight Beta App
   Review approved it, and invitations or a controlled public link are active.

Never collapse one state into the next. In particular, upload acceptance is not proof that a
tester can install the build.

## Current verified state — 2026-08-23

- `REPO_READY`, `ARCHIVE_INPUTS_READY`, and `ARCHIVE_READY` are proven for version 1.2 build
  `202608231930`. The distribution-signed beta-5 archive passed the recursive 14/14 Release
  contract with zero skips and contains `https://voltago.app` plus the exact `/privacy`, `/terms`,
  and `/support` URLs.
- `UPLOAD_ACCEPTED` is proven by Xcode Organizer at 21:38 Europe/Madrid after App Store Connect
  validation passed. Distribution used Apple's **TestFlight Internal Only** method.
- `TESTFLIGHT_READY` is proven: App Store Connect processed the build, reports status **Testing**,
  and assigned it to the existing **Internal** group with one tester/invitation.
- Device installation remains a distinct downstream receipt. The registered test iPhone on
  iOS 27.0 still reported installed build 1.1 (`202607101745`) when the new assignment was checked;
  install/update to 1.2 must be completed in the TestFlight app.
- No external group, Beta App Review, App Review submission, or public release was created.

## Fast execution order

1. In App Store Connect, open the existing app for `tech.arsik.volta` and record the highest
   build visible under TestFlight. Do not create a duplicate app record.
2. Run:

   ```bash
   python3 scripts/qa_testflight_candidate.py \
     --highest-asc-build <NUMBER> \
     --asc-checked-at <CURRENT-ISO-8601-TIMESTAMP>
   ```

   Stop if `archive_inputs_ready` is false. The candidate must be strictly newer than the
   recorded Internal Testing assignment and the current App Store Connect maximum.
3. Keep `PaywallConfig.enabled = false`. Use the production service and public TestFlight-beta
   Privacy, Terms, and Support URLs. Run the release-contract and unsigned archive checks from
   `RELEASE_RUNBOOK.md` Sections 7-8.
4. In Xcode Organizer, create the signed archive. When no local Apple Distribution identity is
   installed, Organizer cloud-signing availability remains unknown until Xcode proves it. Before
   upload, rerun the checker with the same fresh ASC receipt plus `--archive <PATH>` and require
   `archive_ready=true`.
5. Validate App and upload; record upload acceptance separately.
6. Wait for Apple processing. Complete export-compliance prompts and add the processed build to
   an Internal Testing group; this establishes `TESTFLIGHT_READY`. Then install it from TestFlight
   on the owner's iPhone and record the exact installed version/build as a separate device receipt.
7. For family and friends who are not App Store Connect users, first require
   `friends_family_inputs_ready=true`; then create an External Testing
   group, add the same build, provide What to Test plus real feedback/contact information,
   submit it to TestFlight Beta App Review, then invite them only after approval.

## What still blocks device proof and broader distribution

- The archived app's `https://voltago.app` backend is now live through the dedicated Volta tunnel;
  its required UI/API/legal/PWA routes return HTTP 200 and the exact iOS User-Agent reaches the
  chargers API successfully.
- The registered iPhone still needs to install/update build `202608231930` by opening the
  TestFlight app and tapping Update.
- Distinct final beta Privacy, Terms, and Support pages exist in the release tree and archive, and
  their public routes now return HTTP 200.
- External testing has not passed TestFlight Beta App Review. No external-test or public-release
  action is implied by the completed Internal Testing assignment.

The physical charger field test and full native VoiceOver traversal remain real product-quality
gates, but they do not block a limited TestFlight beta. Their status must remain honest and they
still block a public App Store completion claim.
