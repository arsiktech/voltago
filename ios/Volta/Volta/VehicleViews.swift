import PhotosUI
import SwiftUI

/// A small entry beside discovery: useful car context without taking space from chargers.
struct VehicleSummaryCard: View {
    @EnvironmentObject private var garage: VehicleGarage
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { summary(at: context.date) }
                VStack(alignment: .leading, spacing: 8) { summary(at: context.date) }
            }
            .padding(14)
            .voltaCard()
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("yourEVSummary")
    }

    @ViewBuilder
    private func summary(at now: Date) -> some View {
        Image(systemName: "car.side")
            .font(.title2)
            .foregroundStyle(Theme.actionInk)
            .accessibilityHidden(true)
        VStack(alignment: .leading, spacing: 3) {
            Text("Your EV").font(.caption).foregroundStyle(.secondary)
            Text(garage.selected?.name ?? String(localized: "A little more connected."))
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            if let vehicle = garage.selected {
                VehicleStatusLabel(vehicle: vehicle, now: now)
            } else {
                Text("Your battery. Your next stop.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        Spacer(minLength: 0)
        if let percent = OwnedVehicle.valid(garage.selected?.batteryPercent, in: 0...100) {
            Text(percent / 100, format: .percent.precision(.fractionLength(0)))
                .font(.title3.weight(.semibold)).monospacedDigit().foregroundStyle(Theme.ink)
        }
        Image(systemName: "chevron.right")
            .font(.caption.weight(.semibold)).foregroundStyle(.secondary).accessibilityHidden(true)
    }
}

struct VehicleGarageView: View {
    @EnvironmentObject private var garage: VehicleGarage
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var editor: VehicleEditorRequest?
    @State private var photoItem: PhotosPickerItem?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let vehicle = garage.selected {
                    TimelineView(.periodic(from: .now, by: 30)) { context in
                        vehicleCard(vehicle, now: context.date)
                    }
                    targetCard
                } else {
                    emptyGarage
                }
                connectionCard
                if let message = garage.errorMessage {
                    Label(message, systemImage: "exclamationmark.circle")
                        .font(.subheadline).foregroundStyle(Theme.danger)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("vehicleError")
                    Button("Try again") { Task { await garage.load() } }
                        .buttonStyle(.bordered).tint(Theme.actionInk).frame(minHeight: 44)
                }
            }
            .padding(16)
            .frame(maxWidth: 620)
            .frame(maxWidth: .infinity)
        }
        .background(Theme.canvas)
        .navigationTitle("Your EV")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.visible, for: .navigationBar)
        .sheet(item: $editor) { request in VehicleEditorView(request: request) }
        .task(id: photoItem) {
            guard let item = photoItem, let id = garage.selected?.id else { return }
            if !Task.isCancelled {
                let data = try? await item.loadTransferable(type: Data.self)
                if !Task.isCancelled { garage.setPhoto(data ?? Data(), for: id) }
            }
            photoItem = nil
        }
        .accessibilityIdentifier("vehicleGarage")
    }

    private func vehicleCard(_ vehicle: OwnedVehicle, now: Date) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(vehicle.name).font(.title2.weight(.semibold)).foregroundStyle(Theme.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    VehicleStatusLabel(vehicle: vehicle, now: now)
                }
                Spacer(minLength: 0)
                if garage.vehicles.count > 1 {
                    Menu {
                        ForEach(garage.vehicles) { car in
                            Button {
                                garage.selectedID = car.id
                            } label: {
                                if garage.selected?.id == car.id { Label(car.name, systemImage: "checkmark") }
                                else { Text(car.name) }
                            }
                        }
                    } label: {
                        Image(systemName: "chevron.up.chevron.down")
                            .frame(width: 44, height: 44)
                            .background(Theme.surface, in: .circle)
                            .overlay { Circle().strokeBorder(Theme.hairlineStrong, lineWidth: 1) }
                    }
                    .tint(Theme.actionInk).accessibilityLabel("Choose your car")
                }
            }

            VehiclePhoto(vehicleID: vehicle.id)
            if garage.photo(for: vehicle.id) == nil {
                Text("Vehicle illustration · add your own photo below")
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            VehicleMetricsView(vehicle: vehicle, now: now)
            Divider().overlay(Theme.hairline)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { carActions(vehicle) }
                VStack(alignment: .leading, spacing: 8) { carActions(vehicle) }
            }
            if let observed = VehicleDate.parse(vehicle.observedAt) {
                let relative = observed.formatted(.relative(presentation: .numeric, unitsStyle: .abbreviated))
                Text(vehicle.isManual ? String(localized: "Entered \(relative)") : String(localized: "Observed \(relative)"))
                .font(.caption).foregroundStyle(.secondary)
                .accessibilityElement(children: .combine)
            }
        }
        .padding(20)
        .voltaCard(radius: 24)
    }

    @ViewBuilder
    private func carActions(_ vehicle: OwnedVehicle) -> some View {
        PhotosPicker(selection: $photoItem, matching: .images, photoLibrary: .shared()) {
            Label("Add photo", systemImage: "camera")
                .font(.subheadline.weight(.medium)).frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered).tint(Theme.actionInk)
        .accessibilityHint("The photo is stored on this iPhone only")
        Button {
            editor = VehicleEditorRequest(vehicle: vehicle)
        } label: {
            Label(vehicle.isManual ? String(localized: "Edit car") : String(localized: "Car details"), systemImage: "slider.horizontal.3")
                .font(.subheadline.weight(.medium)).frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered).tint(Theme.actionInk)
        if garage.photo(for: vehicle.id) != nil {
            Button {
                garage.removePhoto(for: vehicle.id)
            } label: {
                Image(systemName: "trash").frame(width: 44, height: 44)
            }
            .buttonStyle(.bordered).tint(Theme.actionInk).accessibilityLabel("Remove car photo")
        }
    }

    private var targetCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Charge to").font(.headline)
                Spacer()
                Text(garage.targetPercent / 100, format: .percent.precision(.fractionLength(0)))
                    .font(.title3.weight(.semibold)).monospacedDigit()
            }
            Slider(value: $garage.targetPercent, in: 50...100, step: 5) {
                Text("Target battery level")
            }
            .tint(Theme.actionInk).frame(minHeight: 44)
            .accessibilityValue(Text(garage.targetPercent / 100, format: .percent.precision(.fractionLength(0))))
            Text("Choose a charger to see the energy, minimum charging time and estimated energy cost for this target.")
                .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(20).voltaCard()
    }

    private var emptyGarage: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Your battery. Your next stop.")
                .font(.largeTitle.weight(.semibold)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            Image("VehicleIllustration").resizable().scaledToFit()
                .accessibilityLabel("Vehicle illustration")
            Text("Bring your car into the picture.")
                .font(.headline).foregroundStyle(Theme.ink)
            Text("See your battery, remaining range and energy before you choose where to charge.")
                .font(.body).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(20).voltaCard(radius: 24)
    }

    private var connectionCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(garage.connection?.connected == true ? String(localized: "Connected car") : String(localized: "Make it yours"))
                .font(.headline).foregroundStyle(Theme.ink)
            if garage.connection == nil && garage.isWorking {
                ProgressView("Checking vehicle connection…")
                    .font(.subheadline).tint(Theme.actionInk)
            }
            if garage.connection?.configured == false {
                Text("Live connection is unavailable here. Enter your car's details to plan a charge.")
                    .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("Connect securely through Smartcar. You choose the car and approve read-only access to its battery and charging data.")
                    .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if garage.connection?.mode == "simulation" {
                Label("Simulation · sample provider data", systemImage: "testtube.2")
                    .font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            }
            if garage.connection?.connectionStatus == "reconnect" {
                Label("Reconnect to get new readings", systemImage: "link.badge.plus")
                    .font(.subheadline.weight(.medium)).foregroundStyle(Theme.check)
            }
            if garage.connection?.connected != true || garage.connection?.connectionStatus == "reconnect" {
                Button {
                    Task { await garage.connect() }
                } label: {
                    HStack(spacing: 8) {
                        if garage.isAuthorizing { ProgressView().tint(.white) }
                        Text(garage.isAuthorizing ? String(localized: "Connecting…") : String(localized: "Connect car"))
                    }
                    .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent).tint(Theme.action)
                .disabled(!garage.canConnect)
                .accessibilityIdentifier("connectVehicle")
            }
            Button {
                editor = VehicleEditorRequest(vehicle: .manual())
            } label: {
                Label("Enter car details", systemImage: "plus")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered).tint(Theme.actionInk)
            .accessibilityIdentifier("enterVehicleDetails")
            if garage.connection?.connected == true {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) { connectionActions }
                    VStack(spacing: 8) { connectionActions }
                }
                Text("Updates about every minute while the app is open. Your manufacturer may report data later.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Text("Your photo and manual entries stay on this iPhone. Connected readings pass through the Volta service; disconnecting removes its stored vehicle session.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(20).voltaCard()
    }

    @ViewBuilder
    private var connectionActions: some View {
        Button {
            Task { await garage.load(refresh: true) }
        } label: {
            Label(garage.isWorking ? String(localized: "Refreshing…") : String(localized: "Refresh car"), systemImage: "arrow.clockwise")
                .frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered).tint(Theme.actionInk).disabled(!garage.canRefresh)
        Button {
            Task { await garage.disconnect() }
        } label: {
            Text("Disconnect").frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered).tint(Theme.actionInk).disabled(garage.isWorking || garage.isAuthorizing)
    }
}

