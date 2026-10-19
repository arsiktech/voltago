import Foundation

/// Vehicle telemetry is separate from charger evidence. It never grants route readiness.
struct VehicleConnection: Decodable {
    var configured: Bool
    var connected: Bool
    var mode: String?
    var connectionStatus: String
    var reason: String?
    var refreshIntervalSeconds: Int?
    var refreshAllowedAt: String?
    var vehicleLimitReached: Bool?
    var vehicles: [OwnedVehicle]
}

struct VehicleSource: Codable, Equatable {
    var provider: String
    var mode: String
}

struct OwnedVehicle: Codable, Identifiable, Equatable {
    var id: String
    var make: String?
    var model: String?
    var year: Int?
    var batteryPercent: Double?
    var rangeKm: Double?
    var chargingState: String?
    var chargePowerKw: Double?
    var batteryCapacityKwh: Double?
    var usableEnergyKwh: Double?
    var usableEnergyEstimated: Bool?
    var maxAcKw: Double?
    var maxDcKw: Double?
    var connectors: [String]?
    var source: VehicleSource
    var observedAt: String?
    var fetchedAt: String?
    var freshUntil: String?
    var lastAttemptAt: String?
    var isStale: Bool?
    var error: String?
    var fieldObservedAt: [String: String?]?
    var fieldFreshUntil: [String: String?]?
    /// Client-only provenance for a capacity that filled a missing provider value.
    var ownerEnteredCapacity: Bool?

    var name: String {
        let parts = [make, model].compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        return parts.isEmpty ? String(localized: "My EV") : parts.joined(separator: " ")
    }

    var isManual: Bool { source.mode == "manual" }
    var isSimulation: Bool { source.mode == "simulation" }

    func isFresh(at now: Date) -> Bool {
        if isManual { return true }
        guard source.mode == "live" || isSimulation else { return false }
        return isStale != true && error == nil && (VehicleDate.parse(freshUntil).map { $0 > now } ?? false)
    }

    func fieldIsFresh(_ field: String, at now: Date) -> Bool {
        if isManual { return true }
        guard error == nil, isStale != true else { return false }
        // A successful HTTP request is not an observation from the car.
        let deadline = fieldFreshUntil?[field] ?? nil
        return VehicleDate.parse(deadline).map { $0 > now } ?? false
    }

    func availableEnergy(at now: Date) -> Double? {
        guard let percent = Self.valid(batteryPercent, in: 0...100),
              fieldIsFresh("batteryPercent", at: now) else { return nil }
        if !isManual, fieldIsFresh("usableEnergyKwh", at: now),
           let energy = Self.valid(usableEnergyKwh, in: 0...300) { return energy }
        guard isManual || ownerEnteredCapacity == true || fieldIsFresh("batteryCapacityKwh", at: now),
              let capacity = Self.valid(batteryCapacityKwh, in: 1...300) else { return nil }
        return capacity * percent / 100
    }

    static func valid(_ value: Double?, in range: ClosedRange<Double>) -> Double? {
        guard let value, value.isFinite, range.contains(value) else { return nil }
        return value
    }

    static func manual() -> OwnedVehicle {
        OwnedVehicle(id: "manual:" + UUID().uuidString, model: "", batteryPercent: 50,
                     source: VehicleSource(provider: "Owner", mode: "manual"))
    }
}

struct VehicleSpecs: Codable {
    var batteryCapacityKwh: Double?
    var maxAcKw: Double?
    var maxDcKw: Double?
    var connectors: [String] = []

    func applying(to vehicle: OwnedVehicle) -> OwnedVehicle {
        var result = vehicle
        if result.batteryCapacityKwh == nil {
            result.batteryCapacityKwh = OwnedVehicle.valid(batteryCapacityKwh, in: 1...300)
            result.ownerEnteredCapacity = result.batteryCapacityKwh != nil
        }
        if result.maxAcKw == nil { result.maxAcKw = OwnedVehicle.valid(maxAcKw, in: 0.1...100) }
        if result.maxDcKw == nil { result.maxDcKw = OwnedVehicle.valid(maxDcKw, in: 0.1...1000) }
        if result.connectors?.isEmpty != false {
            result.connectors = connectors.filter { VehiclePlug(rawValue: $0) != nil }
        }
        return result
    }
}

enum VehicleDate {
    static func parse(_ string: String?) -> Date? {
        guard let string else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: string) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: string)
    }

    static func string(_ date: Date = Date()) -> String { ISO8601DateFormatter().string(from: date) }
}

enum VehiclePlug: String, CaseIterable, Identifiable, Hashable {
    case ccs2 = "CCS2", type2 = "Type 2", chademo = "CHAdeMO", nacs = "NACS"
    case ccs1 = "CCS1", type1 = "Type 1"
    var id: String { rawValue }
    var isAC: Bool { self == .type1 || self == .type2 }

