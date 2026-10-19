import XCTest
@testable import Volta

final class SourceDetailsPresentationTests: XCTestCase {
    func testHourlyChargeKeepsItsUnitAndFreeHourCondition() throws {
        let station = try TestDecode.station("""
        {"id":"hourly","name":"N","lat":28,"lon":-16,"lead":true,
         "tariff_details":{"hour_eur":2,"unconditional_free":false,"conditions":["First hour free"]}}
        """)
        XCTAssertEqual(station.priceText, "€2.00")
        XCTAssertEqual(station.priceUnit, "/hour")
        XCTAssertEqual(station.tariffNotes, ["First hour free"])
        XCTAssertNil(station.priceMin, "An hourly charge is not an energy price")
        XCTAssertFalse(station.isRouteReady)
        XCTAssertFalse(station.hasVerifiedPrice)
    }

    func testEnergyChargeKeepsMinimumAndExtraTimeConditions() throws {
        let station = try TestDecode.station("""
        {"id":"composite","name":"N","lat":28,"lon":-16,"price_min":0.35,
         "tariff_details":{"energy_eur_per_kwh":0.35,"minimum_eur":2,"hour_eur":1,
                           "conditions":["Customers only","Customers only"]}}
        """)
        XCTAssertEqual(station.priceText, "€0.35")
        XCTAssertEqual(station.priceUnit, "/kWh")
        XCTAssertEqual(station.tariffDetails?.energyEurPerKwh, 0.35)
        XCTAssertEqual(station.tariffNotes, ["Minimum charge €2.00", "Charging time €1.00/hour", "Customers only"])
    }

    func testConditionalEnergyAndSessionMinimumDoNotBecomeFreeEnergy() throws {
        let station = try TestDecode.station("""
        {"id":"conditional","name":"N","lat":28,"lon":-16,
         "tariff_details":{"energy_eur_per_kwh":0,"unconditional_free":false,
                           "components":[{"type":"MINIMUM","price":2,"unit":"session"},
                                         {"type":"FLAT","price":0.5,"unit":"session"}],
                           "conditions":["With a purchase"]}}
        """)
        XCTAssertEqual(station.priceText, "—")
        XCTAssertNil(station.priceUnit)
        XCTAssertEqual(station.tariffNotes, ["Minimum charge €2.00", "Session fee €0.50", "With a purchase"])
    }

    func testConnectorQuantityAndPowerStaySourceFacts() throws {
        let station = try TestDecode.station("""
        {"id":"hardware","name":"N","lat":28,"lon":-16,"lead":true,
         "connectors":[{"label":"Type 2","count":3,"power_kw":7.4,"source_url":"https://canariasterecarga.com/es/p123/"},
                       {"label":"Unknown connector","count":null},
                       {"label":"CCS2","source_url":"file:///private/source"}]}
        """)
        let connectors = try XCTUnwrap(station.connectors)
        XCTAssertEqual(connectors[0].inventoryLabel, "3 × Type 2")
        XCTAssertEqual(connectors[0].powerText, "7.4 kW")
        XCTAssertEqual(connectors[0].safeSourceURL?.absoluteString, "https://canariasterecarga.com/es/p123/")
        XCTAssertEqual(connectors[1].inventoryLabel, "Unknown connector")
        XCTAssertNil(connectors[1].count)
        XCTAssertEqual(connectors[2].inventoryLabel, "CCS2")
        XCTAssertNil(connectors[2].safeSourceURL)
    }

    func testSourceDetailsAndPhotoLinksDoNotGrantImageDisplayRights() throws {
        let station = try TestDecode.station("""
        {"id":"details","name":"N","lat":28,"lon":-16,
         "source_details":{"source_url":"https://canariasterecarga.com/es/p123/","description":"Parking level -1",
                           "opening_hours":"08:00–22:00","access":"Customers only",
                           "contact":{"phone":"123456789","website":"javascript:alert(1)"}},
         "media":{"photos":[{"url":"https://canariasterecarga.com/photo.jpg",
                                "source_url":"https://canariasterecarga.com/es/p123/","caption":"Entrance"}]}}
        """)
        XCTAssertEqual(station.sourceDetails?.openingHours, "08:00–22:00")
        XCTAssertEqual(station.sourceDetails?.access, "Customers only")
        XCTAssertEqual(station.sourceDetails?.contact?.phone, "123456789")
        XCTAssertNotNil(station.sourceDetails?.safeSourceURL)
        XCTAssertNil(station.sourceDetails?.contact?.safeWebsiteURL)
        XCTAssertEqual(station.media?.photos?.first?.caption, "Entrance")
        XCTAssertNotNil(station.media?.photos?.first?.safeSourceURL)
        XCTAssertNil(station.media?.photos?.first?.safeURL)
        XCTAssertNil(station.firstPublicPhoto)
    }

    func testDifferentSourcePageInventoryDoesNotReplaceSelectedHardware() throws {
        let station = try TestDecode.station("""
        {"id":"two-sources","name":"N","lat":28,"lon":-16,"lead":true,
         "connectors":[{"label":"Type 2","count":2,"power_kw":22}],
         "source_details":{"source_name":"Canarias te recarga","connector_count":9,"max_power_kw":50,
                           "connectors":[{"label":"CCS2","count":1,"power_kw":50},
                                         {"label":"CHAdeMO","count":1,"power_kw":50},
                                         {"label":"Type 2","count":1,"power_kw":43},
                                         {"label":"Type 2","count":6,"power_kw":22}]}}
        """)
        XCTAssertEqual(station.connectors?.first?.count, 2)
        XCTAssertEqual(station.sourceDetails?.sourceName, "Canarias te recarga")
        XCTAssertEqual(station.sourceDetails?.connectorCount, 9)
        XCTAssertEqual(station.sourceDetails?.maxPowerKw, 50)
        XCTAssertEqual(station.sourcePageConnectors.map(\.inventoryText), [
            "1 × CCS2 · 50 kW", "1 × CHAdeMO · 50 kW", "1 × Type 2 · 43 kW", "6 × Type 2 · 22 kW"
        ])
        XCTAssertFalse(station.canShowLiveAvailability)
    }

    func testMatchingSourcePageInventoryIsNotRepeatedForDifferentOrderOrMetadata() throws {
        let station = try TestDecode.station("""
        {"id":"same-hardware","name":"N","lat":28,"lon":-16,
         "connectors":[{"label":"Type 2","count":2,"power_kw":22.1},{"label":"CCS2","power_kw":50}],
         "source_details":{"connectors":[{"label":"CCS2","power_kw":50,"source_url":"https://canariasterecarga.com/es/p123/"},
                                         {"label":"Type 2","count":2,"power_kw":22.1,"connector_id":"directory-row"}]}}
        """)
        XCTAssertEqual(station.connectors?.first?.inventoryText, "2 × Type 2 · 22.1 kW")
        XCTAssertTrue(station.sourcePageConnectors.isEmpty)
    }
}
