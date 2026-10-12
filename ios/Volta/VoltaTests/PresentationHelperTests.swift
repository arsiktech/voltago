import XCTest
@testable import Volta

final class StationParkingAccessTests: XCTestCase {
    func testFreeChargingNeverInventsFreeParking() throws {
        let station = try TestDecode.station("""
        {"id":"free-charge","name":"N","lat":1,"lon":2,"is_free":true,"price_min":0,
         "charge_claim":{"type":"free","price_min":0,"source":"Charging operator"}}
        """)
        XCTAssertEqual(station.parkingSummary.text, "Parking cost unknown")
        XCTAssertNil(station.accessRestrictionSummary)
    }

    func testPaidFalseWithUnknownKindStillMeansUnknownCost() throws {
        let station = try TestDecode.station("""
        {"id":"unknown","name":"N","lat":1,"lon":2,
         "parking":{"kind":"unknown","paid":false}}
        """)
        XCTAssertEqual(station.parkingSummary.text, "Parking cost unknown")
    }

    func testFreeChargingAndPaidParkingStaySeparate() throws {
        let station = try TestDecode.station("""
        {"id":"paid-parking","name":"N","lat":1,"lon":2,"is_free":true,
         "parking":{"kind":"paid_or_metered","paid":true},
         "parking_claim":{"kind":"paid_or_metered","source":"Car park operator"}}
        """)
        XCTAssertEqual(station.priceText, "Free")
        XCTAssertEqual(station.parkingSummary.text, "Paid parking")
        XCTAssertTrue(station.parkingSummary.isCaution)
        XCTAssertEqual(station.parkingClaim?.source, "Car park operator")
    }

    func testOnlyExplicitFreeParkingClaimReportsFreeParking() throws {
        let station = try TestDecode.station("""
        {"id":"free-parking","name":"N","lat":1,"lon":2,"price_min":0.35,
         "parking_claim":{"kind":"free_parking","source":"Town council"}}
        """)
        XCTAssertEqual(station.parkingSummary.text, "Parking reported free")
        XCTAssertFalse(station.parkingSummary.isCaution)
        XCTAssertEqual(station.priceText, "€0.35")
    }

    func testUnknownProfileDoesNotInheritAContradictoryFreeClaim() throws {
        let station = try TestDecode.station("""
        {"id":"conflict","name":"N","lat":1,"lon":2,
         "parking":{"kind":"unknown","paid":false},
         "parking_claim":{"kind":"free_parking"}}
        """)
        XCTAssertEqual(station.parkingSummary.text, "Parking cost unknown")
    }

    func testPaidAccessEvidenceKeepsTheParkingWarning() throws {
        let station = try TestDecode.station("""
        {"id":"paid-access","name":"N","lat":1,"lon":2,
         "parking":{"kind":"free_parking","paid":false},
         "access_claim":{"parking_paid":true}}
        """)
        XCTAssertEqual(station.parkingSummary.text, "Paid parking")
        XCTAssertTrue(station.parkingSummary.isCaution)
    }

    func testPaidParkingClaimDoesNotDisappearBesideFreeProfile() throws {
        let station = try TestDecode.station("""
        {"id":"paid-claim","name":"N","lat":1,"lon":2,
         "parking":{"kind":"free_parking","paid":false},
         "parking_claim":{"kind":"paid_or_metered","source":"Garage operator"}}
        """)
        XCTAssertEqual(station.parkingSummary.text, "Paid parking")
        XCTAssertTrue(station.parkingSummary.isCaution)
    }

    func testCustomerAndTimeLimitedParkingKeepTheirConditions() throws {
        for (kind, expected) in [
            ("retail_parking", "Customer parking · check terms"),
            ("facility_or_customer_parking", "Customer parking · check terms"),
            ("time_limited_or_validated", "Parking conditions apply")
        ] {
            let station = try TestDecode.station("""
            {"id":"conditional","name":"N","lat":1,"lon":2,
             "parking":{"kind":"\(kind)","paid":false}}
            """)
            XCTAssertEqual(station.parkingSummary.text, expected, kind)
            XCTAssertTrue(station.parkingSummary.isCaution, kind)
        }
    }

