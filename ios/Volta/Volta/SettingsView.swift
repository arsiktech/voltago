import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dismiss) private var dismiss

    #if DEBUG
    @State private var urlText = ""
    @State private var testResult: String?
    @State private var testing = false

    private let presets: [(label: String, detail: String, url: String)] = [
        ("Mac on the same network", "replace your-mac with your Mac's Bonjour name",
         "http://your-mac.local:8773"),
        ("Simulator (localhost)", "only inside the iOS Simulator",
         "http://127.0.0.1:8773"),
    ]

    var body: some View {
        NavigationStack {
            Form {
                dataStatusSection

                Section {
                    TextField(
                        text: $urlText,
                        prompt: Text(verbatim: "http://host:8773")
                    ) {
                        Text(verbatim: "Backend server address")
                    }
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.system(.subheadline, design: .monospaced))
                        .accessibilityLabel(Text(verbatim: "Backend server address"))
                        .accessibilityHint(Text(verbatim: "Enter the Volta server address and port, or choose a preset below"))
                } header: {
                    Text(verbatim: "Backend address")
                } footer: {
                    Text(verbatim: "The Volta server must be running on the Mac:\nHOST=0.0.0.0 PORT=8773 python3 server.py")
                        .font(.caption2)
                }

                Section {
                    ForEach(presets, id: \.url) { preset in
                        Button {
                            urlText = preset.url
                            testResult = nil
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                HStack {
                                    Text(verbatim: preset.label).font(.subheadline.weight(.medium))
                                    if urlText == preset.url {
                                        Spacer()
                                        Image(systemName: "checkmark").foregroundStyle(Theme.brand)
                                    }
                                }
                                Text(verbatim: preset.detail).font(.caption).foregroundStyle(.secondary)
                                Text(verbatim: preset.url)
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(.tertiary)
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel(Text(verbatim: "\(preset.label). \(preset.detail)"))
                        .accessibilityHint(Text(verbatim: "Sets the backend address to \(preset.url)"))
                        .accessibilityAddTraits(urlText == preset.url ? .isSelected : [])
                    }
                } header: {
                    Text(verbatim: "Presets")
                }

                Section {
                    Button {
                        testing = true
                        testResult = nil
                        let candidate = urlText
                        Task {
                            let saved = state.baseURLString
                            state.baseURLString = candidate
                            let result = await state.testConnection()
                            state.baseURLString = saved
                            testResult = result
                            testing = false
                        }
                    } label: {
                        HStack {
                            if testing { ProgressView().padding(.trailing, 4) }
                            Text(verbatim: testing ? "Testing…" : "Test connection")
                        }
                    }
                    .disabled(testing || urlText.isEmpty)
                    .accessibilityLabel(Text(verbatim: testing ? "Testing connection" : "Test connection"))
                    .accessibilityHint(Text(verbatim: "Checks whether the Volta server responds at this address"))

                    if let result = testResult {
                        Text(verbatim: result)
                            .font(.caption)
                            .foregroundStyle(result.hasPrefix("✓") ? Theme.go : Theme.danger)
                    }
                }

                legalSection
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: LegalDocument.self) { LegalDocumentView(document: $0) }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button { dismiss() } label: { Text(verbatim: "Cancel") }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        state.baseURLString = urlText.trimmingCharacters(in: .whitespaces)
                        state.baseURLChanged()
                        dismiss()
                    } label: {
                        Text(verbatim: "Save")
                    }
                    .disabled(URL(string: urlText) == nil)
                }
            }
            .onAppear { urlText = state.baseURLString }
        }
    }
    #else
    var body: some View {
        NavigationStack {
            Form {
                dataStatusSection

                Section("Service") {
                    Label("Volta service", systemImage: "network")
                        .font(.headline)
                    Text(state.isServiceConfigured
                         ? "Secure service configured"
                         : "This build is waiting for its secure Volta service configuration.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)

                legalSection

                Section("About") {
                    LabeledContent("Version") {
                        Text(versionAndBuild).monospacedDigit()
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: LegalDocument.self) { LegalDocumentView(document: $0) }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private var versionAndBuild: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "—"
        let build = info?["CFBundleVersion"] as? String ?? "—"
        return "\(version) (\(build))"
    }

    #endif

    private var legalSection: some View {
        Section("Legal") {
            NavigationLink(value: LegalDocument.terms) {
                Label("Terms of Use", systemImage: "doc.text")
            }
            .accessibilityIdentifier("settingsTermsLink")

            NavigationLink(value: LegalDocument.privacy) {
                Label("Privacy", systemImage: "hand.raised")
            }
            .accessibilityIdentifier("settingsPrivacyLink")

            Link(destination: URL(string: "mailto:arsik2121@icloud.com?subject=Volta%20Go%20Support")!) {
                Label("Support", systemImage: "questionmark.circle")
            }
            .accessibilityIdentifier("settingsSupportLink")
        }
    }

    private var dataStatusSection: some View {
        Section("Data status") {
            Label(dataStatusTitle, systemImage: dataStatusSymbol)
                .font(.headline)
                .accessibilityIdentifier("settingsDataStatus")
            if let warning = state.warning {
                Text(warning)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("settingsDataWarning")
            } else {
                Text("No current data warnings.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            if state.mode == .live {
                Button("Refresh data") { state.fetchLive(reason: "settings") }
            }
        }
    }

    private var dataStatusTitle: String {
        switch state.liveQuality {
        case .fresh: String(localized: "Live data is current")
        case .degraded: String(localized: "Live coverage is limited")
        case .mixed: String(localized: "Live and cached data")
        case .stale: String(localized: "Data needs refreshing")
        case .unavailable: String(localized: "Live data is unavailable")
        case .loading: String(localized: "Refreshing data")
        case .overview: String(localized: "Verified charger database")
        }
    }

    private var dataStatusSymbol: String {
        switch state.liveQuality {
        case .fresh: "checkmark.shield"
        case .loading: "arrow.trianglehead.2.clockwise.rotate.90"
        case .overview: "externaldrive.badge.checkmark"
        case .degraded, .mixed, .stale, .unavailable: "info.circle"
        }
    }
}
