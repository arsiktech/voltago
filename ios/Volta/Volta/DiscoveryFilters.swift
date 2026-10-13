import Foundation
import SwiftUI

/// One row of filter chips under search: Free, maximum price, connector and speed.
/// A chip that narrows the results turns ink with a checkmark; nothing here is green,
/// because green only ever means a charger is available now.
struct MapFilterBar: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var filterFeedback = false
    @State private var priceEditorPresented = false
    @State private var powerEditorPresented = false

    private let connectorTypes = ConnectorKind.choices

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                Button {
                    state.freeOnly.toggle()
                    filterFeedback.toggle()
                } label: {
                    FilterChipLabel(title: String(localized: "Free"), systemImage: nil, active: state.freeOnly)
                }
                .buttonStyle(VoltaPressButtonStyle(pressedScale: 0.96))
                .accessibilityLabel("Free chargers only")
                .accessibilityValue(state.freeOnly ? "On" : "Off")
                .accessibilityAddTraits(state.freeOnly ? .isSelected : [])
                .accessibilityIdentifier("freeFilterChip")

                filterButton(
                    title: priceLabel,
                    systemImage: "eurosign",
                    active: state.maxPrice < 4.99,
                    accessibilityLabel: "Maximum price"
                ) {
                    priceEditorPresented = true
                }
                .popover(isPresented: $priceEditorPresented) {
                    FilterSliderEditor(
                        title: "Maximum price",
                        systemImage: "eurosign.circle.fill",
                        value: state.maxPrice >= 4.99 ? Self.noPriceLimit : min(state.maxPrice, 1.0),
                        range: 0...Self.noPriceLimit,
                        step: 0.05,
                        minimumLabel: "Free",
                        maximumLabel: "No limit",
                        accessibilityIdentifier: "maximumPriceSlider",
                        valueText: { value in
                            if value > 1.0 { return String(localized: "No price limit") }
                            if value == 0 { return String(localized: "Free only") }
                            return String(localized: "Up to \(value.formatted(.currency(code: "EUR")))/kWh")
                        }
                    ) { value in
                        let price = value > 1.0 ? 5.0 : value
                        if state.maxPrice != price {
                            state.maxPrice = price
                            filterFeedback.toggle()
                        }
                    }
                    .presentationCompactAdaptation(compactAdaptation)
                }

                Menu {
                    Button {
                        state.connectorTypes.removeAll()
                        filterFeedback.toggle()
                    } label: {
                        Label("Any connector", systemImage: state.connectorTypes.isEmpty ? "checkmark" : "circle")
                    }

                    Divider()

                    ForEach(connectorTypes, id: \.self) { connector in
                        Toggle(
                            connector,
                            isOn: Binding(
                                get: { state.connectorTypes.contains(connector) },
                                set: { selected in
                                    if selected != state.connectorTypes.contains(connector) {
                                        state.toggleConnectorType(connector)
                                        filterFeedback.toggle()
                                    }
                                }
                            )
                        )
                    }
                } label: {
                    FilterChipLabel(title: connectorLabel, systemImage: "ev.plug.ac.type.2", active: !state.connectorTypes.isEmpty)
                }
                .menuActionDismissBehavior(.disabled)
                .buttonStyle(VoltaPressButtonStyle(pressedScale: 0.96))
                .accessibilityLabel("Connector types")
                .accessibilityValue(connectorLabel)

                filterButton(
                    title: powerLabel,
                    systemImage: "bolt.fill",
                    active: state.minimumPowerKW > 0,
                    accessibilityLabel: "Minimum charging speed"
                ) {
                    powerEditorPresented = true
                }
                .popover(isPresented: $powerEditorPresented) {
                    FilterSliderEditor(
                        title: "Minimum power",
                        systemImage: "bolt.circle.fill",
                        value: state.minimumPowerKW,
                        range: 0...250,
                        step: 5,
                        minimumLabel: "Any",
                        maximumLabel: "250 kW",
                        accessibilityIdentifier: "minimumPowerSlider",
                        valueText: { $0 == 0 ? String(localized: "Any charging speed") : String(localized: "At least \(Int($0)) kW") }
                    ) { value in
                        if state.minimumPowerKW != value {
                            state.minimumPowerKW = value
                            filterFeedback.toggle()
                        }
                    }
                    .presentationCompactAdaptation(compactAdaptation)
                }
            }
            .padding(.horizontal, 16)
        }
        .scrollClipDisabled()
        .sensoryFeedback(.selection, trigger: filterFeedback)
        .accessibilityIdentifier("mapFilterBar")
    }

    /// Large accessibility text gets a sheet with room for the slider and its labels.
    private var compactAdaptation: PresentationAdaptation {
        dynamicTypeSize.isAccessibilitySize ? .sheet : .popover
    }

    /// The slider's last stop, past €1.00/kWh, means no limit (as on the web).
    static let noPriceLimit = 1.05

    private var priceLabel: String {
        if state.maxPrice >= 4.99 { return String(localized: "Any price") }
        if state.maxPrice == 0 { return String(localized: "Free") }
        return String(localized: "Up to \(state.maxPrice.formatted(.currency(code: "EUR")))")
    }

    private var connectorLabel: String {
        switch state.connectorTypes.count {
        case 0: String(localized: "Any plug")
        case 1: state.connectorTypes.first ?? String(localized: "Any plug")
        default: String(localized: "\(state.connectorTypes.count) plugs")
        }
    }

    private var powerLabel: String {
        state.minimumPowerKW == 0 ? String(localized: "Any speed") : String(localized: "\(Int(state.minimumPowerKW)) kW+")
    }

    private func filterButton(
        title: String,
        systemImage: String,
        active: Bool,
        accessibilityLabel: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            FilterChipLabel(title: title, systemImage: systemImage, active: active, disclosure: true)
        }
        .buttonStyle(VoltaPressButtonStyle(pressedScale: 0.96))
        .accessibilityLabel(accessibilityLabel)
        .accessibilityValue(title)
    }
}

