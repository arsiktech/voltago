import SwiftUI

/// Planning information follows the station's existing actions. The estimate never changes
/// the station decision, filters, source warnings, or whether Maps can be the primary action.
struct VehicleStationEstimateView: View {
    @EnvironmentObject private var garage: VehicleGarage
    let station: Station

    var body: some View {
        if let vehicle = garage.selected {
            Section("For your EV") {
                TimelineView(.periodic(from: .now, by: 30)) { context in
                    estimate(vehicle: vehicle, now: context.date)
                }
            }
        }
    }

    private func estimate(vehicle: OwnedVehicle, now: Date) -> some View {
        let estimate = VehicleChargingEstimate(vehicle: vehicle, target: garage.targetPercent, station: station, now: now)
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(vehicle.name).font(.headline).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Text(garage.targetPercent / 100, format: .percent.precision(.fractionLength(0)))
                    .font(.headline).monospacedDigit()
            }
            VehicleStatusLabel(vehicle: vehicle, now: now)
            Label(compatibilityText(estimate.compatibility), systemImage: compatibilityIcon(estimate.compatibility))
                .font(.subheadline.weight(.medium))
                .foregroundStyle(estimate.compatibility == .incompatible ? Theme.danger : Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            if estimate.stale {
                Text("Refresh your car before estimating this stop.")
                    .font(.subheadline).foregroundStyle(.secondary)
                Button("Refresh car") { Task { await garage.load(refresh: true) } }
                    .buttonStyle(.bordered).tint(Theme.actionInk).frame(minHeight: 44)
                    .disabled(!garage.canRefresh)
            } else if let energy = estimate.batteryEnergyKwh {
                LabeledContent("Energy to target") {
                    Text("\(energy.formatted(.number.precision(.fractionLength(1)))) kWh").monospacedDigit()
                }
                LabeledContent("Minimum time") {
                    if let minutes = estimate.minimumMinutes {
                        Text("At least \(minutes) min").monospacedDigit()
                    } else { Text("Unknown") }
                }
                LabeledContent("Energy cost") {
                    if let cost = estimate.energyCostEUR {
                        Text(cost, format: .currency(code: "EUR")).monospacedDigit()
                    } else { Text("Unknown") }
                }
                Text("Planning estimate. Time assumes peak power; charging slows near full. Energy cost includes an assumed 10% charging loss, excluding parking, time and session fees.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if estimate.minimumMinutes == nil {
                    Text("Time needs a matching port's power and your car's AC or DC limit.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text("Add your battery level and usable capacity to estimate this stop.")
                    .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            NavigationLink {
                VehicleGarageView()
            } label: {
                Text("Manage car").font(.subheadline.weight(.medium)).frame(minHeight: 44)
            }
            .tint(Theme.actionInk)
        }
        .foregroundStyle(Theme.ink)
        .padding(.vertical, 6)
        .accessibilityIdentifier("vehicleChargingEstimate")
    }

    private func compatibilityText(_ value: VehicleChargingEstimate.Compatibility) -> String {
        switch value {
        case .compatible: String(localized: "Matching connector reported")
        case .incompatible: String(localized: "No matching connector reported")
        case .unknown: String(localized: "Connector compatibility unknown")
        }
    }

    private func compatibilityIcon(_ value: VehicleChargingEstimate.Compatibility) -> String {
        switch value {
        case .compatible: "powerplug"
        case .incompatible: "powerplug.portrait.fill"
        case .unknown: "questionmark.circle"
        }
    }
}
