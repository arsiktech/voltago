import SwiftUI

struct MomentMark: View {
    enum Tone { case quiet, offline, search }
    var tone: Tone = .quiet

    private var accent: Color {
        switch tone {
        case .quiet: Theme.brand
        case .offline: Theme.danger
        case .search: Theme.verify
        }
    }

    var body: some View {
        ZStack {
            Path { path in
                path.move(to: .init(x: 18, y: 54))
                path.addCurve(to: .init(x: 72, y: 46),
                              control1: .init(x: 38, y: 30),
                              control2: .init(x: 52, y: 65))
                path.addCurve(to: .init(x: 136, y: 32),
                              control1: .init(x: 96, y: 22),
                              control2: .init(x: 112, y: 30))
            }
            .stroke(.secondary.opacity(0.16), style: .init(lineWidth: 6, lineCap: .round))

            Path { path in
                path.move(to: .init(x: 18, y: 54))
                path.addCurve(to: .init(x: 72, y: 46),
                              control1: .init(x: 38, y: 30),
                              control2: .init(x: 52, y: 65))
                path.addCurve(to: .init(x: 136, y: 32),
                              control1: .init(x: 96, y: 22),
                              control2: .init(x: 112, y: 30))
            }
            .trim(from: 0, to: 0.82)
            .stroke(accent, style: .init(lineWidth: 3, lineCap: .round, dash: [8, 10]))

            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Theme.surface)
                .frame(width: 70, height: 56)
                .overlay {
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .strokeBorder(Theme.hairline, lineWidth: 1)
                }
                .shadow(color: .black.opacity(0.10), radius: 10, y: 6)

            symbol
                .font(.system(size: 24, weight: .bold, design: .rounded))
                .foregroundStyle(accent)

            Circle()
                .stroke(accent.opacity(0.28), lineWidth: 1.5)
                .frame(width: 32, height: 32)
                .offset(y: 37)
        }
        .frame(width: 156, height: 92)
        .accessibilityHidden(true)
    }

    @ViewBuilder
    private var symbol: some View {
        switch tone {
        case .quiet:
            Image(systemName: "bolt.fill")
        case .offline:
            Image(systemName: "xmark")
        case .search:
            Image(systemName: "magnifyingglass")
        }
    }
}

/// The quiet route-ready confirmation: a single opaque notice with a success haptic, only
/// when a real live Go-now charger appears. No particles or confetti (design-system 1.5).
struct RouteReadyMomentView: View {
    @EnvironmentObject private var state: AppState
    let moment: RouteMoment
    /// `.sensoryFeedback` never plays for a view's first value, so `appeared` flips false to
    /// true on appear to give each freshly mounted confirmation one real change to observe.
    @State private var appeared = false

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(.title2.weight(.semibold))
                .foregroundStyle(Theme.go)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 2) {
                Text("Route-ready charger found")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text(moment.stationName)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Button {
                state.openRouteMomentCard()
            } label: {
                Text("View")
                    .font(.subheadline.weight(.semibold))
                    .frame(minWidth: 44, minHeight: 44)
            }
            .buttonStyle(VoltaActionButtonStyle(prominent: true))

            Button {
                state.dismissRouteMoment()
            } label: {
                Image(systemName: "xmark")
                    .font(.footnote.weight(.bold))
                    .foregroundStyle(.secondary)
                    .frame(width: 44, height: 44)
                    .contentShape(.circle)
            }
            .buttonStyle(VoltaPressButtonStyle())
            .accessibilityLabel("Dismiss route-ready notice")
        }
        .padding(.leading, 14)
        .padding(.trailing, 6)
        .padding(.vertical, 6)
        .background(Theme.surface, in: .rect(cornerRadius: 18, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(Theme.go.opacity(0.65), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.14), radius: 12, y: 6)
        .accessibilityElement(children: .contain)
        .onAppear { appeared = true }
        .sensoryFeedback(.success, trigger: appeared)
    }
}
