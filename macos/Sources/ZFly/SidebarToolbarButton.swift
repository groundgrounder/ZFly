import AppKit
import SwiftUI

/// Set the native focus ring explicitly; the SwiftUI toolbar can otherwise
/// retain its outline even when focusEffectDisabled is applied to a Button.
struct SidebarToolbarButton: NSViewRepresentable {
    var action: () -> Void
    @Environment(\.locale) private var locale
    @Environment(\.isEnabled) private var isEnabled

    func makeCoordinator() -> Coordinator { Coordinator(action: action) }

    func makeNSView(context: Context) -> NSButton {
        let button = NSButton(frame: NSRect(x: 0, y: 0, width: 32, height: 28))
        button.setButtonType(.momentaryPushIn)
        button.bezelStyle = .texturedRounded
        button.showsBorderOnlyWhileMouseInside = true
        button.imagePosition = .imageOnly
        button.imageScaling = .scaleProportionallyDown
        button.focusRingType = .none
        button.cell?.focusRingType = .none
        button.target = context.coordinator
        button.action = #selector(Coordinator.performAction)
        button.setAccessibilityIdentifier("sidebar.toggle")
        return button
    }

    func updateNSView(_ button: NSButton, context: Context) {
        context.coordinator.action = action
        let localization = Bundle.preferredLocalizations(from: Bundle.module.localizations,
                                                       forPreferences: [locale.identifier]).first
        let localizedBundle = localization.flatMap { Bundle.module.url(forResource: $0, withExtension: "lproj") }
            .flatMap(Bundle.init(url:)) ?? .module
        let title = localizedBundle.localizedString(forKey: "侧边栏", value: nil, table: nil)
        button.image = NSImage(systemSymbolName: "sidebar.left", accessibilityDescription: title)?
            .withSymbolConfiguration(.init(pointSize: 16, weight: .regular))
        button.setAccessibilityLabel(title)
        button.toolTip = localizedBundle.localizedString(forKey: "显示或隐藏侧边栏", value: nil, table: nil)
        button.isEnabled = isEnabled
    }

    @MainActor
    final class Coordinator: NSObject {
        var action: () -> Void

        init(action: @escaping () -> Void) { self.action = action }

        @objc func performAction() { action() }
    }
}
