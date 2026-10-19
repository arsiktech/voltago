import XCTest
@testable import Volta

final class VehiclePlanningTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func manual() -> OwnedVehicle {
        var vehicle = OwnedVehicle.manual()
        vehicle.model = "Owner car"
        vehicle.batteryPercent = 20
        vehicle.batteryCapacityKwh = 75
        vehicle.maxAcKw = 7.4
        vehicle.maxDcKw = 150
        vehicle.connectors = ["Type 2"]
        return vehicle
    }

    private func connected() -> OwnedVehicle {
        var vehicle = manual()
        vehicle.id = "provider-car"
        vehicle.source = VehicleSource(provider: "Smartcar", mode: "live")
        vehicle.isStale = false
        let fresh = VehicleDate.string(now.addingTimeInterval(240))
        vehicle.freshUntil = fresh
        vehicle.fieldFreshUntil = ["batteryPercent": fresh, "batteryCapacityKwh": fresh,
                                  "chargingState": fresh, "rangeKm": fresh]
        return vehicle
    }

    func testManualEnergyAndTimeRespectMatchedPortAndVehicleACLimit() throws {
        let vehicle = manual()
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"max_power_kw":350,
         "price_min":0.2,"price_currency":"EUR","verification":{"price_verified":true},
         "connectors":[{"type":"Type 2","power_kw":22},{"type":"CCS2","power_kw":350}]}
        """)
        let estimate = VehicleChargingEstimate(vehicle: vehicle, target: 80, station: station, now: now)
        XCTAssertEqual(vehicle.availableEnergy(at: now), 15)
        XCTAssertEqual(estimate.batteryEnergyKwh, 45)
        XCTAssertEqual(estimate.minimumMinutes, 365)
        XCTAssertEqual(try XCTUnwrap(estimate.energyCostEUR), 10, accuracy: 0.001)
        XCTAssertEqual(estimate.compatibility, .compatible)
    }

    func testAggregateMaximumCannotSupplyAnUnknownSocketPower() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"max_power_kw":350,
         "connectors":[{"type":"Type 2"},{"type":"CCS2","power_kw":350}]}
        """)
        XCTAssertNil(VehicleChargingEstimate(vehicle: manual(), target: 80, station: station, now: now).minimumMinutes)
    }

    func testFreshChargingStatusCannotReviveAnOldBattery() throws {
        var vehicle = connected()
        vehicle.fieldFreshUntil?["batteryPercent"] = VehicleDate.string(now.addingTimeInterval(-1))
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"connectors":[{"type":"Type 2","power_kw":22}]}
        """)
        let estimate = VehicleChargingEstimate(vehicle: vehicle, target: 80, station: station, now: now)
        XCTAssertTrue(estimate.stale)
        XCTAssertNil(estimate.minimumMinutes)
        XCTAssertNil(estimate.energyCostEUR)
        XCTAssertNil(vehicle.availableEnergy(at: now))
        XCTAssertTrue(vehicle.fieldIsFresh("chargingState", at: now))
    }

    func testOldProviderCapacityCannotCombineWithFreshBattery() throws {
        var vehicle = connected()
        vehicle.fieldFreshUntil?["batteryCapacityKwh"] = VehicleDate.string(now.addingTimeInterval(-1))
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"connectors":[{"type":"Type 2","power_kw":22}]}
        """)
        let estimate = VehicleChargingEstimate(vehicle: vehicle, target: 80, station: station, now: now)
        XCTAssertTrue(estimate.stale)
        XCTAssertNil(vehicle.availableEnergy(at: now))
        XCTAssertNil(estimate.batteryEnergyKwh)
        XCTAssertNil(estimate.minimumMinutes)
        // An explicitly owner-entered capacity is a planning specification, not telemetry.
        vehicle.ownerEnteredCapacity = true
        XCTAssertEqual(VehicleChargingEstimate(vehicle: vehicle, target: 80, station: station, now: now).batteryEnergyKwh, 45)
    }

    func testNACSRequiresExplicitACOrDCBeforePickingVehicleLimit() throws {
        var vehicle = manual()
        vehicle.connectors = ["NACS"]
        let ambiguous = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"connectors":[{"type":"NACS","power_kw":150}]}
        """)
        let ac = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"connectors":[{"type":"NACS","power_kw":22,"fast":false}]}
        """)
        let dc = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"connectors":[{"type":"NACS","power_kw":150,"fast":true}]}
        """)
        XCTAssertNil(VehicleChargingEstimate(vehicle: vehicle, target: 80, station: ambiguous, now: now).minimumMinutes)
        XCTAssertEqual(VehicleChargingEstimate(vehicle: vehicle, target: 80, station: ac, now: now).minimumMinutes, 365)
        XCTAssertEqual(VehicleChargingEstimate(vehicle: vehicle, target: 80, station: dc, now: now).minimumMinutes, 18)
    }

    func testUnknownConnectorDoesNotAssertPhysicalIncompatibility() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"connectors":[{"type":"CCS1"},{"type":"unrecognized socket"}]}
        """)
        XCTAssertEqual(VehicleChargingEstimate(vehicle: manual(), target: 80, station: station, now: now).compatibility, .unknown)
    }

    func testTargetBelowCurrentChargeHasZeroEnergyAndUnknownCapacityHasNoGuess() throws {
        var vehicle = manual()
        vehicle.batteryPercent = 90
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"connectors":[{"type":"Type 2","power_kw":22}]}
        """)
        XCTAssertEqual(VehicleChargingEstimate(vehicle: vehicle, target: 80, station: station, now: now).batteryEnergyKwh, 0)
        vehicle.batteryCapacityKwh = nil
        XCTAssertNil(vehicle.availableEnergy(at: now))
        XCTAssertNil(VehicleChargingEstimate(vehicle: vehicle, target: 80, station: station, now: now).minimumMinutes)
    }

    func testTariffCurrencyUnitValidityAndCacheAreRequired() throws {
        let variants = [
            "\"price_currency\":\"USD\",\"price_native_unit\":\"kWh\"",
            "\"price_currency\":\"EUR\",\"price_native_unit\":\"minute\"",
            "\"price_currency\":\"EUR\",\"cache_stale\":true",
            "\"price_currency\":\"EUR\",\"pricing\":[{\"price\":0.2,\"currency\":\"EUR\",\"unit\":\"kWh\",\"is_currently_valid\":false}]"
        ]
        for variant in variants {
            let station = try TestDecode.station("""
            {"id":"s","name":"N","lat":1,"lon":2,"price_min":0.2,
             "verification":{"price_verified":true},\(variant)}
            """)
            XCTAssertNil(VehicleChargingEstimate(vehicle: manual(), target: 80, station: station, now: now).energyCostEUR, variant)
        }
    }

    func testExplicitCurrentWindowDoesNotNeedDuplicatedCurrentFlag() throws {
        let station = try TestDecode.station("""
        {"id":"s","name":"N","lat":1,"lon":2,"price_currency":"EUR",
         "verification":{"price_verified":true},"price_window_status":{"current_windows":[
         {"price":0.2,"currency":"EUR","unit":"kWh"}]}}
        """)
        XCTAssertEqual(try XCTUnwrap(VehicleChargingEstimate(vehicle: manual(), target: 80, station: station, now: now).energyCostEUR), 10, accuracy: 0.001)
    }

    func testNullPerFieldDeadlinesDecodeAndRemainUnknown() throws {
        let vehicle = try JSONDecoder().decode(OwnedVehicle.self, from: Data("""
        {"id":"v","source":{"provider":"Smartcar","mode":"live"},
         "fieldFreshUntil":{"batteryPercent":null},"batteryPercent":20}
        """.utf8))
        XCTAssertFalse(vehicle.fieldIsFresh("batteryPercent", at: now))
        XCTAssertNil(vehicle.availableEnergy(at: now))
    }

    func testNativeHandoffRejectsWrongDestinationDuplicateCodeAndCredentials() throws {
        let code = String(repeating: "a", count: 43)
        XCTAssertEqual(try VehicleAuthorization.handoffCode(from: URL(string: "voltagovehicle://connection/callback?code=\(code)")!), code)
        for url in [
            "voltagovehicle://other/callback?code=\(code)",
            "voltagovehicle://connection/other?code=\(code)",
            "https://connection/callback?code=\(code)",
            "voltagovehicle://connection/callback?code=\(code)&code=\(code)",
            "voltagovehicle://user@connection/callback?code=\(code)",
            "voltagovehicle://connection/callback?code=short",
            "voltagovehicle://connection/callback?code=\(code)#fragment"
        ] { XCTAssertThrowsError(try VehicleAuthorization.handoffCode(from: URL(string: url)!)) }
    }
}
