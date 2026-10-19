import Foundation
import SwiftUI

// MARK: - Result card

/// A result in the sheet, mirroring the web `StationCard`: name and place on the left,
/// the charging price on the right; then decision, source and tariff badges in one
/// wrapping row; parking and access; availability and route facts.
struct StationCard: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let station: Station
    var selected = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if dynamicTypeSize.isAccessibilitySize {
                identity
                StationPriceBlock(station: station, alignment: .leading)
            } else {
                HStack(alignment: .top, spacing: 12) {
                    identity
                    Spacer(minLength: 4)
                    StationPriceBlock(station: station, alignment: .trailing)
                        .frame(maxWidth: 140, alignment: .trailing)
                }
            }

            StationBadges(station: station)
            StationParkingAccessFacts(station: station)
            AvailabilityLine(station: station, showFreshness: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .voltaCard(selected: selected)
        .contentShape(.rect)
    }

    private var identity: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(station.stationTitle)
                .font(.headline)
                .foregroundStyle(Theme.ink)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                .fixedSize(horizontal: false, vertical: true)
            if let place = station.placeLine {
                Text(place)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                    .fixedSize(horizontal: false, vertical: true)
                    .monospacedDigit()
            }
        }
    }
}

/// Placeholder card shown within 300 ms while the first results load.
struct StationCardSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                RoundedRectangle(cornerRadius: 6).frame(width: 180, height: 16)
                Spacer()
                RoundedRectangle(cornerRadius: 6).frame(width: 56, height: 22)
            }
            RoundedRectangle(cornerRadius: 6).frame(width: 140, height: 12)
            RoundedRectangle(cornerRadius: 6).frame(width: 220, height: 12)
        }
        .foregroundStyle(Theme.hairline.opacity(0.7))
        .padding(16)
        .voltaCard()
    }
}

/// "Charging" label, price and unit. Prices use tabular figures so lists line up.
struct StationPriceBlock: View {
    let station: Station
    var alignment: HorizontalAlignment = .trailing
    var large = false

    /// An out-of-service charger keeps its price for reference, but quietly: a big bold
    /// "Free" would make an unusable charger look like the best option on the screen.
    private var muted: Bool { station.isReportedOutOfService }

