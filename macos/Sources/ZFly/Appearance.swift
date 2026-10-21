import AppKit
import SwiftUI

enum ZFlyMetrics {
    static let panelRadius: CGFloat = 14
    static let controlRadius: CGFloat = 8
    static let editorRadius: CGFloat = 10
    static let pagePadding: CGFloat = 24
    static let panelPadding: CGFloat = 24
    static let sectionSpacing: CGFloat = 20
    static let rowHeight: CGFloat = 44
    static let fieldWidth: CGFloat = 240
    static let controlHeight: CGFloat = 36
}

enum ZFlyPalette {
    static let tint = Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor(srgbRed: 0.34, green: 0.73, blue: 0.74, alpha: 1)
            : NSColor(srgbRed: 0.12, green: 0.48, blue: 0.51, alpha: 1)
    })
    static let upload = Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor(srgbRed: 0.69, green: 0.65, blue: 0.91, alpha: 1)
            : NSColor(srgbRed: 0.49, green: 0.44, blue: 0.76, alpha: 1)
    })
    static let action = Color(red: 0.12, green: 0.46, blue: 0.49)
}

struct WindowBackdrop: View {
    @AppStorage("appearance.ambient", store: PreviewOptions.preferences) private var ambient = true
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        (colorScheme == .dark ? Color(red: 0.09, green: 0.10, blue: 0.11) : Color(nsColor: .windowBackgroundColor))
            .overlay(alignment: .topTrailing) {
                if ambient && !reduceTransparency {
                    RadialGradient(colors: [ZFlyPalette.tint.opacity(colorScheme == .dark ? 0.08 : 0.035), .clear],
                                   center: .topTrailing, startRadius: 0, endRadius: 600)
                }
            }
            .ignoresSafeArea().allowsHitTesting(false)
    }
}

struct SidebarSurface: ViewModifier {
    @AppStorage("appearance.glass", store: PreviewOptions.preferences) private var glass = true
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    func body(content: Content) -> some View {
        content.background {
            Group {
                if glass && !reduceTransparency {
                    Rectangle().fill(.regularMaterial)
                } else {
                    Color(nsColor: .controlBackgroundColor)
                }
            }.ignoresSafeArea()
        }
    }
}

/// Text and charts use stable surfaces, independently of the sidebar material.
struct SurfacePanel: ViewModifier {
    var radius: CGFloat = ZFlyMetrics.panelRadius
    var selected = false
    @Environment(\.colorScheme) private var colorScheme

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        content
            .background(colorScheme == .dark ? Color(red: 0.13, green: 0.14, blue: 0.15) : Color(nsColor: .controlBackgroundColor), in: shape)
            .overlay {
                shape.strokeBorder(selected ? ZFlyPalette.tint.opacity(0.5) : Color.primary.opacity(colorScheme == .dark ? 0.10 : 0.065), lineWidth: 1)
                    .allowsHitTesting(false)
            }
    }
}

struct PageHeader<Actions: View>: View {
    let title: LocalizedStringKey
    let actions: Actions

    init(_ title: LocalizedStringKey, @ViewBuilder actions: () -> Actions = { EmptyView() }) {
        self.title = title
        self.actions = actions()
    }

    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            Text(title).font(.system(size: 25, weight: .bold)).accessibilityAddTraits(.isHeader)
            Spacer(minLength: 0)
            actions
        }
        .frame(maxWidth: .infinity, minHeight: ZFlyMetrics.controlHeight, alignment: .leading)
    }
}

struct SearchField: View {
    let prompt: LocalizedStringKey
    @Binding var text: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField(prompt, text: $text).textFieldStyle(.plain)
            if !text.isEmpty {
                Button("清除搜索", systemImage: "xmark.circle.fill") { text = "" }
                    .labelStyle(.iconOnly).buttonStyle(.plain).foregroundStyle(.secondary)
            }
        }
        .font(.callout).padding(.horizontal, 11).frame(height: ZFlyMetrics.controlHeight)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: ZFlyMetrics.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: ZFlyMetrics.controlRadius).strokeBorder(.primary.opacity(0.07)))
    }
}

struct StatusBadge: View {
    let title: LocalizedStringKey
    var color: Color = .secondary

    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 5, height: 5)
            Text(title).font(.system(size: 11, weight: .medium))
        }
        .foregroundStyle(color).padding(.horizontal, 9).padding(.vertical, 5)
        .background(color.opacity(0.09), in: Capsule())
    }
}

