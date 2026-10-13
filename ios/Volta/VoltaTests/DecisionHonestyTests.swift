import CoreLocation
import Foundation
import MapKit
import XCTest
@testable import Volta

private struct StationTruthCorpus: Decodable {
    struct StationCase: Decodable {
        var id: String
        var station: Station
        var expected: Bool
    }
    struct DecisionCase: Decodable {
        var id: String
        var station: Station
        var expected: String
    }
    struct URLCase: Decodable {
        var value: String
        var valid: Bool
    }

    var routeReady: [StationCase]
    var decisionFallback: [DecisionCase]
    var publicWebUrls: [URLCase]

    static func load(file: StaticString = #filePath, line: UInt = #line) throws -> Self {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let url = root.appendingPathComponent("contracts/station_truth_cases.json")
        let data = try Data(contentsOf: url)
        return try TestDecode.decoder().decode(Self.self, from: data)
    }
}

/// decisionKind (Models.swift): a real backend `decision.level` always maps through; an
/// unknown or missing level falls back honestly by lead status ("check" for a live row,
/// "verify" for a lead -- a lead is inherently less certain, so its fallback must be the more
/// cautious of the two). Mirrors tests/test_honesty_parity.py's
/// DecisionFallbackLeadAwareParityTests, but as a real runtime assertion on the decoded type
/// rather than a text-contract on the Swift source.
final class DecisionKindFallbackTests: XCTestCase {
    func testUnknownLevelOnANonLeadFallsBackToCheck() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "decision": {"level": "totally_unknown"}}
        """)
        XCTAssertEqual(st.decisionKind, .check)
    }

    func testMissingDecisionOnALeadFallsBackToVerify() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true}
        """)
        XCTAssertEqual(st.decisionKind, .verify)
    }

    func testMissingDecisionOnANonLeadFallsBackToCheck() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2}
        """)
        XCTAssertEqual(st.decisionKind, .check)
    }

    func testAPresentValidLevelMapsThroughRegardlessOfLeadStatus() throws {
        let go = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true,
         "decision": {"level": "go"}}
        """)
        XCTAssertEqual(go.decisionKind, .go)

        let avoid = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "decision": {"level": "avoid"}}
        """)
        XCTAssertEqual(avoid.decisionKind, .avoid)
    }
}

/// isRouteReady (Models.swift): the "celebrate this / it's actually route-ready now" gate.
/// A true route-ready row must be live (never a lead/directory row), decided "go", AND have a
/// free connector right now -- all three, not any one alone.
final class IsRouteReadyTests: XCTestCase {
    func testLanguageNeutralStationTruthCorpus() throws {
        for item in try StationTruthCorpus.load().routeReady {
            XCTAssertEqual(item.station.isRouteReady, item.expected, item.id)
        }
    }
    func testLiveGoWithAvailableConnectorSummaryIsRouteReady() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "verification": {"live_verified": true, "level": "live"},
         "decision": {"level": "go"}, "connector_summary": {"available": 2}}
        """)
        XCTAssertTrue(st.isRouteReady)
    }

    func testALeadIsNeverRouteReadyEvenWhenGoAndAvailable() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": true,
         "verification": {"live_verified": true, "level": "live"},
         "decision": {"level": "go"}, "connector_summary": {"available": 2}}
        """)
        XCTAssertFalse(st.isRouteReady)
    }

    func testGoWithZeroAvailableConnectorsIsNotRouteReady() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "verification": {"live_verified": true, "level": "live"},
         "decision": {"level": "go"}, "connector_summary": {"available": 0}}
        """)
        XCTAssertFalse(st.isRouteReady)
    }

    func testCheckDecisionIsNeverRouteReadyEvenWithAvailability() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "verification": {"live_verified": true, "level": "live"},
         "decision": {"level": "check"}, "available": 5}
        """)
        XCTAssertFalse(st.isRouteReady)
    }

    func testAvailableFallsBackToTopLevelAvailableWhenNoConnectorSummary() throws {
        let st = try TestDecode.station("""
        {"id": "s", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "verification": {"live_verified": true, "level": "live"},
         "decision": {"level": "go"}, "available": 3}
        """)
        XCTAssertTrue(st.isRouteReady)
    }

    func testAvailableStatusQualifiesOnlyWhenNumericAvailabilityIsAbsent() throws {
        let statusOnly = try TestDecode.station("""
        {"id": "status-only", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "verification": {"live_verified": true, "level": "live"},
         "decision": {"level": "go"}, "status": " available "}
        """)
        let explicitZero = try TestDecode.station("""
        {"id": "explicit-zero", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "verification": {"live_verified": true, "level": "live"},
         "decision": {"level": "go"}, "available": 0, "status": "AVAILABLE"}
        """)

        XCTAssertTrue(statusOnly.isRouteReady)
        XCTAssertFalse(explicitZero.isRouteReady)
    }

    func testExplicitLiveVerificationContradictionsFailClosed() throws {
        let explicitlyNotLive = try TestDecode.station("""
        {"id": "not-live", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "decision": {"level": "go"}, "available": 3,
         "verification": {"live_verified": false, "level": "live"}}
        """)
        let nonLiveLevel = try TestDecode.station("""
        {"id": "wrong-level", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "decision": {"level": "go"}, "available": 3,
         "verification": {"live_verified": true, "level": "official"}}
        """)
        let malformedLiveLevel = try TestDecode.station("""
        {"id": "malformed-level", "name": "N", "lat": 1, "lon": 2, "lead": false,
         "decision": {"level": "go"}, "available": 3,
         "verification": {"live_verified": true, "level": " LIVE "}}
        """)

        XCTAssertFalse(explicitlyNotLive.isRouteReady)
        XCTAssertFalse(nonLiveLevel.isRouteReady)
        XCTAssertFalse(malformedLiveLevel.isRouteReady)
    }
}

final class StationTruthClientBoundaryTests: XCTestCase {
    func testDecisionFallbackCorpus() throws {
        for item in try StationTruthCorpus.load().decisionFallback {
            XCTAssertEqual(item.station.decisionKind.rawValue, item.expected, item.id)
        }
    }

    func testPublicWebURLCorpus() throws {
        for item in try StationTruthCorpus.load().publicWebUrls {
            XCTAssertEqual(PublicWebURL.validated(item.value) != nil, item.valid, item.value)
        }
    }
}

/// AppState.filteredRows is the public list seam used by SheetView. It contains exact verified
/// stations plus the stricter route-ready subset, AND (WS-1, 2026-07-18) the source-backed
/// free-charger leads that are the app's reason to exist — leads appear clearly "Not live", are
/// still gated to a map pin by isMapLocationVerified, and never become route-ready. Filters may
/// narrow the set; a lead with no confirmable price is excluded by the price gate.
@MainActor
final class PrimaryStationFilteringTests: XCTestCase {
    func testPrimaryListShowsVerifiedStationsAndFreeLeadsButNeverPromotesLeads() throws {
        let routeReady = try TestDecode.station("""
        {"id": "route-ready", "name": "Ready", "lat": 39.47, "lon": -0.37, "price_min": 0.20,
         "lead": false, "decision": {"level": "go"},
         "verification": {"live_verified": true, "level": "live"},
         "connector_summary": {"available": 2}}
        """)
        let checkFirst = try TestDecode.station("""
        {"id": "check-first", "name": "Check", "lat": 39.47, "lon": -0.37,
         "lead": false, "price_min": 0.20, "decision": {"level": "check"}, "available": 3,
         "verification": {"live_verified": true, "location_verified": true, "level": "live"}}
        """)
        let verifyFirst = try TestDecode.station("""
        {"id": "verify-first", "name": "Verify", "lat": 39.47, "lon": -0.37,
         "lead": false, "decision": {"level": "verify"}, "available": 3}
        """)
        let unavailableGo = try TestDecode.station("""
        {"id": "unavailable-go", "name": "Busy", "lat": 39.47, "lon": -0.37,
         "lead": false, "decision": {"level": "go"}, "available": 0}
        """)
        let explicitlyNotLive = try TestDecode.station("""
        {"id": "not-live", "name": "Not live", "lat": 39.47, "lon": -0.37,
         "lead": false, "decision": {"level": "go"}, "available": 3,
         "verification": {"live_verified": false, "level": "live"}}
        """)
        let nonLiveLevel = try TestDecode.station("""
        {"id": "wrong-level", "name": "Wrong level", "lat": 39.47, "lon": -0.37,
         "lead": false, "decision": {"level": "go"}, "available": 3,
         "verification": {"live_verified": true, "level": "official"}}
        """)
        // A free source-backed lead (the free-charger DB) now appears — as "Not live", never
        // Go-now. It has a confirmable free price so it survives the price gate.
        let freeLead = try TestDecode.station("""
        {"id": "free-lead", "name": "Free lead", "lat": 39.47, "lon": -0.37,
         "lead": true, "is_free": true, "price_min": 0, "decision": {"level": "verify"}}
        """)
        // A lead with no confirmable price is still excluded (free-first price gate), even though
        // its (source-of-truth-irrelevant) decision says "go".
        let pricelessLead = try TestDecode.station("""
        {"id": "priceless-lead", "name": "Priceless", "lat": 39.47, "lon": -0.37,
         "lead": true, "decision": {"level": "go"}, "available": 3}
        """)

        let state = AppState()
        state.stations = [routeReady, checkFirst, verifyFirst, unavailableGo, explicitlyNotLive, nonLiveLevel]
        state.leads = [freeLead, pricelessLead]
        XCTAssertEqual(state.filteredRows.map(\.id), ["route-ready", "check-first", "verify-first", "unavailable-go", "not-live", "wrong-level", "free-lead", "priceless-lead"])
        // A lead is never promoted to route-ready / celebration merely by being shown.
        XCTAssertFalse(freeLead.isRouteReady)
        XCTAssertEqual(freeLead.trustKind, .lead)
    }

    func testConnectorPowerAndPriceFiltersNarrowRouteReadyRows() throws {
        let matching = try TestDecode.station("""
        {"id":"matching","name":"Matching","lat":39.47,"lon":-0.37,"lead":false,
         "price_min":0.20,"decision":{"level":"go"},"available":1,
         "connectors":[{"type":"CCS2","power_kw":150}]}
        """)
        let slow = try TestDecode.station("""
        {"id":"slow","name":"Slow","lat":39.47,"lon":-0.37,"lead":false,
         "price_min":0.10,"decision":{"level":"go"},"available":1,
         "connectors":[{"type":"Type 2","power_kw":22}]}
        """)
        let state = AppState()
        state.stations = [matching, slow]
        state.maxPrice = 0.20
        state.connectorType = "CCS2"
        state.minimumPowerKW = 50
        XCTAssertEqual(state.filteredRows.map(\.id), ["matching"])
    }

    func testConnectorMultiSelectMatchesAnySelectedConnector() throws {
        let ccs = try TestDecode.station("""
        {"id":"ccs","name":"CCS","lat":39.47,"lon":-0.37,"lead":false,
         "price_min":0.20,"decision":{"level":"go"},"available":1,
         "connectors":[{"type":"CCS2","power_kw":150}]}
        """)
        let chademo = try TestDecode.station("""
        {"id":"chademo","name":"CHAdeMO","lat":39.47,"lon":-0.37,"lead":false,
         "price_min":0.20,"decision":{"level":"go"},"available":1,
         "connectors":[{"type":"CHAdeMO","power_kw":50}]}
        """)
        let type2 = try TestDecode.station("""
        {"id":"type2","name":"Type 2","lat":39.47,"lon":-0.37,"lead":false,
         "price_min":0.20,"decision":{"level":"go"},"available":1,
         "connectors":[{"type":"Type 2","power_kw":22}]}
        """)
        let state = AppState()
        state.stations = [ccs, chademo, type2]
        state.connectorTypes = ["CCS2", "CHAdeMO"]

        XCTAssertEqual(state.filteredRows.map(\.id), ["ccs", "chademo"])
        state.toggleConnectorType("CCS2")
        XCTAssertEqual(state.filteredRows.map(\.id), ["chademo"])
        state.toggleConnectorType("CHAdeMO")
        XCTAssertEqual(state.filteredRows.map(\.id), ["ccs", "chademo", "type2"])
    }

    func testSavedOnlyFilterHidesUnsavedChargersWithoutInventingRows() throws {
        let saved = try TestDecode.station("""
        {"id":"saved","name":"Saved","lat":39.47,"lon":-0.37,"lead":false,
         "price_min":0,"decision":{"level":"go"},"available":1}
        """)
        let other = try TestDecode.station("""
        {"id":"other","name":"Other","lat":39.47,"lon":-0.37,"lead":false,
         "price_min":0,"decision":{"level":"go"},"available":1}
        """)
        let state = AppState()
        state.stations = [saved, other]
        state.favoriteIDs = ["saved"]
        XCTAssertEqual(state.filteredRows.map(\.id), ["saved", "other"])
        state.showingSavedOnly = true
        XCTAssertEqual(state.filteredRows.map(\.id), ["saved"])
        state.favoriteIDs = ["missing-id"]
        XCTAssertEqual(state.filteredRows.map(\.id), [])
    }
}

final class AccessGuidanceTests: XCTestCase {
    func testRestrictedAccessDoesNotInventAStartMethod() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"lead":false,
         "access":{"level":"customer","label":"Customers only","note":"Shop parking."}}
        """)
        XCTAssertEqual(station.accessGuidance.title, "Customers only")
        XCTAssertTrue(station.accessGuidance.detail.contains("Shop parking."))
    }

    func testUnknownStartStaysUnknown() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"lead":true}
        """)
        XCTAssertEqual(station.accessGuidance.title, "How to start is unknown")
    }
}

/// Exercises the max-price fetch seam through a hermetic URLSession boundary.
@MainActor
final class FilteredAPIRequestTests: XCTestCase {
    func testRequestPassesSelectedMaximumPriceWithoutAvailableShortcut() async throws {
        let url = try await capturedLiveURL(maxPrice: 0.15)
        let query = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems)
        XCTAssertEqual(query.first(where: { $0.name == "max_price" })?.value, "0.15")
        XCTAssertNil(query.first(where: { $0.name == "available" }))
    }

    private func capturedLiveURL(maxPrice: Double) async throws -> URL {
        let requestSeen = expectation(description: "live charger request")
        var capturedURL: URL?
        OpenNowURLProtocol.handler = { request in
            capturedURL = request.url
            requestSeen.fulfill()
            let response = try XCTUnwrap(
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 200,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["Content-Type": "application/json"]
                )
            )
            return (response, Data(#"{"stations":[],"leads":[],"meta":{"coverage":{"mode":"standard"}}}"#.utf8))
        }
        defer { OpenNowURLProtocol.handler = nil }

        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [OpenNowURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }

        let api = VoltaAPI(baseURL: URL(string: "https://volta.test")!, session: session)
        let state = AppState(api: api)
        state.visibleRegion = MKCoordinateRegion(center: .init(latitude: 39.47, longitude: -0.37),
                                                span: .init(latitudeDelta: 0.16, longitudeDelta: 0.16))
        state.maxPrice = maxPrice
        state.fetchLive(reason: "test")

        await fulfillment(of: [requestSeen], timeout: 2)
        return try XCTUnwrap(capturedURL)
    }
}

@MainActor
final class StationFocusTests: XCTestCase {
    func testFocusingAStationSelectsItForTheCarouselAndCanCenterTheMap() throws {
        let station = try TestDecode.station("""
        {"id":"focus-target","name":"Focus target","lat":39.4699,"lon":-0.3763,
         "price_min":0.20,"lead":false,"decision":{"level":"go"},"available":1}
        """)
        let state = AppState()
        state.visibleRegion = .init(
            center: .init(latitude: 40, longitude: -3),
            span: .init(latitudeDelta: 0.4, longitudeDelta: 0.3)
        )

        state.focus(station, centerMap: true)

        XCTAssertEqual(state.focusedStationID, station.id)
        XCTAssertEqual(state.visibleRegion.center.latitude, station.lat, accuracy: 0.000_001)
        XCTAssertEqual(state.visibleRegion.center.longitude, station.lon, accuracy: 0.000_001)
        XCTAssertEqual(state.visibleRegion.span.latitudeDelta, 0.4, accuracy: 0.000_001)
    }
}

/// Map clusters are an observable map/accessibility summary. An available check-first row
/// must not set or announce route readiness (the separate free-price signal may still be
/// present); connector-summary-backed Go-now rows must qualify.
final class MapClusterHonestyTests: XCTestCase {
    func testClusterRouteReadySummaryUsesTheStationSafetyContract() throws {
        let availableCheck = try TestDecode.station("""
        {"id": "check", "name": "Check", "lat": 39.47, "lon": -0.37,
         "lead": false, "decision": {"level": "check"}, "available": 3,
         "is_free": true}
        """)
        let routeReady = try TestDecode.station("""
        {"id": "ready", "name": "Ready", "lat": 39.47, "lon": -0.37,
         "lead": false, "decision": {"level": "go"},
         "verification": {"live_verified": true, "level": "live"},
         "connector_summary": {"available": 2}, "is_free": true}
        """)
        let freeLead = try TestDecode.station("""
        {"id": "free-lead", "name": "Lead", "lat": 39.47, "lon": -0.37,
         "lead": true, "decision": {"level": "verify"}, "is_free": true}
        """)

        let checkCluster = MapCluster(stations: [availableCheck])
        XCTAssertFalse(checkCluster.hasRouteReady)
        XCTAssertFalse(checkCluster.hasFreeLead)
        XCTAssertFalse(checkCluster.accessibilityValue.contains("route-ready"))
        XCTAssertFalse(checkCluster.accessibilityValue.contains("lead"))

        let readyCluster = MapCluster(stations: [availableCheck, routeReady])
        XCTAssertTrue(readyCluster.hasRouteReady)
        XCTAssertTrue(readyCluster.accessibilityValue.contains("route-ready"))

        let leadCluster = MapCluster(stations: [freeLead])
        XCTAssertTrue(leadCluster.hasFreeLead)
        XCTAssertTrue(leadCluster.accessibilityValue.contains("free charging lead"))
    }
}

private final class OpenNowURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with _: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            let handler = try XCTUnwrap(Self.handler)
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
