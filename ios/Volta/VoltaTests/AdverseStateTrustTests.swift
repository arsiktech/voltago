import CoreLocation
import Foundation
import MapKit
import XCTest
@testable import Volta

final class AdverseResponseDecodeTests: XCTestCase {
    func testResponsePreservesWarningCacheFlagsAndStationStaleness() throws {
        let response = try TestDecode.chargersResponse("""
        {
          "stations": [
            {"id":"stale","name":"Stale","lat":39.47,"lon":-0.37,
             "lead":false,"cache_stale":true,"available":2,
             "decision":{"level":"go"}}
          ],
          "leads": [],
          "warning": "Live refresh failed; serving stale cache.",
          "cache": {
            "hit": true,
            "stale": true,
            "live_failed": false,
            "fresh_live_authority": true,
            "mixed_live": true,
            "filter_fallback": true,
            "age_seconds": 91
          }
        }
        """)

        XCTAssertEqual(response.warning, "Live refresh failed; serving stale cache.")
        XCTAssertEqual(response.cache?.hit, true)
        XCTAssertEqual(response.cache?.stale, true)
        XCTAssertEqual(response.cache?.liveFailed, false)
        XCTAssertEqual(response.cache?.freshLiveAuthority, true)
        XCTAssertEqual(response.cache?.mixedLive, true)
        XCTAssertEqual(response.cache?.filterFallback, true)
        XCTAssertEqual(response.cache?.ageSeconds, 91)
        XCTAssertEqual(response.stations.first?.cacheStale, true)
    }
}

final class StationAdverseTrustTests: XCTestCase {
    func testStaleStationCanNeverRemainRouteReadyOrClaimLiveAvailability() throws {
        let station = try TestDecode.station("""
        {"id":"stale","name":"Stale","lat":39.47,"lon":-0.37,
         "lead":false,"cache_stale":true,"available":2,
         "connector_summary":{"available":2,"total":2},
         "decision":{"level":"go","label":"Go now"},
         "verification":{"level":"live","live_verified":true}}
        """)

        XCTAssertEqual(station.trustKind, .stale)
        XCTAssertFalse(station.isRouteReady)
        XCTAssertFalse(station.canShowLiveAvailability)
        XCTAssertFalse(station.canShowDecisionReason)
        XCTAssertEqual(station.decisionLabel, "Refresh first")
        XCTAssertEqual(station.tierLabel, "Stale")
    }

    func testUnavailableResponseContextOutranksAnOlderStaleRow() throws {
        let stale = try TestDecode.station("""
        {"id":"live","name":"Previously stale","lat":39.47,"lon":-0.37,
         "lead":false,"cache_stale":true,"available":2,
         "decision":{"level":"go","label":"Go now","reason":"Old optimistic reason"},
         "verification":{"level":"live","live_verified":true}}
        """)
        let unavailable = stale.withTrust(.unavailable)

        XCTAssertEqual(unavailable.trustKind, .unavailable)
        XCTAssertEqual(unavailable.tierLabel, "Unavailable")
        XCTAssertEqual(unavailable.decisionLabel, "Refresh first")
        XCTAssertNotEqual(unavailable.decisionKind, .go)
        XCTAssertFalse(unavailable.canShowLiveAvailability)
        XCTAssertFalse(unavailable.isRouteReady)
        XCTAssertFalse(unavailable.canShowDecisionReason)
    }

    func testDegradedResponseContextDemotesAnOtherwiseRouteReadyStation() throws {
        let decoded = try TestDecode.station("""
        {"id":"limited","name":"Limited","lat":39.47,"lon":-0.37,
         "lead":false,"available":1,"decision":{"level":"go"},
         "verification":{"level":"live","live_verified":true}}
        """)
        let station = decoded.withTrust(.degraded)

        XCTAssertEqual(station.trustKind, .degraded)
        XCTAssertFalse(station.isRouteReady)
        XCTAssertFalse(station.canShowLiveAvailability)
        XCTAssertEqual(station.decisionKind, .check)
        XCTAssertEqual(station.tierLabel, "Limited live")
    }

    func testAdverseTrustVerificationPresentationNeverEchoesOptimisticServerCopy() throws {
        let decoded = try TestDecode.station("""
        {"id":"verification","name":"Verification","lat":39.47,"lon":-0.37,
         "lead":false,"available":1,"decision":{"level":"go"},
         "verification":{"level":"live","label":"Live verified",
                         "note":"Includes current tariff/status fields.",
                         "live_verified":true}}
        """)

        let fresh = try XCTUnwrap(decoded.verificationPresentation)
        XCTAssertEqual(fresh.title, "Live verified")
        XCTAssertEqual(fresh.detail, "Includes current tariff/status fields.")

        for trust in [StationTrustKind.stale, .degraded, .unavailable] {
            let presentation = try XCTUnwrap(decoded.withTrust(trust).verificationPresentation)
            XCTAssertEqual(presentation.title, "Source evidence only")
            XCTAssertEqual(
                presentation.detail,
                "The source record is retained below, but it does not confirm the current tariff or status."
            )
            XCTAssertFalse(presentation.title.localizedCaseInsensitiveContains("Live verified"))
            XCTAssertFalse(presentation.detail?.localizedCaseInsensitiveContains("current tariff/status fields") == true)
        }
    }

    func testPublicSourceActionsRejectUnsafeSchemesAndUnboundedLabels() throws {
        let unsafe = SourceRef(
            name: "Unsafe",
            url: "javascript:alert(1)",
            evidence: nil,
            kind: nil,
            label: nil,
            action: "Open\nmalicious action",
            quality: nil,
            exactness: nil
        )
        XCTAssertNil(unsafe.safeURL)
        XCTAssertEqual(unsafe.safeActionLabel, "Verify at source")

        let safe = SourceRef(
            name: "MapaREVE",
            url: "https://example.com/station",
            evidence: nil,
            kind: nil,
            label: nil,
            action: "Open MapaREVE",
            quality: nil,
            exactness: nil
        )
        XCTAssertEqual(safe.safeURL?.absoluteString, "https://example.com/station")
        XCTAssertEqual(safe.safeActionLabel, "Open MapaREVE")
    }

    func testPublicSourceLicenseAttributionAndTransformationDecodeWithSafeLicenseLink() throws {
        let station = try TestDecode.station("""
        {"id":"licensed","name":"Licensed source","lat":39.47,"lon":-0.37,
         "sources":[{"name":"Madrid open data","url":"https://datos.madrid.es/dataset",
                     "license":"CC BY 4.0","license_url":"https://creativecommons.org/licenses/by/4.0/",
                     "attribution":"Ayuntamiento de Madrid",
                     "transformation":"Coordinates converted from ETRS89 / UTM30N.",
                     "source_fetched_at":"2026-07-15T08:00:00Z"}]}
        """)
        let source = try XCTUnwrap(station.sources?.first)
        XCTAssertEqual(source.license, "CC BY 4.0")
        XCTAssertEqual(source.attribution, "Ayuntamiento de Madrid")
        XCTAssertTrue(source.transformation?.contains("ETRS89") == true)
        XCTAssertEqual(source.sourceFetchedAt, "2026-07-15T08:00:00Z")
        XCTAssertEqual(source.safeLicenseURL?.host, "creativecommons.org")
    }

    func testPublicLiveSourcesPreserveThreeClocksWithoutInventingPrice() throws {
        let station = try TestDecode.station("""
        {"id":"existing-endolla","name":"Endolla","lat":41.4,"lon":2.1,
         "lead":false,"status":"AVAILABLE","available":1,"total_evse":2,
         "verification":{"live_verified":true,"level":"live"},
         "decision":{"level":"go","label":"Go now"},
         "sources":[{"name":"Barcelona Endolla live","attribution":"Ajuntament de Barcelona",
                     "license":"CC BY 4.0","license_url":"https://creativecommons.org/licenses/by/4.0/",
                     "source_fetched_at":"2026-10-20T16:30:00Z",
                     "source_published_at":"2026-10-20T16:29:00Z",
                     "source_last_updated":"2024-01-01T00:00:00Z"},
                    {"name":"Spirii AFIR","attribution":"Spirii; PowerGo"}]}
        """)
        let source = try XCTUnwrap(station.sources?.first)
        XCTAssertEqual(source.sourceFetchedAt, "2026-10-20T16:30:00Z")
        XCTAssertEqual(source.sourcePublishedAt, "2026-10-20T16:29:00Z")
        XCTAssertEqual(source.sourceLastUpdated, "2024-01-01T00:00:00Z")
        XCTAssertEqual(source.attribution, "Ajuntament de Barcelona")
        XCTAssertEqual(source.safeLicenseURL?.host, "creativecommons.org")
        XCTAssertEqual(station.sources?.last?.attribution, "Spirii; PowerGo")
        XCTAssertNil(station.priceMin)
        XCTAssertNil(station.tariffDetails)
        XCTAssertTrue(station.isRouteReady, "Last-change time is not a heartbeat")
        var stale = station
        stale.cacheStale = true
        XCTAssertFalse(stale.isRouteReady)
        XCTAssertFalse(stale.canShowLiveAvailability)
    }