    init?(label: String) {
        switch ConnectorKind.canonical(label) {
        case "ccs2", "ccscombo2", "iec621963", "iec62196t3combo": self = .ccs2
        case "ccs1", "ccscombo1", "iec62196t1combo": self = .ccs1
        case "type2", "iec62196t2": self = .type2
        case "type1", "iec62196t1": self = .type1
        case "chademo": self = .chademo
        case "nacs": self = .nacs
        default: return nil
        }
    }
}

struct VehicleChargingEstimate {
    enum Compatibility: Equatable { case compatible, incompatible, unknown }
    var compatibility: Compatibility
    var batteryEnergyKwh: Double?
    var minimumMinutes: Int?
    var energyCostEUR: Double?
    var stale: Bool

    init(vehicle: OwnedVehicle, target: Double, station: Station, now: Date = Date()) {
        let owned = Set((vehicle.connectors ?? []).compactMap(VehiclePlug.init(label:)))
        let ports = station.connectors ?? []
        let labels = ports.map { $0.type ?? $0.label ?? "" } + (station.connectorLabels ?? [])
        let reported = Set(labels.compactMap(VehiclePlug.init(label:)))
        if owned.isEmpty || reported.isEmpty { compatibility = .unknown }
        else if !owned.isDisjoint(with: reported) { compatibility = .compatible }
        else if labels.contains(where: { VehiclePlug(label: $0) == nil }) { compatibility = .unknown }
        else { compatibility = .incompatible }

        let staleCapacity = vehicle.batteryCapacityKwh != nil && !vehicle.isManual && vehicle.ownerEnteredCapacity != true
            && !vehicle.fieldIsFresh("batteryCapacityKwh", at: now)
        stale = !vehicle.isFresh(at: now) || !vehicle.fieldIsFresh("batteryPercent", at: now) || staleCapacity
        if let percent = OwnedVehicle.valid(vehicle.batteryPercent, in: 0...100),
           vehicle.isManual || vehicle.ownerEnteredCapacity == true || vehicle.fieldIsFresh("batteryCapacityKwh", at: now),
           let capacity = OwnedVehicle.valid(vehicle.batteryCapacityKwh, in: 1...300),
           target.isFinite, (50...100).contains(target) {
            batteryEnergyKwh = capacity * max(0, target - percent) / 100
        }
        // Match power to a real compatible port, never a station's aggregate maximum.
        let powers: [Double] = ports.compactMap { port in
            guard let plug = VehiclePlug(label: port.type ?? port.label ?? ""), owned.contains(plug),
                  let power = OwnedVehicle.valid(port.powerKw, in: 0.1...1000)
            else { return nil }
            // NACS carries AC and DC; its shape alone does not identify a charging limit.
            if plug == .nacs && port.fast == nil { return nil }
            let isAC = plug == .nacs ? port.fast == false : plug.isAC
            guard let limit = OwnedVehicle.valid(isAC ? vehicle.maxAcKw : vehicle.maxDcKw, in: 0.1...1000) else { return nil }
            return min(power, limit)
        }
        // Planning assumption: 90% of energy drawn reaches the battery.
        if !stale, compatibility == .compatible, let energy = batteryEnergyKwh,
           let power = powers.max() {
            minimumMinutes = Int(ceil(energy / power * 60))
        }
        let hasCurrentWindows = station.priceWindowStatus?.currentWindows?.isEmpty == false
        let currentWindows = station.priceWindowStatus?.currentWindows ?? station.pricing ?? []
        let tariffs = currentWindows.compactMap { window -> Double? in
            guard (window.currency ?? station.priceCurrency)?.uppercased() == "EUR",
                  window.unit == nil || window.unit?.lowercased() == "kwh",
                  window.isCurrentlyValid != false && (hasCurrentWindows || window.isCurrentlyValid == true) else { return nil }
            return OwnedVehicle.valid(window.price, in: 0...10)
        }
        let fixedTariff = station.priceCurrency?.uppercased() == "EUR"
            && (station.priceNativeUnit == nil || station.priceNativeUnit?.lowercased() == "kwh") && currentWindows.isEmpty
            && station.priceWindowStatus == nil
        let tariff = tariffs.min() ?? (fixedTariff ? OwnedVehicle.valid(station.priceMin, in: 0...10) : nil)
        if !stale, compatibility != .incompatible, station.verification?.priceVerified == true,
           station.cacheStale != true, station.trustKind != .stale, station.trustKind != .unavailable,
           let tariff, let energy = batteryEnergyKwh {
            energyCostEUR = energy / 0.9 * tariff
        }
    }
}