private struct VehiclePhoto: View {
    @EnvironmentObject private var garage: VehicleGarage
    let vehicleID: String

    var body: some View {
        Group {
            if let image = garage.photo(for: vehicleID) {
                Image(uiImage: image).resizable().scaledToFill()
                    .frame(height: 170).clipped().accessibilityLabel("Your car photo")
            } else {
                Image("VehicleIllustration").resizable().scaledToFit().accessibilityLabel("Vehicle illustration")
            }
        }
        .frame(maxWidth: .infinity)
        .clipShape(.rect(cornerRadius: 16, style: .continuous))
        .id("\(vehicleID)-\(garage.photoRevision)")
    }
}

struct VehicleStatusLabel: View {
    let vehicle: OwnedVehicle
    let now: Date

    var body: some View {
        Label {
            if vehicle.isManual { Text("Manual entry · not live") }
            else if vehicle.isSimulation { Text("Simulation · sample provider data") }
            else if vehicle.isFresh(at: now) { Text("Live · Smartcar") }
            else { Text("Last known reading") }
        } icon: {
            Image(systemName: vehicle.isManual ? "pencil" : vehicle.isFresh(at: now) ? "link" : "clock")
        }
        .font(.caption.weight(.medium)).foregroundStyle(.secondary)
        .fixedSize(horizontal: false, vertical: true)
    }
}