    func testStreetParkingDoesNotPromiseNoFee() throws {
        let station = try TestDecode.station("""
        {"id":"street","name":"N","lat":1,"lon":2,
         "parking":{"kind":"street_or_municipal","paid":false}}
        """)
        XCTAssertEqual(station.parkingSummary.text, "Street parking · check signs")
    }

    func testPrivateClaimRemainsVisibleBesidePublicProfile() throws {
        let station = try TestDecode.station("""
        {"id":"restricted","name":"N","lat":1,"lon":2,
         "access":{"level":"public"},"access_claim":{"private_or_restricted":true}}
        """)
        XCTAssertEqual(station.accessRestrictionSummary, "Private or restricted")
    }

    func testProfileAndClaimRestrictionsRemainVisible() throws {
        for (field, level, expected) in [
            ("access", "customer", "Customer access"),
            ("access", "restricted", "Restricted access"),
            ("access_claim", "private", "Restricted access"),
            ("access_claim", "customer", "Customer access")
        ] {
            let station = try TestDecode.station("""
            {"id":"access","name":"N","lat":1,"lon":2,"\(field)":{"level":"\(level)"}}
            """)
            XCTAssertEqual(station.accessRestrictionSummary, expected, "\(field) \(level)")
        }
    }
}

final class StationTitleTests: XCTestCase {
    func testMissingOperatorKeepsPlaceWithoutInventingProvider() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"City car park","lat":1,"lon":2,"tesla_ok":true}
        """)
        XCTAssertEqual(station.stationTitle, "City car park")
    }

    func testNullOperatorUsesOwnerAndPreservesIdentifierCasing() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"aB12Cd34","lat":1,"lon":2,"operator":null,
         "owner":{"name":"ENDESA X WAY, S.L."}}
        """)
        XCTAssertEqual(station.stationTitle, "ENDESA X WAY, S.L. · aB12Cd34")
    }

    func testBlankOperatorUsesOwner() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"City car park","lat":1,"lon":2,"operator":"   ",
         "owner":{"name":"  Iberdrola  "}}
        """)
        XCTAssertEqual(station.stationTitle, "Iberdrola · City car park")
    }

    func testExplicitOperatorWinsOverOwner() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"City car park","lat":1,"lon":2,"operator":"  bp   pulse  ",
         "owner":{"name":"Different owner"}}
        """)
        XCTAssertEqual(station.stationTitle, "bp pulse · City car park")
    }
}

/// corroborationCount (Models.swift): prefers the backend's own corroboration count, falls
/// back to counting raw source rows, and always floors at 1 (a station with zero counted
/// sources is still presented as having its own primary source).
final class CorroborationCountTests: XCTestCase {
    func testUsesIndependentSourcesWhenPresent() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "corroboration": {"independent_sources": 3}}
        """)
        XCTAssertEqual(st.corroborationCount, 3)
    }

    func testFallsBackToSourcesArrayCountWhenNoCorroboration() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2,
         "sources": [{"name": "a"}, {"name": "b"}]}
        """)
        XCTAssertEqual(st.corroborationCount, 2)
    }

    func testDefaultsToOneWithNeitherCorroborationNorSources() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2}
        """)
        XCTAssertEqual(st.corroborationCount, 1)
    }

    func testFloorsAtOneEvenWhenIndependentSourcesIsZero() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "corroboration": {"independent_sources": 0}}
        """)
        XCTAssertEqual(st.corroborationCount, 1)
    }
}

