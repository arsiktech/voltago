import XCTest
@testable import Volta

final class GoogleMapsLinkTests: XCTestCase {
    func testExactGoogleListingsAcceptPlaceIdentifiersAndShareCodes() throws {
        let listings = [
            "https://www.google.com/maps/search/?api=1&query=EV%20charger&query_place_id=ChIJ_station-123",
            "https://google.com/maps/?q=place_id:ChIJ_station-123",
            "https://maps.google.com/?cid=1234567890",
            "https://www.google.es/maps?ftid=0x123abc:0x456def",
            "https://maps.google.es/maps?cid=9876543210",
            "https://www.google.com/maps/place/EV+charger/data=!4m2!3m1!1s0x123:0x456",
            "https://www.google.com/maps/place/EV+charger/data=!4m2!3m1!16s%2Fg%2F11station",
            "https://maps.app.goo.gl/GoogleShareCode",
            "https://goo.gl/maps/GoogleShareCode"
        ]
        for value in listings {
            let station = try station(navigation: ["google_listing": value])
            XCTAssertEqual(station.googleMapsListingURL?.absoluteString, value, value)
            XCTAssertEqual(station.googleMapsActionURL, station.googleMapsListingURL, value)
            XCTAssertEqual(station.googleMapsActionLabel, "View Google Maps", value)
        }
    }

    func testUnsafeAndUnidentifiedLinksCannotBecomeGoogleListings() throws {
        let unverified = [
            "https://example.com/maps/place/Station?cid=123",
            "https://www.google.com.evil.example/maps?cid=123",
            "https://evil.example@www.google.com/maps?cid=123",
            "https://www.google.com:444/maps?cid=123",
            "http://www.google.com/maps?cid=123",
            "javascript:alert(1)",
            "https://www.google.com/url?q=https://example.com/",
            "https://maps.google.com/url?q=https://example.com/",
            "https://www.google.com/maps/dir/?api=1&destination=1,2&query_place_id=ChIJ123",
            "https://www.google.com/maps/place/EV+charger/",
            "https://www.google.com/maps/search/?api=1&query=39.47,-0.38",
            "https://www.google.com/maps?cid=not-a-number",
            "https://www.google.com/maps?query_place_id=",
            "https://www.google.com/maps?ftid=not-a-feature-id",
            "https://maps.app.goo.gl/",
            "https://goo.gl/maps/",
            "https://goo.gl/not-maps/GoogleShareCode"
        ]
        for value in unverified {
            let station = try station(navigation: ["google_listing": value, "google_search": value])
            XCTAssertNil(station.googleMapsListingURL, value)
            XCTAssertEqual(station.googleMapsActionLabel, "Search Google Maps", value)
            XCTAssertEqual(station.googleMapsActionURL?.host, "www.google.com", value)
            XCTAssertEqual(station.googleMapsActionURL.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false)?.percentEncodedPath }, "/maps/search/", value)
        }
    }

    func testBackendSearchRemainsAConfirmableSearchAndPreservesDirections() throws {
        let search = "https://www.google.com/maps/search/?api=1&query=EV%20charging%20station%20near%2039.47,-0.38"
        let directions = "https://www.google.com/maps/dir/?api=1&destination=39.47,-0.38"
        let station = try station(navigation: ["google_search": search, "google": directions])
        XCTAssertNil(station.googleMapsListingURL)
        XCTAssertEqual(station.googleMapsActionURL?.absoluteString, search)
        XCTAssertEqual(station.mapsURL?.absoluteString, directions)
        XCTAssertEqual(station.googleMapsActionLabel, "Search Google Maps")
        XCTAssertTrue(station.googleMapsActionHint.contains("confirm the result matches"))
        XCTAssertFalse(station.isRouteReady, "A Google Maps link cannot grant live charging evidence")
    }

    func testOlderServiceGetsNameAddressCityAndCoordinatesWithoutQueryInjection() throws {
        let station = try station(navigation: nil)
        let url = try XCTUnwrap(station.googleMapsActionURL)
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        XCTAssertNil(station.googleMapsListingURL)
        XCTAssertEqual(components.queryItems?.count, 2)
        XCTAssertEqual(components.queryItems?.first { $0.name == "api" }?.value, "1")
        XCTAssertEqual(
            components.queryItems?.first { $0.name == "query" }?.value,
            "EV charging station Café & зарядка, Calle A #2, Valencia near 39.47,-0.38"
        )
        XCTAssertNil(components.fragment)
        XCTAssertFalse(station.isRouteReady)
    }

    private func station(navigation: [String: String]?) throws -> Station {
        var station = try TestDecode.station("""
        {"id":"google-link", "name":"Café & зарядка", "address":"Calle A #2", "city":"Valencia",
         "lat":39.47, "lon":-0.38, "lead":true, "decision":{"state":"verify","route_ready":false}}
        """)
        station.navigation = navigation
        return station
    }
}
