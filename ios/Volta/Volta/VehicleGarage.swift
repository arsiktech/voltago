import Foundation
import AuthenticationServices
import ImageIO
import SwiftUI
import UIKit
import CryptoKit

/// Owns only the phone's garage. Connected telemetry stays in memory; owner entries,
/// targets and selected photos persist locally. Server credentials never enter defaults.
@MainActor
final class VehicleGarage: ObservableObject {
    @Published private(set) var connection: VehicleConnection?
    @Published private(set) var manualVehicles: [OwnedVehicle]
    @Published private(set) var specs: [String: VehicleSpecs]
    @Published private(set) var isWorking = false
    @Published private(set) var isAuthorizing = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var photoRevision = 0
    @Published var selectedID: String {
        didSet { defaults.set(selectedID, forKey: "volta.vehicle.selected.v1") }
    }
    @Published var targetPercent: Double {
        didSet { defaults.set(targetPercent, forKey: "volta.vehicle.target.v1") }
    }

    private let defaults: UserDefaults
    private var service: VehicleConnectionService?
    private let authorization = VehicleAuthorization()
    private var generation = UUID()
    private var requestFailed = false
    private var cachedPhotos: [String: UIImage] = [:]
    private var missingPhotos: Set<String> = []

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        manualVehicles = defaults.data(forKey: "volta.vehicle.manual.v1")
            .flatMap { try? JSONDecoder().decode([OwnedVehicle].self, from: $0) }?
            .filter { $0.isManual && $0.id.hasPrefix("manual:") && $0.id.count < 100
                && OwnedVehicle.valid($0.batteryPercent, in: 0...100) != nil } ?? []
        specs = defaults.data(forKey: "volta.vehicle.specs.v1")
            .flatMap { try? JSONDecoder().decode([String: VehicleSpecs].self, from: $0) } ?? [:]
        selectedID = defaults.string(forKey: "volta.vehicle.selected.v1") ?? ""
        let target = defaults.double(forKey: "volta.vehicle.target.v1")
        targetPercent = target.isFinite && (50...100).contains(target) ? target : 80
    }

    var vehicles: [OwnedVehicle] {
        let connected = (connection?.vehicles ?? []).map { vehicle in
            var result = specs[vehicle.id]?.applying(to: vehicle) ?? vehicle
            if requestFailed || connection?.connectionStatus == "reconnect" { result.isStale = true }
            return result
        }
        return connected + manualVehicles
    }

    var selected: OwnedVehicle? { vehicles.first(where: { $0.id == selectedID }) ?? vehicles.first }
    var canConnect: Bool { connection?.configured == true && !isWorking && !isAuthorizing }
    var canRefresh: Bool {
        guard connection?.connected == true, !isWorking, !isAuthorizing else { return false }
        return VehicleDate.parse(connection?.refreshAllowedAt).map { $0 <= Date() } ?? true
    }

    /// The root restarts this task when the service or foreground state changes. Polling
    /// uses the backend's bounded cadence and never requests continuous vehicle wakeups.
    func run(baseURL: URL) async {
        if service?.baseURL != baseURL {
            authorization.cancel()
            generation = UUID()
            service?.invalidate()
            service = VehicleConnectionService(baseURL: baseURL)
            connection = nil
            requestFailed = false
            errorMessage = nil
        }
        await load()
        while !Task.isCancelled {
            let interval = max(60, min(300, connection?.refreshIntervalSeconds ?? 60))
            do { try await Task.sleep(for: .seconds(interval)) } catch { return }
            if Task.isCancelled { return }
            await load(refresh: canRefresh)
        }
    }

    func load(refresh: Bool = false) async {
        guard !isWorking, !isAuthorizing, let service else { return }
        let current = generation
        isWorking = true
        defer { isWorking = false }
        do {
            let result = try await service.request(refresh ? "/refresh" : "", body: refresh ? [:] : nil)
            guard generation == current, !Task.isCancelled else { return }
            connection = result
            requestFailed = false
            errorMessage = nil
        } catch {
            guard generation == current, !Task.isCancelled else { return }
            requestFailed = true
            errorMessage = String(localized: "Your car could not be updated. Try again.")
        }
    }

    func connect() async {
        guard canConnect, let service else { return }
        isAuthorizing = true
        errorMessage = nil
        let current = generation
        defer { isAuthorizing = false }
        do {
            let handoff = try await authorization.authorize(service: service)
            guard generation == current else { return }
            connection = try await service.request("/native/exchange", body: ["code": handoff.code, "verifier": handoff.verifier])
            requestFailed = false
            if let first = connection?.vehicles.first { selectedID = first.id }
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
            // Closing the system browser leaves manual entries and current readings intact.
        } catch {
            guard generation == current else { return }
            errorMessage = String(localized: "Your car could not be connected. Try again or enter its details.")
        }
    }

    func disconnect() async {
        guard !isWorking, !isAuthorizing, let service else { return }
        isWorking = true
        let current = generation
        defer { isWorking = false }
        do {
            let result = try await service.request("/disconnect", body: [:])
            guard current == generation else { return }
            connection = result
            requestFailed = false
            errorMessage = nil
        } catch {
            guard current == generation else { return }
            errorMessage = String(localized: "Could not disconnect. Try again.")
        }
    }

    func saveManual(_ input: OwnedVehicle) {
        var vehicle = input
        guard vehicle.isManual, OwnedVehicle.valid(vehicle.batteryPercent, in: 0...100) != nil else { return }
        vehicle.model = String((vehicle.model ?? "").trimmingCharacters(in: .whitespacesAndNewlines).prefix(80))
        vehicle.observedAt = VehicleDate.string()
        if let index = manualVehicles.firstIndex(where: { $0.id == vehicle.id }) { manualVehicles[index] = vehicle }
        else { manualVehicles.append(vehicle) }
        defaults.set(try? JSONEncoder().encode(manualVehicles), forKey: "volta.vehicle.manual.v1")
        selectedID = vehicle.id
    }

    func saveSpecs(_ value: VehicleSpecs, for id: String) {
        specs[id] = value
        defaults.set(try? JSONEncoder().encode(specs), forKey: "volta.vehicle.specs.v1")
    }

    func removeManual(_ vehicle: OwnedVehicle) {
        manualVehicles.removeAll { $0.id == vehicle.id }
        defaults.set(try? JSONEncoder().encode(manualVehicles), forKey: "volta.vehicle.manual.v1")
        removePhoto(for: vehicle.id)
    }

    func photo(for id: String) -> UIImage? {
        if let image = cachedPhotos[id] { return image }
        if missingPhotos.contains(id) { return nil }
        guard let url = photoURL(for: id) else { return nil }
        guard let image = UIImage(contentsOfFile: url.path) else { missingPhotos.insert(id); return nil }
        cachedPhotos[id] = image
        return image
    }

    func setPhoto(_ data: Data, for id: String) {
        // Downsample on import and re-encode pixels: no location/EXIF metadata is retained.
        guard data.count <= 25_000_000,
              let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 1600] as CFDictionary),
              let output = UIImage(cgImage: image).jpegData(compressionQuality: 0.85),
              var url = photoURL(for: id) else {
            errorMessage = String(localized: "This photo could not be opened. Choose another image.")
            return
        }
        do {
            var directory = url.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            var resources = URLResourceValues()
            resources.isExcludedFromBackup = true
            try directory.setResourceValues(resources)
            try output.write(to: url, options: [.atomic, .completeFileProtection])
            try url.setResourceValues(resources)
            cachedPhotos[id] = UIImage(data: output)
            missingPhotos.remove(id)
            photoRevision += 1
        } catch { errorMessage = String(localized: "This photo could not be saved. Try again.") }
    }

    func removePhoto(for id: String) {
        if let url = photoURL(for: id) { try? FileManager.default.removeItem(at: url) }
        cachedPhotos[id] = nil
        missingPhotos.insert(id)
        photoRevision += 1
    }

    private func photoURL(for id: String) -> URL? {
        guard let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else { return nil }
        let filename = SHA256.hash(data: Data(id.utf8)).map { String(format: "%02x", $0) }.joined()
        return directory.appendingPathComponent("VehiclePhotos", isDirectory: true).appendingPathComponent(filename + ".jpg")
    }
}
