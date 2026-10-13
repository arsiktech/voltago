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
}