    var body: some View {
        VStack(alignment: alignment, spacing: 1) {
            Text("Charging")
                .font(.caption2.weight(.medium))
                .textCase(.uppercase)
                .foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(station.priceText)
                    .font(muted
                        ? .system(large ? .title3 : .subheadline, design: .rounded).weight(.semibold)
                        : .system(large ? .largeTitle : .title2, design: .rounded).weight(.bold))
                    .monospacedDigit()
                    .foregroundStyle(muted ? Color.secondary : Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if let unit = station.priceUnit {
                    Text(unit)
                        .font(large ? .subheadline : .caption)
                        .foregroundStyle(.secondary)
                }
            }
            if !station.tariffNotes.isEmpty {
                Text(station.tariffNotes.joined(separator: " · "))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// One wrapping row: the decision pill, the source tier in plain words, corroboration,
/// the time-of-use window and a source conflict. One decision pill per station.
struct StationBadges: View {
    let station: Station

    var body: some View {
        FlowLayout(spacing: 6, lineSpacing: 6) {
            DecisionPill(station: station)
            TierBadge(station: station)
            if station.corroborationCount >= 2 {
                MiniBadge(text: String(localized: "\(station.corroborationCount) sources"),
                          systemImage: "checkmark.shield",
                          tint: .secondary)
            }
            if let window = station.tariffWindowLabel {
                MiniBadge(text: window.text,
                          systemImage: "clock",
                          tint: window.currentlyCheap ? .secondary : Theme.check)
            }
            if station.claimConflict?.summary != nil {
                MiniBadge(text: String(localized: "Sources disagree"),
                          systemImage: "exclamationmark.triangle",
                          tint: Theme.danger)
            }
        }
    }
}

extension Station {
    /// Distance and place in one line ("1.2 km · Calle Mayor 4 · Valencia").
    var placeLine: String? {
        let parts = [distanceText, address, city].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

// MARK: - Availability (real-time port breakdown: N free · M in use · K out)

struct AvailabilityLine: View {
    let station: Station
    /// The card shows evidence age (leads) or live freshness (live rows), gated only on
    /// presence (DIR-03). The detail shows freshness in its provenance section instead.
    var showFreshness: Bool = false

    var body: some View {
        FlowLayout(spacing: 12, lineSpacing: 6) {
            availabilityFacts
            routeFacts
        }
        .font(.footnote.weight(.semibold))
        .monospacedDigit()
    }

    @ViewBuilder
    private var availabilityFacts: some View {
        if station.trustKind == .lead {
            dot(.secondary, String(localized: "Not live"))
            if let total = station.connectorSummary?.total ?? station.totalEvse {
                plug(total)
            }
        } else if station.trustKind == .stale {
            dot(Theme.danger, String(localized: "Stale status · refresh first"))
            if let total = station.connectorSummary?.total ?? station.totalEvse { plug(total) }
        } else if station.trustKind == .degraded {
            dot(Theme.check, String(localized: "Limited live coverage · check source"))
            if let total = station.connectorSummary?.total ?? station.totalEvse { plug(total) }
        } else if station.trustKind == .unavailable {
            dot(Theme.danger, String(localized: "Current status unavailable"))
            if let total = station.connectorSummary?.total ?? station.totalEvse { plug(total) }
        } else if let summary = station.connectorSummary, let total = summary.total {
            let free = summary.available ?? 0
            dot(station.isRouteReady && free > 0 ? Theme.go : .secondary, String(localized: "\(free) free"))
            if let busy = summary.busy, busy > 0 { dot(Theme.check, String(localized: "\(busy) in use")) }
            if let out = summary.outOfService, out > 0 { dot(Theme.danger, String(localized: "\(out) out")) }
            plug(total)
        } else {
            dot(.secondary, String(localized: "Live · status unknown"))
        }
    }

    @ViewBuilder
    private var routeFacts: some View {
        if let kw = station.maxPowerKw ?? station.connectorSummary?.maxPowerKw {
            Label("up to \(Int(kw)) kW", systemImage: "bolt")
                .labelStyle(.titleAndIcon)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }

        if showFreshness, let freshText = station.freshnessRowText {
            Label(freshText, systemImage: "clock")
                .labelStyle(.titleAndIcon)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private func dot(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(text).foregroundStyle(color == .secondary ? Color.secondary : color)
        }
    }

    private func plug(_ total: Int) -> some View {
        Label("^[\(total) port](inflect: true)", systemImage: "powerplug")
            .labelStyle(.titleAndIcon)
            .font(.footnote)
            .foregroundStyle(.secondary)
    }
}

// MARK: - Badges

/// The decision pill. Go now is the only solid evidence fill; every other level is a
/// tint with a full-strength label, so the eye finds the usable charger first.
struct DecisionPill: View {
    let station: Station

    var body: some View {
        let kind = station.decisionKind
        let solid = kind == .go && station.isRouteReady
        Label(station.decisionLabel, systemImage: kind.symbol)
            .labelStyle(.titleAndIcon)
            .font(.caption.weight(.bold))
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .foregroundStyle(solid ? Color.white : kind.color)
            .background(solid ? kind.color : kind.color.opacity(0.14), in: .capsule)
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
    }
}

/// Red banner directly under a charger's title when its own source reports it out of
/// service. It is the first thing the driver reads and says plainly not to go there.
struct OutOfServiceBanner: View {
    let station: Station

    private var detail: String {
        let source = (station.sourceReportedUnavailable?.source ?? "")
            .components(separatedBy: " public map").first?
            .trimmingCharacters(in: .whitespaces) ?? ""
        return source.isEmpty
            ? String(localized: "The source reports it out of service. Don't go here.")
            : String(localized: "Reported by \(source). Don't go here.")
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "nosign")
                .font(.headline)
                .foregroundStyle(Theme.danger)
            VStack(alignment: .leading, spacing: 2) {
                Text("Out of service")
                    .font(.headline)
                    .foregroundStyle(Theme.danger)
                Text(detail)
                    .font(.subheadline)
                    .foregroundStyle(Theme.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(Theme.danger.opacity(0.12), in: .rect(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("outOfServiceBanner")
    }
}

/// The source tier in plain words (Live, Official, Directory, Community).
struct TierBadge: View {
    let station: Station

    var body: some View {
        Text(station.tierLabel)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
    }
}

struct MiniBadge: View {
    let text: String
    var systemImage: String?
    var tint: Color = .secondary

    var body: some View {
        HStack(spacing: 3) {
            if let s = systemImage {
                Image(systemName: s)
                    .font(.caption2.weight(.semibold))
                    .accessibilityHidden(true)
            }
            Text(text)
        }
        .font(.caption.weight(.semibold))
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(tint.opacity(0.13), in: .rect(cornerRadius: 9, style: .continuous))
        .foregroundStyle(tint)
        // Wraps instead of truncating when a long badge meets a large text size.
        .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - Detail screen (full honesty: provenance, claims, connectors, actions)

struct StationDetailView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    private let fallbackStation: Station
    @State private var checkinBusy = false
    /// The verdict just submitted this visit (drives the thank-you state). nil = not yet checked in.
    @State private var checkinSent: String?

    init(station: Station) {
        fallbackStation = station
    }

    /// Resolves by ID through AppState on every render. A navigation push never owns a stale
    /// value snapshot: refresh/failure/removal immediately changes the open detail in place.
    private var station: Station { state.detailStation(fallback: fallbackStation) }

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(station.stationTitle)
                            .font(.title2.bold())
                            .foregroundStyle(Theme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityAddTraits(.isHeader)
                        if let place = station.placeLine {
                            Text(place)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if station.isReportedOutOfService {
                        OutOfServiceBanner(station: station)
                    }
                    StationBadges(station: station)
                    StationPriceBlock(station: station, alignment: .leading, large: true)
                    AvailabilityLine(station: station)
                    // An out-of-service report is already the strongest warning; a second,
                    // hedged "verify before routing" note would only blur it.
                    if !station.isRouteReady && !station.isReportedOutOfService {
                        Label(routingWarning, systemImage: "exclamationmark.shield")
                            .font(.footnote.weight(.medium))
                            .foregroundStyle(station.trustKind == .stale || station.trustKind == .unavailable
                                ? Theme.danger : Theme.check)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    // The next step stays inside the medium sheet: actions come before the
                    // parking facts and the decision's reasoning.
                    StationActions(station: station)
                    StationParkingAccessFacts(station: station)
                    // Backend reasons describe the decision at fetch time. Once the client
                    // demotes stale/degraded/unavailable data, repeating an old optimistic
                    // reason (for example “route-ready right now”) would contradict the
                    // current fail-closed decision and warning.
                    if station.canShowDecisionReason,
                       !station.isReportedOutOfService,
                       let reason = station.decision?.reason,
                       !reason.isEmpty {
                        Text("Why “\(station.decisionLabel)”: \(reason)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.vertical, 6)
                .listRowSeparator(.hidden)
            }

            if let connectors = station.connectors, !connectors.isEmpty {
                Section("Connectors") {
                    if !station.canShowLiveAvailability {
                        Label("Connector types are shown, but their current status is not trusted.",
                              systemImage: "exclamationmark.shield")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(Array(connectors.enumerated()), id: \.offset) { _, c in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Circle()
                                    .fill(station.canShowLiveAvailability ? connectorColor(c.status) : Color.secondary)
                                    .frame(width: 8, height: 8)
                                Text(c.inventoryLabel)
                                    .font(.subheadline)
                                Spacer()
                                if let power = c.powerText {
                                    Text(power)
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(.secondary)
                                        .monospacedDigit()
                                }
                                if station.canShowLiveAvailability, let s = c.status {
                                    Text(s.replacingOccurrences(of: "_", with: " ").lowercased())
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            // Label, quantity, power and status form one VoiceOver stop.
                            .accessibilityElement(children: .combine)
                            if let source = c.safeSourceURL {
                                Link("View connector source", destination: source)
                                    .font(.caption)
                                    .foregroundStyle(Theme.actionInk)
                                    .frame(minHeight: 44)
                            }
                        }
                    }
                }
            }

            if !station.sourcePageConnectors.isEmpty {
                Section("Source-page connectors") {
                    if let name = station.sourceDetails?.sourceName, !name.isEmpty {
                        Text(name).font(.caption).foregroundStyle(.secondary)
                    }
                    if let count = station.sourceDetails?.connectorCount, count > 0 {
                        Text("Published sockets: \(count)")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(Array(station.sourcePageConnectors.enumerated()), id: \.offset) { _, connector in
                        if let source = connector.safeSourceURL {
                            Link(destination: source) {
                                Text(connector.inventoryText)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .font(.subheadline)
                            .foregroundStyle(Theme.actionInk)
                            .frame(minHeight: 44)
                        } else {
                            Text(connector.inventoryText)
                                .font(.subheadline)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }

            if let tariff = station.tariffDetails, tariff.text?.isEmpty == false || !station.tariffNotes.isEmpty {
                Section("Charging tariff") {
                    if let text = tariff.text, !text.isEmpty {
                        Text(text).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(station.tariffNotes, id: \.self) { note in
                        Text(note).font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let source = tariff.safeSourceURL {
                        Link("View tariff source", destination: source)
                            .foregroundStyle(Theme.actionInk)
                            .frame(minHeight: 44)
                    }
                }
            }

            if let details = station.sourceDetails {
                Section("Charger information") {
                    if let description = details.description, !description.isEmpty {
                        Text(description).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                    }
                    if let hours = details.openingHours, !hours.isEmpty {
                        row(icon: "clock", title: String(localized: "Opening hours"), detail: hours)
                    }
                    if let access = details.access, !access.isEmpty {
                        row(icon: "figure.walk", title: String(localized: "Access"), detail: access)
                    }
                    if let contact = details.contact {
                        let contactText = [contact.name, contact.phone, contact.email].compactMap { $0 }
                            .filter { !$0.isEmpty }.joined(separator: " · ")
                        if !contactText.isEmpty {
                            row(icon: "phone", title: String(localized: "Source contact"), detail: contactText)
                        }
                        if let website = contact.safeWebsiteURL {
                            Link("Contact website", destination: website)
                                .foregroundStyle(Theme.actionInk)
                                .frame(minHeight: 44)
                        }
                    }
                    ForEach(Array((details.notes ?? []).enumerated()), id: \.offset) { _, note in
                        Text(note).font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let source = details.safeSourceURL {
                        Link("View information source", destination: source)
                            .foregroundStyle(Theme.actionInk)
                            .frame(minHeight: 44)
                    }
                }
            }

            Section("How to charge") {
                let guidance = station.accessGuidance
                row(icon: "bolt.car", title: guidance.title, detail: guidance.detail)
                Text("Volta finds and explains chargers. Starting or paying for a session still happens in the operator’s app, card, or onsite terminal when those exist.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Section("Trust & provenance") {
                if let verification = station.verificationPresentation {
                    row(icon: "checkmark.seal", title: verification.title, detail: verification.detail)
                }
                if station.corroborationCount >= 2 {
                    row(icon: "checkmark.shield",
                        title: String(localized: "\(station.corroborationCount) independent sources"),
                        detail: String(localized: "Agree on price & location\(familiesSuffix). Raises confidence it exists — it doesn't make it live."))
                }
                if let p = station.parking {
                    row(icon: "parkingsign", title: p.label ?? String(localized: "Parking"), detail: p.note)
                }
                if let a = station.access, a.level == "restricted" || a.level == "customer" {
                    row(icon: "lock", title: a.label ?? String(localized: "Access"), detail: a.note)
                }
                if let claim = station.accessClaim {
                    row(icon: claim.privateOrRestricted == true ? "lock.shield" : "figure.walk",
                        title: claim.label ?? String(localized: "Access claim"),
                        detail: accessClaimDetail(claim),
                        tint: claim.privateOrRestricted == true || claim.parkingPaid == true ? Theme.check : .secondary)
                }
                if let conflict = station.claimConflict?.summary {
                    row(icon: "exclamationmark.triangle", title: String(localized: "Sources disagree"), detail: conflict, tint: Theme.danger)
                }
                // Charging-PRICE provenance and parking-COST provenance are tracked
                // SEPARATELY (AGENTS.md Prime Directive #1) -- two distinct blocks, each with
                // its own source, never merged into the conflict row above.
                if let claim = station.chargeClaim {
                    claimRow(icon: "bolt.fill", label: String(localized: "Charging claim"), claim: claim)
                }
                if let claim = station.parkingClaim {
                    claimRow(icon: "parkingsign", label: String(localized: "Parking claim"), claim: claim)
                }
                if let fresh = station.freshnessText {
                    row(icon: "clock", title: String(localized: "Last seen \(fresh)"), detail: nil)
                }
            }

            Section("Did charging work here?") {
                if let community = station.community, community.total > 0 {
                    Label {
                        Text(communitySummaryText(community))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    } icon: {
                        Image(systemName: community.recentFailure == true ? "exclamationmark.bubble.fill" : "person.2.wave.2.fill")
                            .foregroundStyle(community.recentFailure == true ? Theme.check : Theme.actionInk)
                    }
                    .accessibilityElement(children: .combine)
                }

                if let sent = checkinSent {
                    Label(checkinThanks(for: sent), systemImage: "checkmark.circle.fill")
                        .font(.subheadline)
                        .foregroundStyle(Theme.actionInk)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("Help other drivers — tell us if you could charge. Community reports never mark a charger “live”.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    checkinControls
                }
            }

            if let photo = station.firstPublicPhoto {
                Section("Source photo") {
                    PublicPhotoView(photo: photo)
                        .listRowInsets(EdgeInsets())
                }
            }

            if let photos = station.media?.photos, photos.contains(where: { $0.safeSourceURL != nil }) {
                Section("Source photos") {
                    ForEach(Array(photos.enumerated()), id: \.offset) { _, photo in
                        if let source = photo.safeSourceURL {
                            VStack(alignment: .leading, spacing: 3) {
                                if let caption = photo.caption, !caption.isEmpty {
                                    Text(caption).font(.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                Link("View source photo", destination: source)
                                    .foregroundStyle(Theme.actionInk)
                                    .frame(minHeight: 44)
                            }
                        }
                    }
                }
            }

            if let sources = station.sources, !sources.isEmpty {
                Section("Public sources (\(sources.count))") {
                    ForEach(Array(sources.enumerated()), id: \.offset) { _, src in
                        VStack(alignment: .leading, spacing: 3) {
                            if let url = src.safeURL {
                                Link(destination: url) {
                                    Text(src.name ?? "Source")
                                        .font(.subheadline.weight(.medium))
                                        .underline()
                                        .foregroundStyle(Theme.actionInk)
                                }
                            } else {
                                Text(src.name ?? "Source")
                                    .font(.subheadline.weight(.medium))
                            }
                            if let e = src.evidence, !e.isEmpty {
                                Text(e).font(.caption2).foregroundStyle(.secondary).lineLimit(3)
                            }
                            if let attribution = src.attribution, !attribution.isEmpty {
                                Text("Attribution: \(attribution)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            if let license = src.license, !license.isEmpty {
                                if let licenseURL = src.safeLicenseURL {
                                    Link("License: \(license)", destination: licenseURL)
                                        .font(.caption2)
                                        .foregroundStyle(Theme.actionInk)
                                } else {
                                    Text("License: \(license)")
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            if let transformation = src.transformation, !transformation.isEmpty {
                                Text("Transformation: \(transformation)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            if let updated = src.sourceLastUpdated, !updated.isEmpty {
                                Text("Source updated: \(updated)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            } else if let fetched = src.sourceFetchedAt, !fetched.isEmpty {
                                Text("Retrieved: \(fetched)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .listSectionSpacing(.compact)
        .contentMargins(.top, 4, for: .scrollContent)
        .navigationTitle(String(localized: "Charger details"))
        .navigationBarTitleDisplayMode(.inline)
        .scrollContentBackground(.hidden)
        .background(Theme.canvas)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    state.toggleFavorite(station)
                } label: {
                    Image(systemName: state.isFavorite(station) ? "star.fill" : "star")
                        .frame(minWidth: 44, minHeight: 44)
                }
                .tint(Theme.actionInk)
                .accessibilityLabel(state.isFavorite(station) ? String(localized: "Saved on this iPhone") : String(localized: "Save charger"))
                .accessibilityHint("Saves this charger on this iPhone only. It does not create an account.")
                .accessibilityIdentifier("saveChargerButton")
            }
        }
    }

    private var routingWarning: String {
        switch station.trustKind {
        case .stale: String(localized: "This status is stale. Refresh and verify the public source before routing.")
        case .degraded: String(localized: "Live coverage is limited. Verify the public source before routing.")
        case .unavailable: String(localized: "Current status is unavailable. Verify the public source before routing.")
        case .lead: String(localized: "This is a known charger lead, not live status. Verify the public source before routing.")
        case .fresh: String(localized: "This charger is not route-ready. Check the public source before routing.")
        }
    }

    private var familiesSuffix: String {
        guard let fams = station.corroboration?.families, !fams.isEmpty else { return "" }
        return " (\(fams.joined(separator: ", ")))"
    }

    private func connectorColor(_ status: String?) -> Color {
        let s = (status ?? "").uppercased()
        if s.contains("AVAIL") { return Theme.go }
        if s.contains("CHARG") || s.contains("OCCUP") || s.contains("BUSY") { return Theme.check }
        if s.contains("OUT") || s.contains("FAULT") || s.contains("UNAVAIL") { return Theme.danger }
        return .secondary
    }

    private func accessClaimDetail(_ claim: AccessClaim) -> String {
        var parts: [String] = []
        if let kind = claim.parkingKind, !kind.isEmpty {
            parts.append(kind.replacingOccurrences(of: "_", with: " "))
        }
        if claim.parkingPaid == true {
            parts.append(String(localized: "paid/conditional parking"))
        }
        if claim.privateOrRestricted == true {
            parts.append(String(localized: "private/restricted"))
        }
        if let source = claim.source, !source.isEmpty {
            parts.append(String(localized: "source: \(source)"))
        }
        if let summary = claim.summary, !summary.isEmpty {
            parts.append(summary)
        }
        return parts.joined(separator: " · ")
    }

    /// A charging-claim or parking-claim block: label, summary, and its OWN source (with the
    /// source URL as a Link when present) -- mirrors SourceProvenance.tsx's "Charging claim" /
    /// "Parking claim" blocks on the web. VoiceOver reads the whole claim as one element; the
    /// source link stays independently reachable in "Public sources" below.
    private func claimRow(icon: String, label: String, claim: Claim) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(label).font(.subheadline.weight(.medium))
                if let summary = claim.summary, !summary.isEmpty {
                    Text(summary).font(.caption).foregroundStyle(.secondary)
                }
                HStack(spacing: 4) {
                    Text("via \(claim.sourceLabel)")
                    Text("·")
                    if let url = claim.sourceURL {
                        Link("view source", destination: url)
                            .foregroundStyle(Theme.actionInk)
                    } else {
                        Text("view source").foregroundStyle(.tertiary)
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    private func row(icon: String, title: String, detail: String?, tint: Color = .secondary) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.subheadline)
                .foregroundStyle(tint)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.medium))
                if let d = detail, !d.isEmpty {
                    Text(d).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 2)
        // One VoiceOver stop (icon + title + detail), like claimRow above.
        .accessibilityElement(children: .combine)
    }

    // MARK: - Community check-in ("did charging actually work?")

    private var checkinControls: some View {
        // Three peer actions share one row and one width; at large text sizes they wrap.
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { checkinButtons }
            VStack(spacing: 8) { checkinButtons }
        }
    }

    @ViewBuilder
    private var checkinButtons: some View {
        checkinButton(title: String(localized: "I charged"), systemImage: "checkmark.circle", verdict: "charged")
        checkinButton(title: String(localized: "Couldn't charge"), systemImage: "xmark.circle", verdict: "failed")
        checkinButton(title: String(localized: "Occupied"), systemImage: "person.fill", verdict: "occupied")
    }

    private func checkinButton(title: String, systemImage: String, verdict: String) -> some View {
        Button {
            guard !checkinBusy else { return }
            checkinBusy = true
            Task {
                let ok = await state.submitCheckin(station: station, verdict: verdict)
                checkinBusy = false
                if ok { withAnimation(VoltaMotion.state) { checkinSent = verdict } }
            }
        } label: {
            Label(title, systemImage: systemImage)
                .font(.footnote.weight(.semibold))
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                .frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.bordered)
        .tint(Theme.ink)
        .disabled(checkinBusy)
        .accessibilityHint("Records a driver report for this charger")
    }

    private func communitySummaryText(_ c: CommunitySummary) -> String {
        var parts: [String] = []
        if c.chargedCount > 0 { parts.append(String(localized: "\(c.chargedCount) charged")) }
        if c.failedCount > 0 { parts.append(String(localized: "\(c.failedCount) couldn't")) }
        if c.occupiedCount > 0 { parts.append(String(localized: "\(c.occupiedCount) found it busy")) }
        let head = parts.isEmpty ? String(localized: "Driver reports logged") : parts.joined(separator: " · ")
        if c.recentFailure == true {
            return head + " · " + String(localized: "recent attempts failed — verify before routing")
        }
        return head
    }

    private func checkinThanks(for verdict: String) -> String {
        switch verdict {
        case "charged": return String(localized: "Thanks! Logged that charging worked here.")
        case "failed": return String(localized: "Thanks — logged that charging didn’t work.")
        default: return String(localized: "Thanks — logged that it was occupied.")
        }
    }
}

struct PublicPhotoView: View {
    let photo: StationPhoto

    var body: some View {
        if let url = photo.safeURL {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .scaledToFill()
                case .failure:
                    Color.clear
                case .empty:
                    ZStack {
                        Theme.surface
                        ProgressView()
                            .tint(Theme.action)
                    }
                @unknown default:
                    Color.clear
                }
            }
            .frame(height: 160)
            .frame(maxWidth: .infinity)
            .clipped()
            .overlay(alignment: .bottomLeading) {
                if let source = photo.source {
                    Text(source)
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .foregroundStyle(Theme.darkInk)
                        .background(Theme.darkSurface, in: .rect(cornerRadius: 8, style: .continuous))
                        .padding(8)
                }
            }
            .accessibilityElement()
            .accessibilityLabel(photo.caption ?? String(localized: "Public source charger photo"))
            .accessibilityHint("Public source image may be stale; onsite view wins.")
        }
    }
}
