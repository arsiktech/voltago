import MapKit
import XCTest
@testable import Volta

final class GlobalBoundsTests: XCTestCase {
    func testWorldwideDeviceLocationsStayInsideTheirQueryBounds() {
        let coordinates = [
            CLLocationCoordinate2D(latitude: 35.68, longitude: 139.69),
            CLLocationCoordinate2D(latitude: -33.87, longitude: 151.21),
            CLLocationCoordinate2D(latitude: 37.77, longitude: -122.42),
            CLLocationCoordinate2D(latitude: 90, longitude: 180),
        ]
        for coordinate in coordinates {
            let region = MKCoordinateRegion(center: coordinate,
                                            span: .init(latitudeDelta: 0.16, longitudeDelta: 0.16))
            XCTAssertTrue(region.bbox.contains(latitude: coordinate.latitude, longitude: coordinate.longitude))
            XCTAssertTrue(region.bbox.supportsLiveLookup)
        }
    }

    func testDateLineContainsBothSidesWithoutIncludingGreenwich() {
        let bounds = BBox(south: -17, west: 179, north: -16, east: 181)
        XCTAssertEqual(bounds.west, 179)
        XCTAssertEqual(bounds.east, -179)
        XCTAssertEqual(bounds.longitudeSpan, 2)
        XCTAssertEqual(abs(bounds.centerLongitude), 180)
        XCTAssertTrue(bounds.contains(latitude: -16.5, longitude: 179.5))
        XCTAssertTrue(bounds.contains(latitude: -16.5, longitude: -179.5))
        XCTAssertFalse(bounds.contains(latitude: -16.5, longitude: 0))
        XCTAssertEqual(bounds.queryValue, "-17.0,179.0,-16.0,-179.0")
    }

    func testWorldAndPolarBoundsAreValidAndNeverLiveQueries() {
        let world = BBox(south: -95, west: -190, north: 95, east: 190)
        XCTAssertEqual(world, BBox(south: -90, west: -180, north: 90, east: 180))
        XCTAssertTrue(world.contains(latitude: 90, longitude: 180))
        XCTAssertFalse(world.supportsLiveLookup)
        XCTAssertFalse(world.contains(latitude: .nan, longitude: 0))
        XCTAssertFalse(world.contains(latitude: 0, longitude: .infinity))
    }

    func testHighLatitudeLiveBudgetAndLargeRegionFallback() {
        XCTAssertTrue(BBox(south: 79.5, west: 10, north: 80.5, east: 13).supportsLiveLookup)
        XCTAssertFalse(BBox(south: -0.5, west: 10, north: 0.5, east: 13).supportsLiveLookup)
        XCTAssertFalse(BBox(south: 30, west: 129, north: 46, east: 146).supportsLiveLookup)
    }

    func testExactOCMStationPageOnlyVerifiesItsLocation() throws {
        let station = try TestDecode.station("""
        {"id":"ocm:123","name":"OCM location","lat":35.68,"lon":139.69,"lead":true,
         "reliability":"directory-community-snapshot","source_url":"https://openchargemap.org/site/poi/details/123",
         "verification":{"location_verified":true,"price_verified":false,"live_verified":false}}
        """)
        XCTAssertTrue(station.isMapLocationVerified)
        XCTAssertFalse(station.hasVerifiedPrice)
        XCTAssertFalse(station.isRouteReady)
        var generic = station
        generic.sourceUrl = "https://openchargemap.org/"
        XCTAssertFalse(generic.isMapLocationVerified)
        generic.sourceUrl = "https://other.example/openchargemap.org/site/poi/details/123"
        XCTAssertFalse(generic.isMapLocationVerified)
    }

    // MARK: - Location framing

    private let valencia = CLLocationCoordinate2D(latitude: 39.4699, longitude: -0.3763)

    func testLocationFrameHoldsTheDriverAndTheThreeNearestPlaces() throws {
        let near = [
            CLLocationCoordinate2D(latitude: 39.50, longitude: -0.40),
            CLLocationCoordinate2D(latitude: 39.47, longitude: -0.38),
            CLLocationCoordinate2D(latitude: 39.46, longitude: -0.37),
        ]
        let far = CLLocationCoordinate2D(latitude: 39.90, longitude: -0.10)
        let region = try XCTUnwrap(AppState.nearbyFrameRegion(origin: valencia, places: near + [far]))
        for point in [valencia] + near {
            XCTAssertTrue(region.bbox.contains(latitude: point.latitude, longitude: point.longitude))
        }
        XCTAssertFalse(region.bbox.contains(latitude: far.latitude, longitude: far.longitude))
        XCTAssertTrue(region.bbox.supportsLiveLookup)
    }

    func testLocationFrameIsNeverCloserThanStreetLevel() throws {
        let nextDoor = CLLocationCoordinate2D(latitude: 39.4700, longitude: -0.3762)
        let region = try XCTUnwrap(AppState.nearbyFrameRegion(origin: valencia, places: [nextDoor]))
        let street = MKCoordinateRegion(center: valencia, latitudinalMeters: 1_200, longitudinalMeters: 1_200).span
        XCTAssertGreaterThanOrEqual(region.span.latitudeDelta, street.latitudeDelta * 0.999)
        XCTAssertGreaterThanOrEqual(region.span.longitudeDelta, street.longitudeDelta * 0.999)
    }

    func testLocationFrameIsNilWithoutValidPlaces() {
        XCTAssertNil(AppState.nearbyFrameRegion(origin: valencia, places: []))
        XCTAssertNil(AppState.nearbyFrameRegion(origin: valencia,
                                                places: [CLLocationCoordinate2D(latitude: 200, longitude: 0)]))
    }

    func testLocationSearchSpanStaysInsideTheLiveLookupBudget() {
        let region = MKCoordinateRegion(center: valencia,
                                        span: .init(latitudeDelta: AppState.locationSearchSpan,
                                                    longitudeDelta: AppState.locationSearchSpan))
        XCTAssertTrue(region.bbox.supportsLiveLookup)
    }
}