struct SectionTab<Value: Hashable>: Identifiable {
    let value: Value
    let title: LocalizedStringKey
    let icon: String
    var id: Value { value }
}

/// Tabs share the width of the longest label and fit within the available space.
private struct EqualWidthTabs: Layout {
    var spacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        let width = (sizes.map(\.width).max() ?? 0) * CGFloat(sizes.count) + spacing * CGFloat(max(sizes.count - 1, 0))
        return CGSize(width: min(width, proposal.width ?? width), height: sizes.map(\.height).max() ?? 0)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard !subviews.isEmpty else { return }
        let width = max(0, (bounds.width - spacing * CGFloat(subviews.count - 1)) / CGFloat(subviews.count))
        for (index, subview) in subviews.enumerated() {
            subview.place(at: CGPoint(x: bounds.minX + CGFloat(index) * (width + spacing), y: bounds.minY),
                          anchor: .topLeading, proposal: ProposedViewSize(width: width, height: bounds.height))
        }
    }
}

struct SectionTabs<Value: Hashable>: View {
    let title: LocalizedStringKey
    @Binding var selection: Value
    let items: [SectionTab<Value>]
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        EqualWidthTabs {
            ForEach(items) { item in
                Button { selection = item.value } label: {
                    Label(item.title, systemImage: item.icon)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(selection == item.value ? ZFlyPalette.tint : Color.secondary)
                        .lineLimit(1).minimumScaleFactor(0.85)
                        .padding(.horizontal, 13).frame(maxWidth: .infinity).frame(height: 36)
                        .background {
                            if selection == item.value {
                                RoundedRectangle(cornerRadius: 8, style: .continuous)
                                    .fill(Color(nsColor: .controlBackgroundColor))
                                    .shadow(color: .black.opacity(0.06), radius: 2, y: 1)
                            }
                        }
                        .contentShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(item.title)
                .accessibilityAddTraits(selection == item.value ? .isSelected : [])
            }
        }
        .padding(4).fixedSize(horizontal: false, vertical: true)
        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.primary.opacity(0.05)))
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.15), value: selection)
        .accessibilityElement(children: .contain).accessibilityLabel(title)
    }
}

struct ContentSection<Content: View>: View {
    let title: LocalizedStringKey
    let content: Content

    init(_ title: LocalizedStringKey, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.system(size: 12, weight: .semibold)).accessibilityAddTraits(.isHeader)
            content
        }
        .padding(ZFlyMetrics.panelPadding).frame(maxWidth: .infinity, alignment: .leading)
        .modifier(SurfacePanel())
    }
}

/// Each settings row uses the same label column, control edge and baseline.
struct PreferenceRow<Content: View>: View {
    let title: LocalizedStringKey
    let content: Content

    init(_ title: LocalizedStringKey, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        HStack(spacing: 20) {
            Text(title).font(.callout).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            content.labelsHidden()
        }
        .frame(minHeight: ZFlyMetrics.rowHeight)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(title)
    }
}

struct ToolAction: View {
    let title: LocalizedStringKey
    var role: ButtonRole? = nil
    let action: () -> Void

    init(_ title: LocalizedStringKey, role: ButtonRole? = nil, action: @escaping () -> Void) {
        self.title = title
        self.role = role
        self.action = action
    }

    var body: some View {
        Button(role: role, action: action) {
            Text(title).frame(maxWidth: .infinity).frame(minHeight: 12)
        }
    }
}

struct ZFlyButtonStyle: ButtonStyle {
    var prominent = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(prominent ? Color.white : (configuration.role == .destructive ? Color.red : Color.primary))
            .padding(.horizontal, 12).frame(minHeight: ZFlyMetrics.controlHeight)
            .background {
                RoundedRectangle(cornerRadius: ZFlyMetrics.controlRadius, style: .continuous)
                    .fill(prominent ? ZFlyPalette.action.opacity(configuration.isPressed ? 0.8 : 1) : Color.primary.opacity(configuration.isPressed ? 0.09 : 0.045))
                    .overlay {
                        RoundedRectangle(cornerRadius: ZFlyMetrics.controlRadius, style: .continuous)
                            .strokeBorder(prominent ? Color.white.opacity(0.1) : Color.primary.opacity(0.07), lineWidth: 1)
                    }
            }
            .opacity(isEnabled ? 1 : 0.4)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

struct ActionStyle: ViewModifier {
    var prominent = false
    func body(content: Content) -> some View {
        content.buttonStyle(ZFlyButtonStyle(prominent: prominent))
    }
}