/// tierLabel (Models.swift): live / official / directory / community, mirroring the web's
/// sourceTier(). A non-lead row is always "Live" regardless of its reliability text.
final class TierLabelTests: XCTestCase {
    func testNonLeadIsAlwaysLiveRegardlessOfReliability() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false, "reliability": "community"}
        """)
        XCTAssertEqual(st.tierLabel, "Live")
    }

    func testLeadWithOfficialPrefixedReliabilityIsOfficial() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true,
         "reliability": "official_charger_data"}
        """)
        XCTAssertEqual(st.tierLabel, "Official")
    }

    func testLeadWithDirectoryReliabilityIsDirectory() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true,
         "reliability": "directory_listing"}
        """)
        XCTAssertEqual(st.tierLabel, "Directory")
    }

    func testLeadWithUnrecognizedReliabilityIsCommunity() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true,
         "reliability": "user_submitted"}
        """)
        XCTAssertEqual(st.tierLabel, "Community")
    }

    func testLeadWithMissingReliabilityIsCommunity() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true}
        """)
        XCTAssertEqual(st.tierLabel, "Community")
    }
}

/// priceText / priceUnit (Models.swift): "Free" (no unit) whenever isFree or priceMin==0; a
/// real price renders "€X.XX" + "/kWh"; no evidence at all renders an honest em dash, never a
/// fabricated number.
final class PriceTextTests: XCTestCase {
    func testExplicitlyFreeStationShowsFreeWithNoUnit() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "is_free": true}
        """)
        XCTAssertEqual(st.priceText, "Free")
        XCTAssertNil(st.priceUnit)
    }

    func testZeroPriceMinShowsFreeWithNoUnit() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "price_min": 0}
        """)
        XCTAssertEqual(st.priceText, "Free")
        XCTAssertNil(st.priceUnit)
    }

    func testAPositivePriceRendersEuroAmountWithPerKWhUnit() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "price_min": 0.21}
        """)
        XCTAssertEqual(st.priceText, "€0.21")
        XCTAssertEqual(st.priceUnit, "/kWh")
    }

    func testNoPriceEvidenceRendersAnEmDashWithNoUnit() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2}
        """)
        XCTAssertEqual(st.priceText, "—")
        XCTAssertNil(st.priceUnit)
    }

    func testUnverifiedExactOsmLeadCannotUseAMapPriceSurface() throws {
        let st = try TestDecode.station("""
        {"id": "osm", "name": "OSM", "lat": 1, "lon": 2, "lead": true,
         "is_free": true, "price_min": 0,
         "reliability": "directory", "verification": {"location_verified": true},
         "primary_source": {"url": "https://www.openstreetmap.org/node/42"}}
        """)
        XCTAssertTrue(st.isMapLocationVerified)
        XCTAssertFalse(st.hasVerifiedPrice)
    }

    func testExplicitPriceVerificationRetainsTheMapPriceSurface() throws {
        let st = try TestDecode.station("""
        {"id": "official", "name": "Official", "lat": 1, "lon": 2, "lead": true,
         "price_min": 0.21, "reliability": "official_directory",
         "verification": {"location_verified": true, "price_verified": true}}
        """)
        XCTAssertTrue(st.hasVerifiedPrice)
        XCTAssertEqual(st.priceText, "€0.21")
    }
}

/// freshnessText (Models.swift) boundary inputs, asserting the ACTUAL rendered strings from
/// the real implementation: <1 min -> "just now"; <60 min -> "N min ago"; <48 h -> "N h ago"
/// (rounded); >=48 h -> days, then months from 60 days and years from 730 days. All test
/// minute counts are exact multiples of 60
/// so the Double(mins)/60 division is exact (no floating-point rounding noise at the boundary
/// itself).
final class FreshnessTextTests: XCTestCase {
    func testZeroMinutesIsJustNow() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "freshness_minutes": 0}
        """)
        XCTAssertEqual(st.freshnessText, "just now")
    }

    func test59MinutesStaysInMinutesForm() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "freshness_minutes": 59}
        """)
        XCTAssertEqual(st.freshnessText, "59 min ago")
    }

    func test60MinutesCrossesIntoHoursForm() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "freshness_minutes": 60}
        """)
        XCTAssertEqual(st.freshnessText, "1 h ago")
    }

    func test47HoursStaysInHoursForm() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "freshness_minutes": 2820}
        """)
        XCTAssertEqual(st.freshnessText, "47 h ago")
    }

    func test48HoursCrossesIntoDaysForm() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "freshness_minutes": 2880}
        """)
        XCTAssertEqual(st.freshnessText, "2 d ago")
    }

    func test60DaysCrossesIntoMonthsForm() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "freshness_minutes": 86400}
        """)
        XCTAssertEqual(st.freshnessText, "2 mo ago")
    }

    func test730DaysCrossesIntoYearsForm() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "freshness_minutes": 1051200}
        """)
        XCTAssertEqual(st.freshnessText, "2 y ago")
    }

    func testMissingFreshnessMinutesIsNil() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2}
        """)
        XCTAssertNil(st.freshnessText)
    }
}