    func testAllPublicHandoffSurfacesShareStrictHTTPURLValidation() throws {
        let unsafeValues = [
            "file:///tmp/private.html",
            "javascript:alert(1)",
            "data:text/plain,unsafe",
            "volta://station/123",
            "example.com/no-scheme",
            "https://user:password@example.com/private",
        ]
        for value in unsafeValues {
            XCTAssertNil(PublicWebURL.validated(value), "Accepted unsafe public URL: \(value)")
        }

        let source = SourceRef(
            name: "Unsafe",
            url: unsafeValues[0],
            evidence: nil,
            kind: nil,
            label: nil,
            action: "Open source",
            quality: nil,
            exactness: nil
        )
        XCTAssertNil(source.safeURL)

        let claim = Claim(
            type: "free",
            kind: nil,
            source: "Unsafe",
            url: unsafeValues[1],
            priceMin: 0,
            costHint: nil,
            summary: nil
        )
        XCTAssertNil(claim.sourceURL)

        let station = try TestDecode.station("""
        {"id":"unsafe-handoffs","name":"Unsafe handoffs","lat":39.47,"lon":-0.37,
         "source_url":"volta://station/123",
         "navigation":{"google":"example.com/no-scheme","apple":"file:///tmp/map"}}
        """)
        XCTAssertNil(station.sourceURL)
        XCTAssertEqual(station.mapsURL?.host, "maps.apple.com", "Unsafe navigation must use the safe fallback")

        XCTAssertEqual(PublicWebURL.validated("http://example.com/a")?.scheme, "http")
        XCTAssertEqual(PublicWebURL.validated("https://example.com/b")?.scheme, "https")

        let nonPublicOrigins = [
            "http://localhost:8773/private",
            "http://station.local/private",
            "http://10.0.0.1/private",
            "https://127.0.0.1/private",
            "https://169.254.169.254/latest/meta-data",
            "https://172.16.4.2/private",
            "https://192.0.2.1/documentation-only",
            "https://192.88.99.1/deprecated-relay",
            "https://192.168.1.1/private",
            "https://198.51.100.2/documentation-only",
            "https://203.0.113.3/documentation-only",
            "https://224.0.0.1/multicast",
            "https://[::1]/private",
            "http://0x7f.0.0.1/private",
            "http://0177.0.0.1/private",
            "https://bad..example.com/private",
            "https://-bad.example/private",
            "https://bad-.example/private",
            "https://intranet/private",
        ]
        for value in nonPublicOrigins {
            XCTAssertNil(PublicWebURL.validated(value), "Accepted non-public URL: \(value)")
        }
    }

    func testStationPhotosUseTheStrictPublicURLBoundaryBeforeAutomaticLoading() throws {
        let unsafeValues = [
            "javascript:alert(1)",
            "file:///tmp/private.jpg",
            "data:image/png;base64,AAAA",
            "volta-photo://station/123",
            "images.example.com/no-scheme.jpg",
            "https://user:password@example.com/private.jpg",
        ]
        for value in unsafeValues {
            let photo = StationPhoto(
                url: value,
                thumbnailUrl: nil,
                source: nil,
                sourceUrl: nil,
                caption: nil,
                license: nil,
                displayRightsApproved: true
            )
            XCTAssertNil(photo.safeURL, "Accepted unsafe automatic photo URL: \(value)")
        }

        let safeThumbnailFallback = StationPhoto(
            url: "javascript:alert(1)",
            thumbnailUrl: "https://images.example.com/safe-thumbnail.jpg",
            source: nil,
            sourceUrl: nil,
            caption: nil,
            license: nil,
            displayRightsApproved: true
        )
        XCTAssertEqual(
            safeThumbnailFallback.safeURL?.absoluteString,
            "https://images.example.com/safe-thumbnail.jpg"
        )

        let safePrimary = StationPhoto(
            url: "http://images.example.com/full.jpg",
            thumbnailUrl: "https://images.example.com/thumbnail.jpg",
            source: nil,
            sourceUrl: nil,
            caption: nil,
            license: nil,
            displayRightsApproved: true
        )
        XCTAssertEqual(safePrimary.safeURL?.absoluteString, "http://images.example.com/full.jpg")

        let unapproved = StationPhoto(
            url: "https://images.example.com/unapproved.jpg",
            thumbnailUrl: nil,
            source: nil,
            sourceUrl: nil,
            caption: nil,
            license: "unknown",
            displayRightsApproved: false
        )
        XCTAssertNil(unapproved.safeURL, "Technically safe media must remain hidden without rights approval")

        let station = try TestDecode.station("""
        {"id":"photo-boundary","name":"Photo boundary","lat":39.47,"lon":-0.37,
         "media":{"photos":[
           {"url":"file:///tmp/private.jpg","thumbnail_url":"data:image/png;base64,AAAA","display_rights_approved":true},
           {"url":"https://user:password@example.com/private.jpg","display_rights_approved":true},
           {"url":"javascript:alert(1)","thumbnail_url":"https://images.example.com/safe.jpg","display_rights_approved":true}
         ]}}
        """)
        XCTAssertEqual(
            station.firstPublicPhoto?.safeURL?.absoluteString,
            "https://images.example.com/safe.jpg",
            "The first public photo must skip unsafe entries and use a validated fallback"
        )
    }
}

@MainActor
final class AppStateAdverseTrustTests: XCTestCase {
    override func tearDown() {
        AdverseURLProtocol.handler = nil
        AdverseURLProtocol.delay = nil
        super.tearDown()
    }

    func testNearbyCatalogueAppearsBeforeSlowLiveProvidersEvenWithOldLeads() async throws {
        AdverseURLProtocol.delay = { Self.isOverviewRequest($0) ? 0.02 : 0.8 }
        let state = makeState { request in
            Self.response(request, status: 200, body: Self.isOverviewRequest(request)
                          ? Self.nearbyOverviewJSON : Self.freshLivePresentationJSON)
        }
        state.leads = try TestDecode.chargersResponse(Self.lateOverviewJSON).leads
        state.loadNearby(reason: "locate")
        await waitForOverview(state, expected: .loaded)

        XCTAssertEqual(state.liveState, .loading)
        XCTAssertEqual(state.listRows.map(\.id), ["nearby-known"])
        XCTAssertFalse(try XCTUnwrap(state.listRows.first).isRouteReady)
        XCTAssertFalse(try XCTUnwrap(state.listRows.first).canShowLiveAvailability)
        XCTAssertNil(state.routeMoment)
        await waitUntilSettled(state)
        XCTAssertEqual(state.leads.map(\.id), ["live-lead"])
    }

    func testNearbyCatalogueCannotReplaceAFasterLiveResponse() async {
        AdverseURLProtocol.delay = { Self.isOverviewRequest($0) ? 0.5 : 0.01 }
        let state = makeState { request in
            Self.response(request, status: 200, body: Self.isOverviewRequest(request)
                          ? Self.nearbyOverviewJSON : Self.freshLivePresentationJSON)
        }
        state.loadNearby(reason: "locate")
        await waitUntilSettled(state)
        await waitForOverview(state, expected: .loaded)
        XCTAssertEqual(state.overviewState, .loaded, "The delayed catalogue must actually finish")
        XCTAssertEqual(state.liveQuality, .fresh)
        XCTAssertEqual(state.leads.map(\.id), ["live-lead"])
        XCTAssertEqual(state.warning, "Fresh live response remains authoritative.")
    }

