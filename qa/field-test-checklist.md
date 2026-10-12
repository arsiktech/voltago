# Volta Go Field-Test Checklist

Current runbook: 2026-07-15

Purpose: validate that the app can safely guide a Tesla driver to a free or very cheap charger outside home, without confusing leads, paid parking, or stale live status for route-ready availability.

## Preconditions

- Test from the installed Volta Go iPhone app on cellular data. The current release candidate
  points at `https://voltago.app`; verify that address in Settings before leaving.
- Generate a fresh production candidate packet immediately before departure:
  `python3 scripts/qa_field_test_packet.py --base-url https://voltago.app --out qa/field-test-candidates.md --check-links --require-candidate-match`.
- Run `python3 scripts/qa_field_packet_freshness.py` and stop if it does not report `ok=true`.
- Stop if the packet says `Candidate revision match: no`. A correct hostname is not enough: the
  deployed `/api/intel` recordset must match the repository candidate used for the field run.
- Copy the packet's matching `Expected local recordset SHA-256` into
  `field_test.recordset_sha256` in the manual evidence. Evidence from an older recordset does not
  satisfy TC-051 after a later rebuild or deployment.
- Bring the provider apps/accounts normally used by the driver, especially Gaia,
  Iberdrola/MapaREVE-backed apps, Apple/Google Maps, and any provider named by the current card.
- Do not start charging solely from Volta Go; always confirm in the provider app and onsite signage.

## Pass 1: Default Trusted View

1. Open the app fresh.
2. Confirm the source banner says whether data is live, degraded, or preview.
3. Confirm the first visible cards show Parking, Proof, Source, and a source/start button before lower-priority details.
4. Confirm any lead cards visibly say Not live and do not show a plain Navigate action.
5. Open Source details on the first trusted card and confirm there is at least one usable provider/source link.
6. Record the exact Volta card text for price, parking, source quality, and live/lead status before leaving.

Expected result: the driver can tell which entries are live versus source-backed leads before opening a map.

## Pass 2: Open Now

1. Tap Open now.
2. If cards appear, confirm every card is live/openish, not a lead, has easy/free parking, and shows Navigate plus a provider source/start link.
3. If no cards appear, confirm the app shows a clear no route-ready chargers message and does not leave stale lead cards visible.

Expected result: Open now is fail-closed. It either gives route-ready live candidates or says none are currently available.

## Pass 3: Source Launch

1. Open the provider/source link for the best candidate.
2. Confirm the provider/source was opened from that card's Source/Source details action, not from a separate search.
3. Confirm the provider page/app identifies the same charger/location.
4. Confirm the tariff is free or <= 0.25 EUR/kWh.
5. Confirm the parking terms are free/public, street, retail/customer with acceptable conditions, or otherwise clearly disclosed.

Expected result: Volta source facts match the provider/source page closely enough to decide whether to route.

## Pass 4: On-Site Reality

1. Navigate to the chosen charger.
2. Confirm whether the parking is free at arrival, including gates, validation, time limits, and paid-meter signs.
3. Confirm connector compatibility with the Tesla being tested.
4. Attempt a normal provider-app charging start.
5. Record actual price, parking cost, start success, occupancy, and any mismatch with Volta.

Expected result: at least one route chosen from the app can be reached, parked at acceptably, and started under the expected tariff.

## Evidence To Capture

- Fresh `qa/field-test-candidates.md` generated immediately before departure.
- The packet's matching recordset SHA copied into the manual field-evidence JSON.
- Screenshot of Volta default view.
- Screenshot of Volta Open now state.
- Screenshot of the chosen Volta card showing price, parking, source quality, and live/lead status.
- Screenshot of the chosen Volta card's Source details disclosure.
- Screenshot or deep link from the provider/source page.
- Google Maps arrival screenshot or address confirmation.
- Photo/note of onsite parking terms.
- Charging session start screenshot showing price or tariff.

## Current prepared evidence

The committed 2026-07-15 packet is a production-service snapshot. Until the owner deploys the
final Plan 011 commit and 659-record database, it must say `Candidate revision match: no` and is
**not departure-ready**. After deployment, regenerate with `--require-candidate-match`; only a
matching, fresh, link-valid packet prepares the drive. None of this is onsite evidence.

## Current Blocker

This checklist requires the owner, a real car, the installed iPhone app, provider accounts, and
physical/on-site validation. It cannot be completed from the repository runtime. Keep TC-051
blocked until the actual drive, parking, tariff, connector, and session-start evidence is attached.
