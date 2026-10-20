import SwiftUI

struct VehicleEditorRequest: Identifiable {
    let vehicle: OwnedVehicle
    var id: String { vehicle.id }
}

struct VehicleEditorView: View {
    @EnvironmentObject private var garage: VehicleGarage
    @Environment(\.dismiss) private var dismiss
    let request: VehicleEditorRequest
    @State private var name: String
    @State private var battery: Double
    @State private var range: String
    @State private var capacity: String
    @State private var maxAC: String
    @State private var maxDC: String
    @State private var connectors: Set<String>

    init(request: VehicleEditorRequest) {
        self.request = request
        let vehicle = request.vehicle
        _name = State(initialValue: vehicle.name == String(localized: "My EV") ? "" : vehicle.name)
        _battery = State(initialValue: vehicle.batteryPercent ?? 50)
        _range = State(initialValue: Self.editable(vehicle.rangeKm))
        _capacity = State(initialValue: Self.editable(vehicle.batteryCapacityKwh))
        _maxAC = State(initialValue: Self.editable(vehicle.maxAcKw))
        _maxDC = State(initialValue: Self.editable(vehicle.maxDcKw))
        _connectors = State(initialValue: Set(vehicle.connectors ?? []))
    }

    var body: some View {
        NavigationStack {
            Form {
                if request.vehicle.isManual {
                    Section {
                        TextField("Car name", text: $name)
                            .textInputAutocapitalization(.words).frame(minHeight: 44)
                            .onChange(of: name) { _, value in if value.count > 80 { name = String(value.prefix(80)) } }
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("Battery")
                                Spacer()
                                Text(battery / 100, format: .percent.precision(.fractionLength(0))).monospacedDigit()
                            }
                            Slider(value: $battery, in: 0...100, step: 1) { Text("Battery level") }
                                .tint(Theme.actionInk).frame(minHeight: 44)
                        }
                        numberField(String(localized: "Remaining range (km)"), text: $range)
                    } header: {
                        Text("Your car")
                    } footer: {
                        Text("Manual entries are labeled not live. Update the battery level before planning a charge.")
                    }
                } else {
                    Section {
                        Text(request.vehicle.name).font(.headline)
                        Text("Fill in missing specifications from your car's manual. Manufacturer readings stay read-only.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                }

                Section {
                    numberField(String(localized: "Usable capacity (kWh)"), text: $capacity,
                                disabled: !request.vehicle.isManual && request.vehicle.ownerEnteredCapacity != true && request.vehicle.batteryCapacityKwh != nil)
                    numberField(String(localized: "Maximum AC power (kW)"), text: $maxAC,
                                disabled: hasProviderValue(\.maxAcKw))
                    numberField(String(localized: "Maximum DC power (kW)"), text: $maxDC,
                                disabled: hasProviderValue(\.maxDcKw))
                } header: {
                    Text("Charging specifications")
                } footer: {
                    Text("Use usable capacity, not the larger nominal pack size. Leave unknown specifications blank.")
                }

                Section {
                    ForEach(VehiclePlug.allCases) { plug in
                        Toggle(isOn: Binding(
                            get: { connectors.contains(plug.rawValue) },
                            set: { selected in
                                if selected { connectors.insert(plug.rawValue) }
                                else { connectors.remove(plug.rawValue) }
                            }
                        )) { Text(verbatim: plug.rawValue) }
                        .tint(Theme.action).frame(minHeight: 44)
                        .disabled(!request.vehicle.isManual && providerVehicle?.connectors?.isEmpty == false)
                    }
                } header: {
                    Text("Your connectors")
                } footer: {
                    Text("Choose only the connectors your car supports. Adapters are not assumed.")
                }

                if !valid {
                    Text("Enter a car name and valid numbers. Capacity: 1–300 kWh; range: 0–2,000 km; AC: 0.1–100 kW; DC: 0.1–1,000 kW.")
                        .font(.caption).foregroundStyle(Theme.danger)
                }
                if request.vehicle.isManual && garage.manualVehicles.contains(where: { $0.id == request.id }) {
                    Section {
                        Button("Remove car", role: .destructive) {
                            garage.removeManual(request.vehicle)
                            dismiss()
                        }
                        .frame(minHeight: 44)
                    }
                }
            }
            .navigationTitle(request.vehicle.isManual ? String(localized: "Car details") : String(localized: "Charging specifications"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel edit") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save car") { save(); dismiss() }.disabled(!valid)
                }
            }
        }
        .accessibilityIdentifier("vehicleEditor")
    }

    private var providerVehicle: OwnedVehicle? { garage.connection?.vehicles.first(where: { $0.id == request.id }) }
    private func hasProviderValue(_ keyPath: KeyPath<OwnedVehicle, Double?>) -> Bool {
        !request.vehicle.isManual && providerVehicle?[keyPath: keyPath] != nil
    }

    private func numberField(_ title: String, text: Binding<String>, disabled: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.subheadline).foregroundStyle(.secondary)
            TextField(text: text, prompt: Text("Not reported")) { Text(title) }
                .keyboardType(.decimalPad).monospacedDigit().frame(minHeight: 44)
                .disabled(disabled)
        }
    }

    private var valid: Bool {
        (!request.vehicle.isManual || !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            && validNumber(capacity, in: 1...300) && validNumber(range, in: 0...2000)
            && validNumber(maxAC, in: 0.1...100) && validNumber(maxDC, in: 0.1...1000)
    }

    private func save() {
        if request.vehicle.isManual {
            var vehicle = request.vehicle
            vehicle.make = nil
            vehicle.model = name
            vehicle.batteryPercent = battery
            vehicle.rangeKm = Self.number(range)
            vehicle.batteryCapacityKwh = Self.number(capacity)
            vehicle.maxAcKw = Self.number(maxAC)
            vehicle.maxDcKw = Self.number(maxDC)
            vehicle.connectors = connectors.sorted()
            garage.saveManual(vehicle)
        } else {
            garage.saveSpecs(VehicleSpecs(batteryCapacityKwh: Self.number(capacity), maxAcKw: Self.number(maxAC),
                                         maxDcKw: Self.number(maxDC), connectors: connectors.sorted()), for: request.id)
        }
    }

    private func validNumber(_ string: String, in bounds: ClosedRange<Double>) -> Bool {
        string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || OwnedVehicle.valid(Self.number(string), in: bounds) != nil
    }

    private static func number(_ string: String) -> Double? {
        Double(string.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: ",", with: "."))
    }

    private static func editable(_ number: Double?) -> String {
        number.map { $0.formatted(.number.grouping(.never).precision(.fractionLength(0...2))) } ?? ""
    }
}