    func testMovingToAnotherAreaRejectsTheEarlierNearbyCatalogue() async throws {
        let firstStarted = expectation(description: "First area's catalogue requested")
        let isEarlier: (URLRequest) -> Bool = { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?
                .contains(where: { $0.name == "lat" && $0.value == "39.47" }) == true
        }
        AdverseURLProtocol.delay = { request in
            Self.isOverviewRequest(request) ? (isEarlier(request) ? 0.5 : 0.01) : 0.9
        }
        let state = makeState { request in
            if Self.isOverviewRequest(request) {
                if isEarlier(request) { firstStarted.fulfill() }
                let body = isEarlier(request) ? Self.nearbyOverviewJSON
                    : Self.nearbyOverviewJSON.replacingOccurrences(of: "nearby-known", with: "new-area-known")
                return Self.response(request, status: 200, body: body)
            }
            return Self.response(request, status: 200, body: Self.freshLivePresentationJSON)
        }
        state.userCoordinate = .init(latitude: 39.47, longitude: -0.37)
        state.loadNearby(reason: "locate")
        await fulfillment(of: [firstStarted], timeout: 2)
        state.userCoordinate = .init(latitude: 39.48, longitude: -0.37)
        state.visibleRegion.center = state.userCoordinate!
        state.loadNearby(reason: "locate")
        await waitForOverview(state, expected: .loaded)
        try await Task.sleep(for: .milliseconds(600))
        XCTAssertEqual(state.leads.map(\.id), ["new-area-known"])
        XCTAssertEqual(state.liveState, .loading)
        await waitUntilSettled(state)
    }

