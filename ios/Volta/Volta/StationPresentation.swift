import Foundation
import SwiftUI

struct StationCompactFact: Equatable {
    let text: String
    let isCaution: Bool
}

extension Station {
    /// Parking has its own evidence. In particular, `paid: false` is also emitted for
    /// unknown, street and customer parking; neither it nor a free charging tariff proves
    /// free parking. The full parking claim and its source remain in station details.
    var parkingSummary: StationCompactFact {
        let kind = parking?.kind ?? parkingClaim?.kind ?? "unknown"
        if parking?.paid == true || accessClaim?.parkingPaid == true
            || kind == "paid_or_metered" || parkingClaim?.kind == "paid_or_metered" {
            return StationCompactFact(text: String(localized: "Paid parking"), isCaution: true)
        }
        switch kind {
        case "free_parking":
            return StationCompactFact(text: String(localized: "Parking reported free"), isCaution: false)
        case "time_limited_or_validated":
            return StationCompactFact(text: String(localized: "Parking conditions apply"), isCaution: true)
        case "retail_parking", "facility_or_customer_parking":
            return StationCompactFact(text: String(localized: "Customer parking · check terms"), isCaution: true)
        case "street_or_municipal":
            return StationCompactFact(text: String(localized: "Street parking · check signs"), isCaution: false)
        default:
            return StationCompactFact(text: String(localized: "Parking cost unknown"), isCaution: false)
        }
    }

    /// A restriction from either access surface stays visible even if the other says
    /// public. This summary cannot grant access or upgrade live/route authority.
    var accessRestrictionSummary: String? {
        if accessClaim?.privateOrRestricted == true {
            return String(localized: "Private or restricted")
        }
        let levels = [access?.level, accessClaim?.level].compactMap { $0?.lowercased() }
        if levels.contains("private") || levels.contains("restricted") {
            return String(localized: "Restricted access")
        }
        if levels.contains("customer") {
            return String(localized: "Customer access")
        }
        return nil
    }
}

/// Compact route facts shared by a result, selected map pin and detail header. These
/// summarize cost and restrictions; each claim's attribution stays separately available
/// in the detail provenance section.
struct StationParkingAccessFacts: View {
    let station: Station

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            let parking = station.parkingSummary
            Label(parking.text, systemImage: "parkingsign")
                .foregroundStyle(parking.isCaution ? Theme.check : .secondary)
                .accessibilityElement(children: .combine)
            if let restriction = station.accessRestrictionSummary {
                Label(restriction, systemImage: "lock")
                    .foregroundStyle(Theme.check)
                    .accessibilityElement(children: .combine)
            }
        }
        .font(.caption.weight(.medium))
        .fixedSize(horizontal: false, vertical: true)
    }
}
