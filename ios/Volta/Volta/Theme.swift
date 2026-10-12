import SwiftUI

/// Volta design tokens, ported from the web app (frontend/src/index.css).
/// Direction D, "Green means go" (docs/design-system.md): actions, selection and tint use
/// neutral ink; green is reserved for the Go-now evidence state.
enum Theme {
    /// Asset-catalog tint (AccentColor): ink in light mode, a white-label-safe grey in dark.
    static let brand = Color("AccentColor")
    /// Filled action. White text is 18.5:1 on the light ink and 5.2:1 on the dark grey,
    /// whose edge is 3.7:1 against the dark canvas. Never green: that would read as Go now.
    static let action = Color(light: .init(hex: 0x0E1512), dark: .init(hex: 0x646F6A))
    /// Text/icons that carry an action; keep the filled action paired with white.
    static let actionInk = Color(light: .init(hex: 0x0E1512), dark: .init(hex: 0xEDF1EE))

    static let go = Color(light: .init(hex: 0x0D7D40), dark: .init(hex: 0x21D277))
    static let check = Color(light: .init(hex: 0x986200), dark: .init(hex: 0xF0B429))
    static let verify = Color(light: .init(hex: 0x426AC3), dark: .init(hex: 0x7AA2F7))
    static let danger = Color(light: .init(hex: 0xD83A40), dark: .init(hex: 0xFF6B70))

    // Solid neutral surfaces keep map labels legible over every Apple Maps palette and let
    // the Go-now green stand out as the only saturated signal on screen.
    static let canvas = Color(light: Color(hex: 0xF5F6F4), dark: Color(hex: 0x0B0F0D))
    static let surface = Color(light: Color(hex: 0xFFFFFF), dark: Color(hex: 0x141A17))
    static let raisedSurface = Color(light: Color(hex: 0xFFFFFF), dark: Color(hex: 0x1B221F))
    static let ink = Color(light: Color(hex: 0x0E1512), dark: Color(hex: 0xEDF1EE))
    static let inverseInk = Color(light: Color(hex: 0xF5F6F4), dark: Color(hex: 0x0E1512))
    static let hairline = Color(light: Color(hex: 0xD3D8D4), dark: Color(hex: 0x323A36))

    // Use these together for evergreen map anchors in either appearance. An adaptive
    // inverse foreground on a permanent dark surface disappears in Dark Mode.
    static let darkSurface = Color(hex: 0x0E1512)
    static let darkInk = Color(hex: 0xF5F6F4)
    static let darkSecondaryInk = Color(hex: 0xB5BEB9)
}

/// One motion vocabulary for the map, filter instrument and route ticket. Frequent UI
/// feedback stays below 300 ms; the views themselves remove spatial movement when Reduce
/// Motion is enabled while retaining opacity/color feedback.
enum VoltaMotion {
    static let press = Animation.easeOut(duration: 0.12)
    static let state = Animation.easeInOut(duration: 0.20)
    static let spatial = Animation.spring(duration: 0.28, bounce: 0)
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }

    /// Dynamic light/dark color.
    init(light: Color, dark: Color) {
        self.init(uiColor: UIColor { trait in
            trait.userInterfaceStyle == .dark ? UIColor(dark) : UIColor(light)
        })
    }
}

/// Quiet, immediate press feedback for controls that use a plain visual style.
/// The opacity change preserves feedback when Reduce Motion removes the scale.
struct VoltaPressButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var pressedScale: CGFloat = 0.97

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed && !reduceMotion ? pressedScale : 1)
            .opacity(configuration.isPressed ? 0.78 : 1)
            .animation(reduceMotion ? nil : VoltaMotion.press, value: configuration.isPressed)
    }
}

// MARK: - Materials

/// Liquid Glass for small floating map controls only (docs/design-system.md section 6).
/// Content never sits on glass. Reduce Transparency, Increase Contrast and iOS versions
/// before 26 get an opaque surface with a visible hairline instead.
struct VoltaGlassSurface<S: InsettableShape>: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    let shape: S
    var interactive = false

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 26, *), !reduceTransparency, contrast != .increased {
            content
                .foregroundStyle(Theme.ink)
                .glassEffect(.regular.interactive(interactive), in: shape)
        } else {
            content
                .foregroundStyle(Theme.ink)
                .background(Theme.surface, in: shape)
                .overlay { shape.strokeBorder(Theme.hairline, lineWidth: 1) }
                .shadow(color: .black.opacity(0.12), radius: 8, y: 3)
        }
    }
}

extension View {
    func voltaGlass<S: InsettableShape>(in shape: S, interactive: Bool = false) -> some View {
        modifier(VoltaGlassSurface(shape: shape, interactive: interactive))
    }

    /// Groups neighbouring glass controls so they blend and morph as one on iOS 26+.
    @ViewBuilder
    func voltaGlassGroup(spacing: CGFloat = 8) -> some View {
        if #available(iOS 26, *) {
            GlassEffectContainer(spacing: spacing) { self }
        } else {
            self
        }
    }

    /// Opaque content card: surface fill, hairline border, no shadow (borders before shadows).
    func voltaCard(selected: Bool = false, radius: CGFloat = 18) -> some View {
        self
            .background(
                selected ? Theme.selectedSurface : Theme.surface,
                in: .rect(cornerRadius: radius, style: .continuous)
            )
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(selected ? Theme.actionInk : Theme.hairline, lineWidth: selected ? 1.5 : 1)
            }
    }
}

extension Theme {
    /// A selected card keeps its opaque surface with a faint ink wash; the ink border is
    /// the main cue, and VoiceOver hears `.isSelected`.
    /// Border of a secondary button: at least 3:1 against the surface it sits on.
    static let hairlineStrong = Color(light: Color(hex: 0x8B948F), dark: Color(hex: 0x6F7A74))
    static let selectedSurface = Color(light: Color(hex: 0xEEF0EE), dark: Color(hex: 0x1B221F))
}

// MARK: - Flow layout

/// Wraps badges and facts onto as many lines as the width and text size need, so one
/// layout serves an iPhone SE at default size and a Pro Max at the largest accessibility size.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let width = rows.map(\.width).max() ?? 0
        let height = rows.map(\.height).reduce(0, +) + lineSpacing * CGFloat(max(rows.count - 1, 0))
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for item in row.items {
                let size = item.size
                // A subview that fits keeps its ideal size (no proposal), so its text can
                // still grow; only an over-wide one is held to the row width.
                subviews[item.index].place(
                    at: CGPoint(x: x, y: y + (row.height - size.height) / 2),
                    proposal: item.constrained ? ProposedViewSize(width: bounds.width, height: nil) : .unspecified
                )
                x += size.width + spacing
            }
            y += row.height + lineSpacing
        }
    }

    private struct Row {
        var items: [(index: Int, size: CGSize, constrained: Bool)] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = [Row()]
        for index in subviews.indices {
            var size = subviews[index].sizeThatFits(.unspecified)
            let constrained = size.width > width
            if constrained {
                size = subviews[index].sizeThatFits(ProposedViewSize(width: width, height: nil))
            }
            let needed = rows[rows.count - 1].items.isEmpty ? size.width : rows[rows.count - 1].width + spacing + size.width
            if needed > width, !rows[rows.count - 1].items.isEmpty {
                rows.append(Row())
            }
            var row = rows[rows.count - 1]
            row.width = row.items.isEmpty ? size.width : row.width + spacing + size.width
            row.height = max(row.height, size.height)
            row.items.append((index, size, constrained))
            rows[rows.count - 1] = row
        }
        return rows.filter { !$0.items.isEmpty }
    }
}