/// A 36-point capsule inside a 44-point target. Every chip in the row shares that height.
/// The leading slot is reserved, so turning a chip on swaps its icon for a checkmark
/// without moving the label.
private struct FilterChipLabel: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let title: String
    let systemImage: String?
    let active: Bool
    var disclosure = false

    var body: some View {
        HStack(spacing: 6) {
            if active {
                Image(systemName: "checkmark")
                    .font(.caption.weight(.bold))
            } else if let systemImage {
                Image(systemName: systemImage)
                    .font(.caption.weight(.semibold))
            }
            Text(title)
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
                .contentTransition(.numericText())
                .animation(reduceMotion ? nil : VoltaMotion.state, value: title)
            if disclosure {
                Image(systemName: "chevron.down")
                    .font(.caption2.weight(.bold))
                    .opacity(0.7)
            }
        }
        // The row scrolls sideways, so a chip takes its full label size and never truncates.
        .fixedSize()
        .foregroundStyle(active ? Color.white : Theme.ink)
        .padding(.horizontal, 14)
        .frame(minHeight: 36)
        .background(active ? Theme.action : Theme.surface, in: .capsule)
        .overlay { Capsule().strokeBorder(active ? Theme.action : Theme.hairline, lineWidth: 1) }
        .frame(minHeight: 44)
        .contentShape(.rect)
        .animation(VoltaMotion.state, value: active)
    }
}

private struct FilterSliderEditor: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let title: LocalizedStringKey
    let systemImage: String
    let range: ClosedRange<Double>
    let step: Double
    let minimumLabel: LocalizedStringKey
    let maximumLabel: LocalizedStringKey
    let accessibilityIdentifier: String
    let valueText: (Double) -> String
    let onCommit: (Double) -> Void
    @State private var draft: Double

    init(
        title: LocalizedStringKey,
        systemImage: String,
        value: Double,
        range: ClosedRange<Double>,
        step: Double,
        minimumLabel: LocalizedStringKey,
        maximumLabel: LocalizedStringKey,
        accessibilityIdentifier: String,
        valueText: @escaping (Double) -> String,
        onCommit: @escaping (Double) -> Void
    ) {
        self.title = title
        self.systemImage = systemImage
        self.range = range
        self.step = step
        self.minimumLabel = minimumLabel
        self.maximumLabel = maximumLabel
        self.accessibilityIdentifier = accessibilityIdentifier
        self.valueText = valueText
        self.onCommit = onCommit
        _draft = State(initialValue: value)
    }

    private var heading: some View {
        HStack(spacing: 10) {
            Image(systemName: systemImage)
                .font(.title2)
                .foregroundStyle(Theme.actionInk)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                Text(valueText(draft))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                    .contentTransition(.numericText())
            }
        }
    }

    private var doneButton: some View {
        Button {
            onCommit(draft)
            dismiss()
        } label: {
            Text("Done")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: dynamicTypeSize.isAccessibilitySize ? .infinity : nil, minHeight: 44)
                .frame(minWidth: 44)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 8) {
                    heading
                    doneButton
                        .buttonStyle(VoltaActionButtonStyle(prominent: true))
                }
            } else {
                HStack(spacing: 10) {
                    heading
                    Spacer()
                    doneButton
                }
            }

            Slider(
                value: $draft,
                in: range,
                step: step,
                onEditingChanged: { editing in
                    if !editing { onCommit(draft) }
                }
            )
            .tint(Theme.action)
            .accessibilityLabel(title)
            .accessibilityValue(valueText(draft))
            .accessibilityIdentifier(accessibilityIdentifier)

            HStack {
                Text(minimumLabel)
                Spacer()
                Text(maximumLabel)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(18)
        .frame(minWidth: 300, idealWidth: 330, maxWidth: dynamicTypeSize.isAccessibilitySize ? .infinity : 360)
        .presentationDetents([.medium, .large])
    }
}