private struct VehicleMetricsView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let vehicle: OwnedVehicle
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Battery").font(.subheadline).foregroundStyle(.secondary)
                if let percent = OwnedVehicle.valid(vehicle.batteryPercent, in: 0...100) {
                    Text(percent / 100, format: .percent.precision(.fractionLength(0)))
                        .font(.largeTitle.weight(.semibold)).monospacedDigit().foregroundStyle(Theme.ink)
                        .contentTransition(reduceMotion ? .identity : .numericText())
                        .animation(reduceMotion ? nil : VoltaMotion.state, value: percent)
                    GeometryReader { geometry in
                        Capsule().fill(Theme.hairline)
                        Capsule().fill(Theme.ink).frame(width: geometry.size.width * percent / 100)
                    }
                    .frame(height: 8).accessibilityHidden(true)
                    if !vehicle.fieldIsFresh("batteryPercent", at: now) {
                        Text("Last known battery level").font(.caption).foregroundStyle(.secondary)
                    }
                } else { Text("Not reported").font(.title3).foregroundStyle(.secondary) }
            }
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 14) { secondaryMetrics }
            } else {
                HStack(alignment: .top, spacing: 18) { secondaryMetrics }
            }
            Label(chargingText, systemImage: vehicle.chargingState == "charging" ? "bolt.fill" : "powerplug")
                .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let power = OwnedVehicle.valid(vehicle.chargePowerKw, in: 0...1000),
               vehicle.fieldIsFresh("chargePowerKw", at: now) {
                Text("Charging power: \(power.formatted(.number.precision(.fractionLength(1)))) kW")
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
        }
    }

    @ViewBuilder
    private var secondaryMetrics: some View {
        metric(title: String(localized: "Estimated available"),
               value: vehicle.availableEnergy(at: now).map { $0.formatted(.number.precision(.fractionLength(1))) + " kWh" })
        metric(title: String(localized: "Remaining range"),
               value: OwnedVehicle.valid(vehicle.rangeKm, in: 0...2000).map { $0.formatted(.number.precision(.fractionLength(0))) + " km" },
               stale: vehicle.rangeKm != nil && !vehicle.fieldIsFresh("rangeKm", at: now))
        metric(title: vehicle.ownerEnteredCapacity == true ? String(localized: "Usable pack · you") : String(localized: "Usable pack"),
               value: OwnedVehicle.valid(vehicle.batteryCapacityKwh, in: 1...300).map { $0.formatted(.number.precision(.fractionLength(1))) + " kWh" })
    }

    private func metric(title: String, value: String?, stale: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text(value ?? String(localized: "Not reported"))
                .font(.headline).foregroundStyle(Theme.ink).monospacedDigit()
                .fixedSize(horizontal: false, vertical: true)
            if stale { Text("Last known").font(.caption2).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private var chargingText: String {
        if !vehicle.isManual && !vehicle.fieldIsFresh("chargingState", at: now) {
            return String(localized: "Charging status needs updating")
        }
        switch vehicle.chargingState {
        case "charging": return String(localized: "Charging")
        case "fully_charged": return String(localized: "Fully charged")
        case "not_charging": return String(localized: "Not charging")
        case "unplugged": return String(localized: "Unplugged")
        case "plugged_in": return String(localized: "Plugged in")
        default: return String(localized: "Charging status not reported")
        }
    }
}