    func testFittedCameraBoundsKeepEdgeChargersAfterLiveCompletes() async throws {
        let requested = MKCoordinateRegion(center: .init(latitude: 39.47, longitude: -0.37),
                                          latitudinalMeters: 1_200, longitudinalMeters: 1_200)
        let fitted = MKCoordinateRegion(center: requested.center,
                                       span: .init(latitudeDelta: 0.04, longitudeDelta: 0.04))
        let edge = try TestDecode.station(#"{"id":"fitted-edge","name":"Edge charger","lat":39.48,"lon":-0.37,"lead":true}"#)
        XCTAssertFalse(requested.bbox.contains(edge))
        XCTAssertTrue(fitted.bbox.contains(edge))
        let initialStarted = expectation(description: "Initial area's live request")
        let fittedStarted = expectation(description: "Both queries cover the fitted region")
        fittedStarted.expectedFulfillmentCount = 2
        let isFitted: (URLRequest) -> Bool = { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?
                .first(where: { $0.name == "bbox" })?.value == fitted.bbox.queryValue
        }
        let edgeJSON = """
        {"stations":[],"leads":[{"id":"fitted-edge","name":"Edge charger",
          "lat":39.48,"lon":-0.37,"lead":true}],
         "warning":"Fitted area refreshed.","cache":{"fresh_live_authority":true}}
        """
        AdverseURLProtocol.delay = { request in
            isFitted(request) && !Self.isOverviewRequest(request) ? 0.4 : 0.01
        }
        let state = makeState { request in
            if isFitted(request) {
                fittedStarted.fulfill()
                return Self.response(request, status: 200, body: edgeJSON)
            }
            if !Self.isOverviewRequest(request) { initialStarted.fulfill() }
            return Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.visibleRegion = requested
        state.loadNearby(reason: "locate")
        await fulfillment(of: [initialStarted], timeout: 2)
        await waitUntilSettled(state)
        XCTAssertEqual(state.liveState, .ok)

        // The camera settles on a wider region after the initial lookup already finished.
        state.visibleRegion = fitted
        state.loadNearby(reason: "pan")
        await fulfillment(of: [fittedStarted], timeout: 2)
        await waitForOverview(state, expected: .loaded)
        XCTAssertEqual(state.listRows.map(\.id), [edge.id])
        XCTAssertEqual(state.liveState, .loading)
        XCTAssertFalse(try XCTUnwrap(state.listRows.first).isRouteReady)

        await waitUntilSettled(state)
        XCTAssertEqual(state.liveState, .ok)
        XCTAssertEqual(state.listRows.map(\.id), [edge.id])
        XCTAssertEqual(state.warning, "Fitted area refreshed.")
    }

    func testLocateReusesARecentFixAndDoesNotRestartTheSamePendingLookup() async throws {
        let manager = NearbyLocationManager()
        manager.cachedLocation = Self.location(age: 1, accuracy: 40)
        let catalogueStarted = expectation(description: "Nearby catalogue requested once")
        let liveStarted = expectation(description: "Live request started once")
        catalogueStarted.assertForOverFulfill = true
        liveStarted.assertForOverFulfill = true
        AdverseURLProtocol.delay = { Self.isOverviewRequest($0) ? 0.02 : 0.8 }
        let state = makeState(locationManager: manager) { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems
            XCTAssertEqual(query?.first(where: { $0.name == "lat" })?.value, "39.47")
            XCTAssertEqual(query?.first(where: { $0.name == "lon" })?.value, "-0.37")
            XCTAssertNotNil(query?.first(where: { $0.name == "bbox" }))
            if Self.isOverviewRequest(request) {
                catalogueStarted.fulfill()
                XCTAssertEqual(query?.first(where: { $0.name == "compact" })?.value, "true")
                XCTAssertNil(query?.first(where: { $0.name == "summary" }), "Native details still need provenance")
                return Self.response(request, status: 200, body: Self.nearbyOverviewJSON)
            }
            liveStarted.fulfill()
            return Self.response(request, status: 200, body: Self.freshLivePresentationJSON)
        }
        state.locate()
        state.locate()
        XCTAssertEqual(manager.locationRequests, 0)
        XCTAssertFalse(state.locating)
        XCTAssertEqual(state.locationState, .located)
        XCTAssertEqual(state.userCoordinate?.latitude, 39.47)
        await fulfillment(of: [catalogueStarted, liveStarted], timeout: 2)
        await waitForOverview(state, expected: .loaded)
        XCTAssertEqual(state.listRows.map(\.id), ["nearby-known"])
        XCTAssertEqual(state.liveState, .loading)
        await waitUntilSettled(state)
    }

    func testStartupRequestsFirstPermissionOnceWhileShowingSavedExploration() async {
        let manager = NearbyLocationManager()
        manager.testAuthorization = .notDetermined
        let overview = expectation(description: "Startup fallback loads once")
        overview.assertForOverFulfill = true
        let state = makeState(locationManager: manager) { request in
            XCTAssertTrue(Self.isOverviewRequest(request), "Live lookup needs a real location first")
            overview.fulfill()
            return Self.response(request, status: 200, body: Self.nearbyOverviewJSON)
        }
        state.start()
        state.start()
        XCTAssertEqual(manager.authorizationRequests, 1)
        XCTAssertEqual(manager.locationRequests, 0)
        XCTAssertTrue(state.locating)
        XCTAssertEqual(state.locationState, .requesting)
        await fulfillment(of: [overview], timeout: 2)
        await waitForOverview(state, expected: .loaded)
        XCTAssertEqual(state.mode, .overview)
        XCTAssertFalse(state.leads.isEmpty)
    }

    func testStartupUsesFreshLocationWithoutDuplicatingItsNearbyPipeline() async {
        let manager = NearbyLocationManager()
        manager.cachedLocation = Self.location(age: 1, accuracy: 40)
        let overview = expectation(description: "Nearby catalogue loads once")
        let live = expectation(description: "Nearby live query loads once")
        overview.assertForOverFulfill = true
        live.assertForOverFulfill = true
        let state = makeState(locationManager: manager) { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            XCTAssertEqual(query.first(where: { $0.name == "lat" })?.value, "39.47")
            if Self.isOverviewRequest(request) { overview.fulfill() }
            else { live.fulfill() }
            return Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.start()
        state.start()
        XCTAssertEqual(manager.locationRequests, 0)
        XCTAssertEqual(state.locationState, .located)
        XCTAssertEqual(state.mode, .live)
        XCTAssertEqual(state.visibleRegion.center.latitude, 39.47)
        await fulfillment(of: [overview, live], timeout: 2)
        await waitUntilSettled(state)
    }

    func testFirstPermissionGrantRequestsOneFixAndThenOpensNearbyChargers() async {
        let manager = NearbyLocationManager()
        manager.testAuthorization = .notDetermined
        let state = makeState(locationManager: manager) { request in
            Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.start()
        manager.testAuthorization = .authorizedWhenInUse
        state.locationManagerDidChangeAuthorization(manager)
        state.locationManagerDidChangeAuthorization(manager)
        for _ in 0 ..< 100 where manager.locationRequests == 0 {
            try? await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(manager.authorizationRequests, 1)
        XCTAssertEqual(manager.locationRequests, 1)
        state.locationManager(manager, didUpdateLocations: [Self.location(age: 0, accuracy: 40)])
        for _ in 0 ..< 100 where state.locationState != .located {
            try? await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(state.locationState, .located)
        XCTAssertEqual(state.mode, .live)
        XCTAssertEqual(state.visibleRegion.center.latitude, 39.47)
        await waitUntilSettled(state)
    }

    func testDeniedStartupKeepsExplorationUsableWithoutRequestingLocation() async {
        let manager = NearbyLocationManager()
        manager.testAuthorization = .denied
        let state = makeState(locationManager: manager) { request in
            XCTAssertTrue(Self.isOverviewRequest(request))
            return Self.response(request, status: 200, body: Self.nearbyOverviewJSON)
        }
        state.start()
        await waitForOverview(state, expected: .loaded)
        XCTAssertEqual(manager.authorizationRequests, 0)
        XCTAssertEqual(manager.locationRequests, 0)
        XCTAssertEqual(state.locationState, .denied)
        XCTAssertEqual(state.mode, .overview)
        XCTAssertFalse(state.leads.isEmpty)
    }

    func testManualPlaceWinsOverAnUnresolvedStartupLocation() async {
        let manager = NearbyLocationManager()
        let state = makeState(locationManager: manager) { request in
            Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.start()
        XCTAssertEqual(manager.locationRequests, 1)
        state.pick(GeocodeResult(label: "Madrid", name: "Madrid", lat: 40.4168, lon: -3.7038, bbox: nil))
        state.locationManager(manager, didUpdateLocations: [Self.location(age: 0, accuracy: 40)])
        await waitUntilSettled(state)
        XCTAssertEqual(manager.cancelledLocationRequests, 1)
        XCTAssertEqual(state.visibleRegion.center.latitude, 40.4168)
        XCTAssertEqual(state.visibleRegion.center.longitude, -3.7038)
        XCTAssertNil(state.userCoordinate, "A cancelled callback must not silently become a new nearby lookup")
        XCTAssertFalse(state.locating)
    }

    func testManualNavigationCancelsAnUnresolvedPermissionGrantWithoutRequestingAFix() async {
        let manager = NearbyLocationManager()
        manager.testAuthorization = .notDetermined
        let state = makeState(locationManager: manager) { request in
            Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.start()
        state.cancelLocationForManualNavigation()
        manager.testAuthorization = .authorizedWhenInUse
        state.locationManagerDidChangeAuthorization(manager)
        await waitForOverview(state, expected: .loaded)
        XCTAssertEqual(manager.authorizationRequests, 1)
        XCTAssertEqual(manager.locationRequests, 0)
        XCTAssertFalse(state.locating)
        XCTAssertEqual(state.mode, .overview)
    }

    func testStartupLocationFailureKeepsSavedExplorationAndAllowsRetry() async {
        let manager = NearbyLocationManager()
        let state = makeState(locationManager: manager) { request in
            Self.response(request, status: 200, body: Self.nearbyOverviewJSON)
        }
        state.start()
        state.locationManager(manager, didFailWithError: NSError(domain: kCLErrorDomain, code: CLError.Code.locationUnknown.rawValue))
        await waitForOverview(state, expected: .loaded)
        for _ in 0 ..< 100 where state.locationState != .failed {
            try? await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(state.locationState, .failed)
        XCTAssertFalse(state.leads.isEmpty)
        XCTAssertEqual(state.mode, .overview)
        state.locate()
        XCTAssertTrue(state.locating)
        XCTAssertEqual(manager.locationRequests, 2)
    }

    func testLocateRequestsNeighbourhoodAccuracyAndCoalescesTapsWithoutAFreshFix() async {
        let manager = NearbyLocationManager()
        manager.cachedLocation = Self.location(age: 60, accuracy: 40)
        let state = makeState(locationManager: manager) { request in
            Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.locate()
        state.locate()
        XCTAssertEqual(manager.desiredAccuracy, kCLLocationAccuracyHundredMeters)
        XCTAssertEqual(manager.locationRequests, 1)
        XCTAssertTrue(state.locating)
        XCTAssertEqual(state.locationState, .requesting)
        XCTAssertNil(state.userCoordinate)

        state.locationManager(manager, didUpdateLocations: [Self.location(age: 0, accuracy: 40)])
        await waitForOverview(state, expected: .loaded)
        XCTAssertEqual(state.locationState, .located)
        XCTAssertFalse(state.locating)
        XCTAssertEqual(state.userCoordinate?.latitude, 39.47)
        await waitUntilSettled(state)
    }

    func testCachedLocationMustBeRecentValidAndAccurateEnoughForTheNeighbourhood() {
        let now = Date()
        for (age, accuracy) in [(0.0, 40.0), (15, 500)] {
            XCTAssertTrue(AppState.canReuseLocation(Self.location(age: age, accuracy: accuracy, now: now), now: now))
        }
        for (age, accuracy) in [(16.0, 40.0), (-1, 40), (0, -1), (0, 501)] {
            XCTAssertFalse(AppState.canReuseLocation(Self.location(age: age, accuracy: accuracy, now: now), now: now))
        }
        let invalid = CLLocation(coordinate: .init(latitude: 91, longitude: 0), altitude: 0,
                                 horizontalAccuracy: 40, verticalAccuracy: 0, timestamp: now)
        XCTAssertFalse(AppState.canReuseLocation(invalid, now: now))
    }

    func testDeniedPermissionNeverReusesTheLocationCache() {
        let manager = NearbyLocationManager()
        manager.testAuthorization = .denied
        manager.cachedLocation = Self.location(age: 1, accuracy: 40)
        let state = makeState(locationManager: manager) { request in
            XCTFail("Denied permission must not start a nearby lookup")
            return Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.locate()
        XCTAssertEqual(manager.locationRequests, 0)
        XCTAssertEqual(state.locationState, .denied)
        XCTAssertFalse(state.locating)
        XCTAssertNil(state.userCoordinate)
    }

    func testCatalogueDoesNotSendAnOldLocationAfterPermissionIsRevoked() async {
        let manager = NearbyLocationManager()
        let state = makeState(locationManager: manager) { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
            XCTAssertFalse(query.contains(where: { $0.name == "lat" || $0.name == "lon" }))
            return Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.userCoordinate = .init(latitude: 39.47, longitude: -0.37)
        manager.testAuthorization = .denied
        await state.loadOverview()
        XCTAssertEqual(state.overviewState, .loaded)
    }

    func testSelectingAnOffscreenPinRefreshesItsAreaAndRejectsTheOldRequest() async throws {
        try await assertRecenterRefreshesArea(select: true)
    }

    func testFocusingAnOffscreenPinRefreshesItsAreaAndRejectsTheOldRequest() async throws {
        try await assertRecenterRefreshesArea(select: false)
    }

    private func assertRecenterRefreshesArea(select: Bool) async throws {
        let oldStarted = expectation(description: "Old area's live request started")
        let oldArea: (URLRequest) -> Bool = { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?
                .first(where: { $0.name == "bbox" })?.value?.hasPrefix("39.") == true
        }
        AdverseURLProtocol.delay = { oldArea($0) ? 0.5 : 0.01 }
        let state = makeState { request in
            if Self.isOverviewRequest(request) {
                return Self.response(request, status: 200, body: Self.emptyFreshJSON)
            }
            if oldArea(request) { oldStarted.fulfill() }
            let id = oldArea(request) ? "old-area-live" : "new-area-live"
            return Self.response(request, status: 200, body: """
            {"stations":[{"id":"\(id)","name":"Live charger","lat":41.39,"lon":2.17,
              "lead":false,"available":1,"decision":{"level":"go"},
              "verification":{"live_verified":true}}],"leads":[]}
            """)
        }
        state.loadNearby(reason: "locate")
        await fulfillment(of: [oldStarted], timeout: 2)
        let pin = try TestDecode.station(#"{"id":"new-area-live","name":"Pin","lat":41.39,"lon":2.17}"#)
        if select { state.select(pin, moveMap: true) }
        else { state.focus(pin, centerMap: true) }
        await waitUntilSettled(state)
        XCTAssertEqual(state.stations.map(\.id), ["new-area-live"])
        try await Task.sleep(for: .milliseconds(600))
        XCTAssertEqual(state.stations.map(\.id), ["new-area-live"])
        XCTAssertEqual(state.visibleRegion.center.latitude, 41.39)
    }

    private static func location(age: TimeInterval, accuracy: CLLocationAccuracy, now: Date = Date()) -> CLLocation {
        CLLocation(coordinate: .init(latitude: 39.47, longitude: -0.37), altitude: 0,
                   horizontalAccuracy: accuracy, verticalAccuracy: 0, timestamp: now.addingTimeInterval(-age))
    }

    private static let nearbyOverviewJSON = """
    {"stations":[],"leads":[{"id":"nearby-known","name":"Known nearby charger",
      "lat":39.47,"lon":-0.37,"lead":true,"is_free":true,"price_min":0,
      "reliability":"official_directory","verification":{"location_verified":true}}],
     "cache":{"overview":true},"meta":{"coverage":{"mode":"overview"}}}
    """

    func testLatestOverviewOwnsRowsWhenEarlierOverviewFinishesLast() async throws {
        await assertLatestOverviewWins(earlierStatus: 200)
    }

    func testLatestOverviewOwnsStatusWhenEarlierOverviewFailsLast() async throws {
        await assertLatestOverviewWins(earlierStatus: 503)
    }

    private func assertLatestOverviewWins(earlierStatus: Int) async {
        let firstStarted = expectation(description: "First overview reached the service")
        let isEarlier: (URLRequest) -> Bool = { request in
            URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
                .queryItems?.contains(where: { $0.name == "max_price" && $0.value == "0.2" }) == true
        }
        AdverseURLProtocol.delay = { isEarlier($0) ? 0.4 : 0 }
        let state = makeState { request in
            if isEarlier(request) {
                firstStarted.fulfill()
                return Self.response(request, status: earlierStatus, body: Self.lateOverviewJSON)
            }
            return Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }

        state.maxPrice = 0.2
        let earlier = Task { await state.loadOverview() }
        await fulfillment(of: [firstStarted], timeout: 2)
        state.maxPrice = 5
        await state.loadOverview()
        XCTAssertEqual(state.overviewState, .loaded)
        await earlier.value

        XCTAssertEqual(state.overviewState, .loaded)
        XCTAssertTrue(state.leads.isEmpty, "An earlier filter request must not restore its old rows")
        XCTAssertNil(state.warning, "An earlier failure must not replace the successful refresh")
    }

    #if DEBUG
    func testEndpointResetRejectsPendingOverviewAndClearsSelectedStationAuthority() async throws {
        let firstStarted = expectation(description: "Old overview reached the service")
        var requestCount = 0
        AdverseURLProtocol.delay = { _ in requestCount == 1 ? 0.4 : 0 }
        let state = makeState { request in
            requestCount += 1
            if requestCount == 1 {
                firstStarted.fulfill()
                return Self.response(request, status: 200, body: Self.lateOverviewJSON)
            }
            return Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        let selected = try TestDecode.chargersResponse(Self.freshJSON).stations[0]
        state.selected = selected
        state.focusedStationID = selected.id
        let earlier = Task { await state.loadOverview() }
        await fulfillment(of: [firstStarted], timeout: 2)

        state.baseURLChanged()
        XCTAssertNil(state.selected)
        XCTAssertNil(state.focusedStationID)
        XCTAssertFalse(state.detailStation(fallback: selected).isRouteReady)
        await waitForOverview(state, expected: .loaded)
        await earlier.value

        XCTAssertEqual(state.overviewState, .loaded)
        XCTAssertTrue(state.leads.isEmpty, "The old service must not repopulate the new overview")
        XCTAssertNil(state.warning)
    }

    func testEndpointResetCancelsPendingLiveAndSearchResponses() async throws {
        let liveStarted = expectation(description: "Old live request reached the service")
        let searchStarted = expectation(description: "Old search reached the service")
        AdverseURLProtocol.delay = { Self.isOverviewRequest($0) ? 0 : 0.5 }
        let state = makeState { request in
            if Self.isOverviewRequest(request) {
                return Self.response(request, status: 200, body: Self.emptyFreshJSON)
            }
            if request.url?.path == "/api/geocode" {
                searchStarted.fulfill()
                return Self.response(request, status: 200,
                                     body: #"{"results":[{"label":"Valencia, Spain","name":"Valencia","lat":39.47,"lon":-0.37}]}"#)
            }
            liveStarted.fulfill()
            return Self.response(request, status: 200, body: Self.freshLivePresentationJSON)
        }
        state.fetchLive(reason: "old-service")
        state.searchText = "Valencia"
        state.searchChanged("Valencia")
        await fulfillment(of: [liveStarted, searchStarted], timeout: 2)

        state.baseURLChanged()
        XCTAssertEqual(state.searchState, .idle)
        XCTAssertTrue(state.searchText.isEmpty)
        await waitForOverview(state, expected: .loaded)
        try await Task.sleep(for: .milliseconds(650))

        XCTAssertEqual(state.mode, .overview)
        XCTAssertEqual(state.liveState, .idle)
        XCTAssertEqual(state.liveQuality, .overview)
        XCTAssertTrue(state.stations.isEmpty)
        XCTAssertTrue(state.leads.isEmpty)
        XCTAssertTrue(state.searchResults.isEmpty)
        XCTAssertEqual(state.searchState, .idle)
        XCTAssertNil(state.routeMoment)
        XCTAssertNil(state.warning)
    }
    #endif

    func testStaleResponsesNeverRouteAndDegradedCoveragePreservesIndividuallyVerifiedRows() async throws {
        let staleState = makeState(responseJSON: Self.staleJSON)
        staleState.fetchLive(reason: "test-stale")
        await waitUntilSettled(staleState)

        XCTAssertEqual(staleState.liveQuality, .stale)
        XCTAssertEqual(staleState.warning, "Serving stale cache. Refresh before routing.")
        XCTAssertEqual(staleState.liveCache?.filterFallback, true)
        XCTAssertEqual(staleState.liveReadyCount, 0)
        XCTAssertFalse(try XCTUnwrap(staleState.stations.first).isRouteReady)

        let degradedState = makeState(responseJSON: Self.degradedJSON)
        degradedState.fetchLive(reason: "test-degraded")
        await waitUntilSettled(degradedState)

        XCTAssertEqual(degradedState.liveQuality, .degraded)
        XCTAssertEqual(degradedState.warning, "Low live coverage. Check the source before routing.")
        XCTAssertEqual(degradedState.liveReadyCount, 1)
        XCTAssertTrue(try XCTUnwrap(degradedState.stations.first).isRouteReady)
    }

    func testTopLevelStaleBannerDoesNotDemoteFreshRowsMergedFromAnotherSource() async throws {
        let state = makeState(responseJSON: Self.mixedStaleAndFreshJSON)
        state.fetchLive(reason: "test-mixed")
        await waitUntilSettled(state)

        XCTAssertEqual(state.liveQuality, .mixed, "The response-level banner names fresh plus cached evidence")
        XCTAssertEqual(state.stations.first(where: { $0.id == "stale" })?.trustKind, .stale)
        XCTAssertFalse(try XCTUnwrap(state.stations.first(where: { $0.id == "stale" })).isRouteReady)
        XCTAssertEqual(state.stations.first(where: { $0.id == "fresh" })?.trustKind, .fresh)
        XCTAssertTrue(try XCTUnwrap(state.stations.first(where: { $0.id == "fresh" })).isRouteReady)
        XCTAssertEqual(state.liveReadyCount, 1)
    }

    func testFreshAuthorityWinsOverLegacyAggregateStaleFlagsWhenNoFinalRowIsStale() async throws {
        let state = makeState(responseJSON: Self.reconciledFreshOnlyJSON)
        state.fetchLive(reason: "test-reconciled-fresh")
        await waitUntilSettled(state)

        XCTAssertEqual(state.liveQuality, .fresh)
        XCTAssertEqual(try XCTUnwrap(state.stations.first).trustKind, .fresh)
        XCTAssertTrue(try XCTUnwrap(state.stations.first).isRouteReady)
        XCTAssertEqual(state.liveReadyCount, 1)
    }

    func testRouteMomentDismissesWhenItsStationBecomesStaleEvenIfAnotherStationIsFresh() async throws {
        var responseIndex = 0
        let state = makeState { request in
            defer { responseIndex += 1 }
            return Self.response(
                request,
                status: 200,
                body: responseIndex == 0 ? Self.freshJSON : Self.sameStationStaleWithFreshReplacementJSON
            )
        }

        state.fetchLive(reason: "fresh-a")
        await waitUntilSettled(state)
        XCTAssertEqual(state.routeMoment?.stationID, "live")

        state.fetchLive(reason: "stale-a-fresh-b")
        await waitUntilSettled(state)
        XCTAssertEqual(state.stations.first(where: { $0.id == "live" })?.trustKind, .stale)
        XCTAssertTrue(try XCTUnwrap(state.stations.first(where: { $0.id == "fresh" })).isRouteReady)
        XCTAssertNil(state.routeMoment, "A route notice must not survive after its own station stops being route-ready")
    }

    func testLiveFailedPayloadIsUnavailableAndFailClosedToLeads() async throws {
        let state = makeState(responseJSON: Self.unavailableJSON)
        state.fetchLive(reason: "test-unavailable")
        await waitUntilSettled(state)

        XCTAssertEqual(state.liveQuality, .unavailable)
        XCTAssertEqual(state.liveState, .failed)
        XCTAssertTrue(state.stations.isEmpty)
        XCTAssertEqual(state.leads.map(\.id), ["lead"])
        XCTAssertEqual(state.liveReadyCount, 0)
    }

    func testSelectedStaleDetailDemotesOnHTTPFailureAndRecoversFreshByStationID() async throws {
        var responseIndex = 0
        let state = makeState { request in
            guard request.url?.path == "/api/chargers" else {
                return Self.response(request, status: 404, body: #"{"error":"not found"}"#)
            }
            defer { responseIndex += 1 }
            switch responseIndex {
            case 0:
                return Self.response(request, status: 200, body: Self.staleJSON)
            case 1:
                return Self.response(request, status: 503, body: #"{"error":"unavailable"}"#)
            default:
                return Self.response(request, status: 200, body: Self.freshJSON)
            }
        }

        state.fetchLive(reason: "stale")
        await waitUntilSettled(state)
        state.selected = try XCTUnwrap(state.stations.first)
        XCTAssertEqual(try XCTUnwrap(state.detailStation).trustKind, .stale)

        state.fetchLive(reason: "failure")
        await waitUntilSettled(state)
        let demoted = try XCTUnwrap(state.detailStation)
        XCTAssertEqual(demoted.id, "live")
        assertUnavailableDetail(demoted)

        state.fetchLive(reason: "recovery")
        await waitUntilSettled(state)
        let recovered = try XCTUnwrap(state.detailStation)
        XCTAssertEqual(recovered.trustKind, .fresh)
        XCTAssertTrue(recovered.isRouteReady)
        XCTAssertTrue(recovered.canShowDecisionReason)
    }

    func testSelectedStaleDetailDemotesWhenSuccessfulResponseRemovesRowThenRecovers() async throws {
        var responseIndex = 0
        let state = makeState { request in
            guard request.url?.path == "/api/chargers" else {
                return Self.response(request, status: 404, body: #"{"error":"not found"}"#)
            }
            defer { responseIndex += 1 }
            switch responseIndex {
            case 0:
                return Self.response(request, status: 200, body: Self.staleJSON)
            case 1:
                return Self.response(request, status: 200, body: Self.emptyFreshJSON)
            default:
                return Self.response(request, status: 200, body: Self.freshJSON)
            }
        }

        state.fetchLive(reason: "stale")
        await waitUntilSettled(state)
        state.selected = try XCTUnwrap(state.stations.first)
        XCTAssertEqual(try XCTUnwrap(state.detailStation).trustKind, .stale)

        state.fetchLive(reason: "row-removed")
        await waitUntilSettled(state)
        let demoted = try XCTUnwrap(state.detailStation)
        XCTAssertEqual(demoted.id, "live")
        assertUnavailableDetail(demoted)

        state.fetchLive(reason: "recovery")
        await waitUntilSettled(state)
        let recovered = try XCTUnwrap(state.detailStation)
        XCTAssertEqual(recovered.trustKind, .fresh)
        XCTAssertTrue(recovered.isRouteReady)
        XCTAssertTrue(recovered.canShowDecisionReason)
    }

    func testStartingAndFailingASecondSearchClearsTheFirstResult() async throws {
        var geocodeIndex = 0
        let state = makeState { request in
            guard request.url?.path == "/api/geocode" else {
                return Self.response(request, status: 200, body: #"{"stations":[],"leads":[]}"#)
            }
            defer { geocodeIndex += 1 }
            if geocodeIndex == 0 {
                return Self.response(
                    request,
                    status: 200,
                    body: #"{"results":[{"label":"Valencia, Spain","name":"Valencia","lat":39.47,"lon":-0.37}]}"#
                )
            }
            return Self.response(request, status: 503, body: #"{"error":"unavailable"}"#)
        }

        state.searchText = "Valencia"
        state.searchChanged("Valencia")
        await waitForSearch(state, expected: .results)
        XCTAssertEqual(state.searchResults.map(\.name), ["Valencia"])

        state.searchText = "Barcelona"
        state.searchChanged("Barcelona")
        XCTAssertTrue(state.searchResults.isEmpty, "A new query must immediately remove the old result")
        XCTAssertEqual(state.searchState, .loading)
        await waitForSearch(state, expected: .failed)
        XCTAssertTrue(state.searchResults.isEmpty)
    }

    func testAuthorizationTransitionRecoversFromSettingsExactlyOnce() {
        for priorState in [AppState.LocationState.denied, .restricted] {
            let recovery = AppState.authorizationTransition(
                status: .authorizedWhenInUse,
                locationState: priorState,
                locating: false,
                awaitingAuthorization: false
            )
            XCTAssertEqual(recovery.locationState, .requesting)
            XCTAssertTrue(recovery.locating)
            XCTAssertTrue(recovery.shouldRequestLocation)
            XCTAssertFalse(recovery.awaitingAuthorization)

            let repeatedCallback = AppState.authorizationTransition(
                status: .authorizedWhenInUse,
                locationState: recovery.locationState,
                locating: recovery.locating,
                awaitingAuthorization: recovery.awaitingAuthorization
            )
            XCTAssertFalse(repeatedCallback.shouldRequestLocation, "A repeated authorized callback must not request twice")
        }
    }

    func testAuthorizationTransitionPreservesColdLaunchAndPromptGrantSemantics() {
        let coldLaunchCallback = AppState.authorizationTransition(
            status: .authorizedAlways,
            locationState: .requesting,
            locating: true,
            awaitingAuthorization: false
        )
        XCTAssertFalse(coldLaunchCallback.shouldRequestLocation,
                       "locate() already requested location for a cold-launch authorized state")

        let promptGrant = AppState.authorizationTransition(
            status: .authorizedWhenInUse,
            locationState: .requesting,
            locating: true,
            awaitingAuthorization: true
        )
        XCTAssertTrue(promptGrant.shouldRequestLocation)
        XCTAssertFalse(promptGrant.awaitingAuthorization)
    }

    func testGrantFromSettingsCannotOverrideNewerManualDiscovery() {
        let transition = AppState.authorizationTransition(
            status: .authorizedWhenInUse, locationState: .denied,
            locating: false, awaitingAuthorization: false, allowSettingsRecovery: false
        )
        XCTAssertFalse(transition.shouldRequestLocation)
        XCTAssertFalse(transition.locating)
        XCTAssertEqual(transition.locationState, .idle, "Allowed permission must not keep showing denied recovery")
    }

    func testSettingsGrantClearsDeniedUIWithoutMovingManualArea() async {
        let manager = NearbyLocationManager()
        manager.testAuthorization = .denied
        let state = makeState(locationManager: manager) { request in
            Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.start()
        state.pick(GeocodeResult(label: "Madrid", name: "Madrid", lat: 40.4168, lon: -3.7038, bbox: nil))
        manager.testAuthorization = .authorizedWhenInUse
        state.locationManagerDidChangeAuthorization(manager)
        await waitUntilSettled(state)
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(state.locationState, .idle)
        XCTAssertFalse(state.locating)
        XCTAssertEqual(manager.locationRequests, 0)
        XCTAssertEqual(state.visibleRegion.center.latitude, 40.4168)
    }

    func testExplicitLocateAfterManualAreaOwnsSettingsRecoveryExactlyOnce() async {
        let manager = NearbyLocationManager()
        manager.testAuthorization = .denied
        let state = makeState(locationManager: manager) { request in
            Self.response(request, status: 200, body: Self.emptyFreshJSON)
        }
        state.start()
        state.pick(GeocodeResult(label: "Madrid", name: "Madrid", lat: 40.4168, lon: -3.7038, bbox: nil))
        state.locate()
        XCTAssertEqual(state.locationState, .denied)
        manager.testAuthorization = .authorizedWhenInUse
        state.locationManagerDidChangeAuthorization(manager)
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(state.locationState, .requesting)
        XCTAssertEqual(manager.locationRequests, 1)
        state.locationManagerDidChangeAuthorization(manager)
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(manager.locationRequests, 1)
        state.locationManager(manager, didUpdateLocations: [Self.location(age: 0, accuracy: 40)])
        try? await Task.sleep(for: .milliseconds(100))
        await waitUntilSettled(state)
        XCTAssertEqual(state.locationState, .located)
        XCTAssertEqual(state.visibleRegion.center.latitude, 39.47)
    }

    func testLateLocationFailureDoesNotEraseAnAlreadyResolvedCoordinate() {
        XCTAssertEqual(
            AppState.locationFailureTransition(hasUsableCoordinate: true),
            .located
        )
        XCTAssertEqual(
            AppState.locationFailureTransition(hasUsableCoordinate: false),
            .failed
        )
    }

    func testFreshLivePresentationSurvivesLateOverviewFailure() async throws {
        AdverseURLProtocol.delay = { request in
            Self.isOverviewRequest(request) ? 0.5 : 0
        }
        let state = makeState { request in
            if Self.isOverviewRequest(request) {
                return Self.response(request, status: 503, body: #"{"error":"unavailable"}"#)
            }
            return Self.response(request, status: 200, body: Self.freshLivePresentationJSON)
        }

        let overviewTask = Task { await state.loadOverview() }
        await waitForOverview(state, expected: .loading)
        state.fetchLive(reason: "race-live")
        await waitUntilSettled(state)
        XCTAssertEqual(state.liveQuality, .fresh)
        XCTAssertEqual(state.warning, "Fresh live response remains authoritative.")
        XCTAssertEqual(state.leads.map(\.id), ["live-lead"])
        XCTAssertEqual(state.routeMoment?.stationID, "live")

        await overviewTask.value
        XCTAssertEqual(state.overviewState, .failed)
        XCTAssertEqual(state.warning, "Fresh live response remains authoritative.")
        XCTAssertEqual(state.leads.map(\.id), ["live-lead"])
        XCTAssertEqual(state.routeMoment?.stationID, "live")
    }

    func testFreshLivePresentationSurvivesLateOverviewSuccess() async throws {
        AdverseURLProtocol.delay = { request in
            Self.isOverviewRequest(request) ? 0.5 : 0
        }
        let state = makeState { request in
            if Self.isOverviewRequest(request) {
                return Self.response(request, status: 200, body: Self.lateOverviewJSON)
            }
            return Self.response(request, status: 200, body: Self.freshLivePresentationJSON)
        }

        let overviewTask = Task { await state.loadOverview() }
        await waitForOverview(state, expected: .loading)
        state.fetchLive(reason: "race-live")
        await waitUntilSettled(state)
        XCTAssertEqual(state.leads.map(\.id), ["live-lead"])
        XCTAssertEqual(state.routeMoment?.stationID, "live")

        await overviewTask.value
        XCTAssertEqual(state.overviewState, .loaded)
        XCTAssertEqual(state.warning, "Fresh live response remains authoritative.")
        XCTAssertEqual(state.leads.map(\.id), ["live-lead"])
        XCTAssertEqual(state.routeMoment?.stationID, "live")
    }

    func testLateOverviewSuccessSuppliesFallbackAfterLiveFailsFirst() async throws {
        AdverseURLProtocol.delay = { request in
            Self.isOverviewRequest(request) ? 0.5 : 0
        }
        let state = makeState { request in
            if Self.isOverviewRequest(request) {
                return Self.response(request, status: 200, body: Self.lateOverviewJSON)
            }
            return Self.response(request, status: 503, body: #"{"error":"unavailable"}"#)
        }

        let overviewTask = Task { await state.loadOverview() }
        await waitForOverview(state, expected: .loading)
        state.fetchLive(reason: "race-live-failure")
        await waitUntilSettled(state)

        XCTAssertEqual(state.liveState, .failed)
        XCTAssertEqual(state.liveQuality, .unavailable)
        XCTAssertTrue(state.stations.isEmpty)
        XCTAssertTrue(state.leads.isEmpty)
        XCTAssertNil(state.routeMoment)
        let liveFailureWarning = try XCTUnwrap(state.warning)

        await overviewTask.value
        XCTAssertEqual(state.overviewState, .loaded)
        XCTAssertEqual(state.mode, .live)
        XCTAssertTrue(state.stations.isEmpty)
        XCTAssertEqual(state.leads.map(\.id), ["overview-lead"])
        XCTAssertEqual(state.warning, liveFailureWarning,
                       "Fallback leads must not replace the live failure warning")
        XCTAssertNil(state.routeMoment,
                     "Overview fallback must not create or restore a route-ready notice")
    }

    func testOverviewLeadsPaintDuringLiveLoadingAndSurviveItsLaterFailure() async throws {
        AdverseURLProtocol.delay = { request in
            Self.isOverviewRequest(request) ? 0.05 : 0.5
        }
        let state = makeState { request in
            if Self.isOverviewRequest(request) {
                return Self.response(request, status: 200, body: Self.lateOverviewJSON)
            }
            return Self.response(request, status: 503, body: #"{"error":"unavailable"}"#)
        }

        let overviewTask = Task { await state.loadOverview() }
        await waitForOverview(state, expected: .loading)
        state.fetchLive(reason: "race-live-loading")

        await overviewTask.value
        XCTAssertEqual(state.overviewState, .loaded)
        XCTAssertEqual(state.mode, .live)
        XCTAssertEqual(state.liveState, .loading)
        XCTAssertTrue(state.stations.isEmpty)
        XCTAssertEqual(state.leads.map(\.id), ["overview-lead"],
                       "The fast overview should paint honest fallback rows during live loading")
        XCTAssertNil(state.routeMoment)

        await waitUntilSettled(state)
        XCTAssertEqual(state.liveState, .failed)
        XCTAssertEqual(state.liveQuality, .unavailable)
        XCTAssertTrue(state.stations.isEmpty)
        XCTAssertEqual(state.leads.map(\.id), ["overview-lead"],
                       "A later live failure must preserve the already-painted fallback")
        XCTAssertEqual(state.warning, state.serviceFailureMessage)
        XCTAssertNil(state.routeMoment)
    }

    func testDirectoryOnlyRegionNeverAnnouncesFreshLiveAuthority() async throws {
        let state = makeState(responseJSON: """
        {"stations":[],"leads":[],"cache":{"live_supported":false,"live_failed":false},
         "meta":{"coverage":{"mode":"directory"}}}
        """)
        state.fetchLive(reason: "global-directory")
        await waitUntilSettled(state)
        XCTAssertEqual(state.liveQuality, .overview)
        XCTAssertEqual(state.liveState, .ok)
        XCTAssertEqual(state.liveReadyCount, 0)
    }

    func testCountryViewRequestsOnlyScopedStoredEvidence() async {
        let state = makeState { request in
            XCTAssertTrue(Self.isOverviewRequest(request))
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems
            XCTAssertEqual(query?.first(where: { $0.name == "bbox" })?.value, "30.0,129.0,46.0,146.0")
            return Self.response(request, status: 200, body: "{\"stations\":[],\"leads\":[],\"meta\":{\"truncated\":true}}")
        }
        state.visibleRegion = MKCoordinateRegion(center: .init(latitude: 38, longitude: 137.5),
                                                span: .init(latitudeDelta: 16, longitudeDelta: 17))
        state.fetchLive(reason: "country")
        await waitForOverview(state, expected: .loaded)
        XCTAssertEqual(state.mode, .overview)
        XCTAssertEqual(state.overviewState, .loaded)
        XCTAssertTrue(state.resultsTruncated)
    }

    private func makeState(responseJSON: String) -> AppState {
        makeState { request in Self.response(request, status: 200, body: responseJSON) }
    }

    private func assertUnavailableDetail(
        _ station: Station,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertEqual(station.trustKind, .unavailable, file: file, line: line)
        XCTAssertEqual(station.tierLabel, "Unavailable", file: file, line: line)
        XCTAssertEqual(station.decisionLabel, "Refresh first", file: file, line: line)
        XCTAssertNotEqual(station.decisionKind, .go, file: file, line: line)
        XCTAssertFalse(station.canShowLiveAvailability, file: file, line: line)
        XCTAssertFalse(station.isRouteReady, file: file, line: line)
        XCTAssertFalse(station.canShowDecisionReason, file: file, line: line)
    }

    private func makeState(
        locationManager: CLLocationManager = NearbyLocationManager(),
        handler: @escaping (URLRequest) throws -> (HTTPURLResponse, Data)
    ) -> AppState {
        AdverseURLProtocol.handler = handler
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AdverseURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let state = AppState(api: VoltaAPI(baseURL: URL(string: "https://volta.test")!, session: session),
                             locationManager: locationManager)
        // Live-response fixtures model a local search; country views now use overview.
        state.visibleRegion = MKCoordinateRegion(center: .init(latitude: 39.47, longitude: -0.37),
                                                span: .init(latitudeDelta: 0.16, longitudeDelta: 0.16))
        return state
    }

    private func waitUntilSettled(_ state: AppState) async {
        for _ in 0 ..< 100 where state.liveState == .loading {
            try? await Task.sleep(for: .milliseconds(20))
        }
    }

    private func waitForSearch(_ state: AppState, expected: AppState.SearchState) async {
        for _ in 0 ..< 100 where state.searchState != expected {
            try? await Task.sleep(for: .milliseconds(20))
        }
    }

    private func waitForOverview(_ state: AppState, expected: AppState.OverviewState) async {
        for _ in 0 ..< 100 where state.overviewState != expected {
            try? await Task.sleep(for: .milliseconds(20))
        }
    }

    private static func isOverviewRequest(_ request: URLRequest) -> Bool {
        URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?
            .queryItems?
            .contains(where: { $0.name == "overview" && $0.value == "true" }) == true
    }

    private static func response(
        _ request: URLRequest,
        status: Int,
        body: String
    ) -> (HTTPURLResponse, Data) {
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: status,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        return (response, Data(body.utf8))
    }

    private static let freshJSON = """
    {
      "stations": [{
        "id":"live","name":"Fresh live station","lat":39.47,"lon":-0.37,
        "lead":false,"available":2,"status":"AVAILABLE",
        "decision":{"level":"go","label":"Go now"},
        "verification":{"level":"live","live_verified":true,"location_verified":true},
        "primary_source":{"name":"MapaREVE","url":"https://example.com/live","action":"Open MapaREVE"},
        "source_url":"https://example.com/live"
      }],
      "leads": [],
      "cache":{"hit":false,"stale":false},
      "meta":{"coverage":{"mode":"standard"}}
    }
    """

    private static let staleJSON = """
    {
      "stations": [{
        "id":"live","name":"Stale station","lat":39.47,"lon":-0.37,
        "lead":false,"cache_stale":true,"available":2,
        "decision":{"level":"go","label":"Go now","reason":"Old optimistic reason"},
        "verification":{"level":"live","live_verified":true}
      }],
      "leads": [],
      "warning":"Serving stale cache. Refresh before routing.",
      "cache":{"hit":true,"stale":true,"filter_fallback":true},
      "meta":{"coverage":{"mode":"standard"}}
    }
    """

    private static let emptyFreshJSON = """
    {
      "stations": [],
      "leads": [],
      "cache":{"hit":false,"stale":false},
      "meta":{"coverage":{"mode":"standard"}}
    }
    """

    private static let degradedJSON = """
    {
      "stations": [{
        "id":"limited","name":"Limited station","lat":39.47,"lon":-0.37,
        "lead":false,"available":2,"decision":{"level":"go"},
        "verification":{"level":"live","live_verified":true}
      }],
      "leads": [],
      "warning":"Low live coverage. Check the source before routing.",
      "cache":{"hit":false,"stale":false},
      "meta":{"coverage":{"mode":"degraded"}}
    }
    """

    private static let unavailableJSON = """
    {
      "stations": [],
      "leads": [{"id":"lead","name":"Known lead","lat":39.47,"lon":-0.37,"lead":true}],
      "warning":"Live source unavailable. Showing source-backed leads only.",
      "cache":{"hit":false,"live_failed":true},
      "meta":{"coverage":{"mode":"degraded"}}
    }
    """

    private static let mixedStaleAndFreshJSON = """
    {
      "stations": [
        {"id":"stale","name":"Stale MapaREVE","lat":39.47,"lon":-0.37,
         "lead":false,"cache_stale":true,"available":2,"decision":{"level":"go"},
         "verification":{"level":"live","live_verified":true}},
        {"id":"fresh","name":"Fresh Gaia","lat":39.48,"lon":-0.38,
         "lead":false,"available":1,"decision":{"level":"go"},
         "verification":{"level":"live","live_verified":true}}
      ],
      "leads": [],
      "warning":"MapaREVE is stale; a fresh independent source is still available.",
      "cache":{"hit":true,"stale":true,"fresh_live_authority":true,"mixed_live":true},
      "meta":{"coverage":{"mode":"standard"}}
    }
    """

    private static let reconciledFreshOnlyJSON = """
    {
      "stations": [
        {"id":"fresh","name":"Fresh Gaia replacement","lat":39.47,"lon":-0.37,
         "lead":false,"available":1,"status":"AVAILABLE","decision":{"level":"go"},
         "verification":{"level":"live","live_verified":true}}
      ],
      "leads": [],
      "warning":"Fresh secondary-source status remains live.",
      "cache":{"hit":true,"stale":true,"live_failed":true,"fresh_live_authority":true,"mixed_live":false},
      "meta":{"coverage":{"mode":"standard"}}
    }
    """

    private static let sameStationStaleWithFreshReplacementJSON = """
    {
      "stations": [
        {"id":"live","name":"Fresh A, now stale","lat":39.47,"lon":-0.37,
         "lead":false,"cache_stale":true,"available":2,"decision":{"level":"go"},
         "verification":{"level":"live","live_verified":true}},
        {"id":"fresh","name":"Fresh B","lat":39.48,"lon":-0.38,
         "lead":false,"available":1,"decision":{"level":"go"},
         "verification":{"level":"live","live_verified":true}}
      ],
      "leads": [],
      "warning":"A is stale; B remains fresh.",
      "cache":{"hit":true,"stale":true,"fresh_live_authority":true,"mixed_live":true},
      "meta":{"coverage":{"mode":"standard"}}
    }
    """

    private static let freshLivePresentationJSON = """
    {
      "stations": [{
        "id":"live","name":"Fresh live station","lat":39.47,"lon":-0.37,
        "lead":false,"available":2,"status":"AVAILABLE",
        "decision":{"level":"go","label":"Go now"},
        "verification":{"level":"live","live_verified":true,"location_verified":true}
      }],
      "leads": [{"id":"live-lead","name":"Live-mode lead","lat":39.48,"lon":-0.38,"lead":true}],
      "warning":"Fresh live response remains authoritative.",
      "cache":{"hit":false,"stale":false},
      "meta":{"coverage":{"mode":"standard"}}
    }
    """

    private static let lateOverviewJSON = """
    {
      "stations": [],
      "leads": [{"id":"overview-lead","name":"Late overview lead","lat":40.0,"lon":-3.7,"lead":true}],
      "warning":"Late overview warning.",
      "meta":{"coverage":{"mode":"standard"}}
    }
    """
}

private final class NearbyLocationManager: CLLocationManager {
    var testAuthorization: CLAuthorizationStatus = .authorizedWhenInUse
    var cachedLocation: CLLocation?
    var locationRequests = 0
    var authorizationRequests = 0
    var cancelledLocationRequests = 0

    override var authorizationStatus: CLAuthorizationStatus { testAuthorization }
    override var location: CLLocation? { cachedLocation }
    override func requestLocation() { locationRequests += 1 }
    override func requestWhenInUseAuthorization() { authorizationRequests += 1 }
    override func stopUpdatingLocation() { cancelledLocationRequests += 1 }
}

private final class AdverseURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    static var delay: ((URLRequest) -> TimeInterval)?

    override class func canInit(with _: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            let handler = try XCTUnwrap(Self.handler)
            let (response, data) = try handler(request)
            let delay = Self.delay?(request) ?? 0
            if delay > 0 {
                DispatchQueue.global(qos: .userInitiated).asyncAfter(deadline: .now() + delay) { [weak self] in
                    self?.deliver(response: response, data: data)
                }
            } else {
                deliver(response: response, data: data)
            }
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}

    private func deliver(response: HTTPURLResponse, data: Data) {
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
}
