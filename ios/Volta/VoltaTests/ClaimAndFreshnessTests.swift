import XCTest
@testable import Volta

/// charge_claim / parking_claim (Models.swift, P3.3): the backend's ensure_claim_contract()
/// ALWAYS attaches BOTH claims to every station/lead row (server.py ~750-795) -- charging-PRICE
/// provenance and parking-COST provenance are tracked SEPARATELY (AGENTS.md Prime Directive #1)
/// and may conflict (claim_conflict). Before this unit, Station had neither field, so
/// JSONDecoder silently dropped both keys -- only the conflict SUMMARY survived. These tests
/// prove the runtime decode now carries both claims, independently, each with its own source.
final class ClaimDecodeTests: XCTestCase {
    func testChargeAndParkingClaimsDecodeIndependentlyWithDistinctSourcesAndURLs() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2,
         "charge_claim": {"type": "free", "source": "Electromaps", "url": "https://electromaps.com/p/1",
                           "price_min": 0, "summary": "Free while shopping."},
         "parking_claim": {"kind": "paid_or_metered", "source": "OpenStreetMap",
                            "url": "https://openstreetmap.org/way/2", "cost_hint": "€1/h",
                            "summary": "Metered street parking."}}
        """)
        let charge = try XCTUnwrap(st.chargeClaim)
        XCTAssertEqual(charge.type, "free")
        XCTAssertEqual(charge.source, "Electromaps")
        XCTAssertEqual(charge.url, "https://electromaps.com/p/1")
        XCTAssertEqual(charge.priceMin, 0)
        XCTAssertEqual(charge.summary, "Free while shopping.")

        let parking = try XCTUnwrap(st.parkingClaim)
        XCTAssertEqual(parking.kind, "paid_or_metered")
        XCTAssertEqual(parking.source, "OpenStreetMap")
        XCTAssertEqual(parking.url, "https://openstreetmap.org/way/2")
        XCTAssertEqual(parking.costHint, "€1/h")
        XCTAssertEqual(parking.summary, "Metered street parking.")

        // Prime Directive #1: charge and parking provenance are tracked separately and must
        // never be merged into a single claim -- distinct sources prove they decoded into two
        // genuinely independent structs, not aliases of the same one.
        XCTAssertNotEqual(charge.source, parking.source)
        XCTAssertNotEqual(charge.url, parking.url)
    }

    func testClaimConflictSummaryDecodesAlongsideBothDistinctClaims() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2,
         "charge_claim": {"source": "Electromaps", "url": "https://electromaps.com/p/1"},
         "parking_claim": {"source": "OpenStreetMap", "url": "https://openstreetmap.org/way/2"},
         "claim_conflict": {"summary": "Charging price and parking condition come from different public sources; verify both before routing."}}
        """)
        XCTAssertNotNil(st.chargeClaim)
        XCTAssertNotNil(st.parkingClaim)
        XCTAssertEqual(
            st.claimConflict?.summary,
            "Charging price and parking condition come from different public sources; verify both before routing."
        )
    }

    func testRowWithNeitherClaimDecodesToNilWithoutCrashing() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2}
        """)
        XCTAssertNil(st.chargeClaim)
        XCTAssertNil(st.parkingClaim)
        XCTAssertNil(st.claimConflict)
    }

    func testClaimSourceLabelFallsBackToGenericSourceWhenSourceNameMissing() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2,
         "charge_claim": {"url": "https://example.com/x"}}
        """)
        let charge = try XCTUnwrap(st.chargeClaim)
        XCTAssertEqual(charge.sourceLabel, "source")
        XCTAssertEqual(charge.sourceURL?.absoluteString, "https://example.com/x")
    }

    func testClaimWithoutURLHasNilSourceURLButKeepsItsOwnSourceLabel() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2,
         "parking_claim": {"source": "Local knowledge", "summary": "Free curb parking."}}
        """)
        let parking = try XCTUnwrap(st.parkingClaim)
        XCTAssertNil(parking.sourceURL)
        XCTAssertEqual(parking.sourceLabel, "Local knowledge")
    }
}

/// Row-card freshness fact (DIR-03, P3.3): StationCard/AvailabilityLine must show freshness for
/// BOTH live and lead rows, gated only on freshnessText's presence -- NOT on isLead (matches the
/// web's `st.last_seen || st.freshness_minutes != null` gate in StationCard.tsx). Wording
/// distinguishes evidence-age-on-leads ("As of …") from live-freshness-on-live-rows ("Updated
/// …") per GOAL_CONTRACT.md's Trust UX bullet. freshnessRowText is the exact computed value the
/// row consumes (StationViews.swift's AvailabilityLine, `showFreshness: true` on StationCard).
final class FreshnessRowTextTests: XCTestCase {
    func testLeadWithFreshnessShowsAsOfPrefix() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true, "freshness_minutes": 180}
        """)
        XCTAssertTrue(st.isLead)
        XCTAssertEqual(st.freshnessRowText, "As of 3 h ago")
    }

    func testLiveRowWithFreshnessShowsUpdatedPrefix() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false, "freshness_minutes": 5}
        """)
        XCTAssertFalse(st.isLead)
        XCTAssertEqual(st.freshnessRowText, "Updated 5 min ago")
    }

    func testMissingLeadFlagDefaultsToLiveWording() throws {
        // isLead defaults to false when `lead` is absent (Models.swift: `lead ?? false`),
        // matching the server/web default for an ordinary live station row.
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "freshness_minutes": 45}
        """)
        XCTAssertFalse(st.isLead)
        XCTAssertEqual(st.freshnessRowText, "Updated 45 min ago")
    }

    func testNoFreshnessDataYieldsNilForBothLeadAndLiveRows() throws {
        let lead = try TestDecode.station("""
        {"id": "s1", "name": "N", "lat": 1, "lon": 2, "lead": true}
        """)
        let live = try TestDecode.station("""
        {"id": "s2", "name": "N", "lat": 1, "lon": 2, "lead": false}
        """)
        XCTAssertNil(lead.freshnessRowText)
        XCTAssertNil(live.freshnessRowText)
    }

    func testFreshnessRowTextTracksTheSameUnderlyingFreshnessTextString() throws {
        // The row fact never invents its own aging math -- it wraps the exact same
        // freshnessText a lead's detail view already trusts, just with a distinguishing prefix.
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true, "freshness_minutes": 2880}
        """)
        let fresh = try XCTUnwrap(st.freshnessText)
        XCTAssertEqual(st.freshnessRowText, "As of \(fresh)")
    }
}
