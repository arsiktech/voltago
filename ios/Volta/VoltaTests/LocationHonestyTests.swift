import XCTest
@testable import Volta

/// hasExactDirectoryLocation (Models.swift): a Directory-tier row may only be pinned on the
/// map as an exact location when its primary source is provably an exact station/detail page
/// (an allowlisted `kind`, or a matching Electromaps exact-page URL for one of the four
/// supported languages) -- never a generic list/locator page. Mirrors
/// tests/test_honesty_parity.py's ExactDirectoryKindListParityTests /
/// ElectromapsExactPageLanguageParityTests as runtime assertions on the decoded type.
final class HasExactDirectoryLocationTests: XCTestCase {
    func testAllowedKindOnADirectoryRowIsExact() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true, "reliability": "directory",
         "verification": {"location_verified": true},
         "primary_source": {"kind": "station_page", "url": "https://example.com/x"}}
        """)
        XCTAssertTrue(st.hasExactDirectoryLocation)
    }

    func testElectromapsExactPageURLWithAnAllowedLanguageIsExact() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true, "reliability": "directory",
         "verification": {"location_verified": true},
         "primary_source": {"kind": "generic_page",
                             "url": "https://www.electromaps.com/en/p/12345-somewhere"}}
        """)
        XCTAssertTrue(st.hasExactDirectoryLocation)
    }

    func testElectromapsURLWithANonAllowedLanguageIsNotExact() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true, "reliability": "directory",
         "verification": {"location_verified": true},
         "primary_source": {"kind": "generic_page",
                             "url": "https://www.electromaps.com/de/p/12345-somewhere"}}
        """)
        XCTAssertFalse(st.hasExactDirectoryLocation)
    }

    func testNonAllowedKindWithNoMatchingURLIsNotExact() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true, "reliability": "directory",
         "verification": {"location_verified": true},
         "primary_source": {"kind": "generic_page", "url": "https://example.com/other"}}
        """)
        XCTAssertFalse(st.hasExactDirectoryLocation)
    }

    func testANonDirectoryTierIsNeverExactRegardlessOfSourceQuality() throws {
        // Guard clause: hasExactDirectoryLocation requires tierLabel == "Directory" first.
        // A live (non-lead) row is tier "Live", so this must be false even with a real
        // Electromaps exact-page URL and an allowed kind.
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "verification": {"location_verified": true},
         "primary_source": {"kind": "station_page",
                             "url": "https://www.electromaps.com/en/p/1-x"}}
        """)
        XCTAssertFalse(st.hasExactDirectoryLocation)
    }
}

/// isMapLocationVerified (Models.swift): the stricter map-PIN gate (a pin visually claims an
/// exact location; a list row can show a weaker lead with honest copy instead). Covers the
/// route-checked shortcut, the non-lead default-trust branch, and the "official" branch
/// (official registry coordinates earn a pin once the location is verified, with or without a
/// published tariff -- the price stays off the map until `price_verified`).
final class IsMapLocationVerifiedTests: XCTestCase {
    func testOfficialTierWithLocationAndPriceVerifiedIsMapVerified() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true,
         "reliability": "official_directory",
         "verification": {"location_verified": true, "price_verified": true}}
        """)
        XCTAssertTrue(st.isMapLocationVerified)
    }

    /// Since "Keep every located charger on the map" (a3dad42), an official registry site
    /// with a verified location is pinned even without a published price; the pin then shows
    /// no price, because `hasVerifiedPrice` still requires `price_verified`.
    func testOfficialTierWithVerifiedLocationButNoVerifiedPriceIsPinnedWithoutAPrice() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true,
         "reliability": "official_directory",
         "verification": {"location_verified": true}}
        """)
        XCTAssertTrue(st.isMapLocationVerified)
        XCTAssertFalse(st.hasVerifiedPrice)
    }

    func testOfficialTierWithoutVerifiedLocationIsNotMapVerified() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true,
         "reliability": "official_directory",
         "verification": {"location_verified": false, "price_verified": true}}
        """)
        XCTAssertFalse(st.isMapLocationVerified)
    }

    func testNonLeadRowWithNoVerificationDataDefaultsToTrusted() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false}
        """)
        XCTAssertTrue(st.isMapLocationVerified)
    }

    func testNonLeadRowExplicitlyMarkedLocationUnverifiedIsNotMapVerified() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "verification": {"location_verified": false}}
        """)
        XCTAssertFalse(st.isMapLocationVerified)
    }

    func testRouteCheckedShortCircuitsToVerifiedRegardlessOfTier() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true, "reliability": "community",
         "verification": {"route_checked": true}}
        """)
        XCTAssertTrue(st.isMapLocationVerified)
    }
}
