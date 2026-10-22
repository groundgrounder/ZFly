import AppKit
import Observation
import SwiftUI

/// AppKit owns the popover's material, rounded frame and dismissal behavior.
@MainActor
final class StatusMenuController: NSObject {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let popover = NSPopover()
    private let initialFocus = StatusMenuInitialFocusView(frame: .zero)
    private let store: Store

    init(store: Store, openMainWindow: @escaping () -> Void) {
        self.store = store
        super.init()
        popover.behavior = .transient
        popover.contentViewController = NSHostingController(rootView: StatusMenuRoot(
            store: store,
            openMainWindow: { [weak self] in
                self?.popover.performClose(nil)
                openMainWindow()
            },
            applyAppearance: { [weak self] in self?.applyAppearance($0) }
        ))
        if let view = popover.contentViewController?.view {
            initialFocus.setAccessibilityElement(false)
            initialFocus.focusRingType = .none
            view.addSubview(initialFocus)
            initialFocus.nextKeyView = view
        }
        if let button = statusItem.button {
            button.target = self
            button.action = #selector(togglePopover(_:))
            button.setAccessibilityLabel("ZFly")
        }
        updateStatusIcon()
    }

    private func updateStatusIcon() {
        let running = withObservationTracking {
            store.management?.running == true
        } onChange: { [weak self] in
            Task { @MainActor [weak self] in self?.updateStatusIcon() }
        }
        let image = NSImage(systemSymbolName: running ? "paperplane.fill" : "paperplane", accessibilityDescription: "ZFly")
        image?.isTemplate = true
        statusItem.button?.image = image
    }

    @objc private func togglePopover(_ sender: Any?) {
        if popover.isShown { popover.performClose(sender) }
        else { show() }
    }

    @discardableResult
    func show() -> NSWindow? {
        guard let button = statusItem.button else { return nil }
        applyAppearance(PreviewOptions.preferences.string(forKey: "appearance.scheme") ?? "system")
        popover.animates = !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        let window = popover.contentViewController?.view.window
        // A nil responder sends focus back into SwiftUI's first button. Start
        // on a neutral responder instead; Tab still enters the normal key loop.
        window?.initialFirstResponder = initialFocus
        window?.makeFirstResponder(initialFocus)
        return window
    }

    private func applyAppearance(_ preference: String) {
        // A status popover otherwise inherits its menu bar anchor's appearance.
        AppAppearance.apply(preference, to: popover)
    }
}

private final class StatusMenuInitialFocusView: NSView {
    override var acceptsFirstResponder: Bool { true }
    override var canBecomeKeyView: Bool { false }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 48 {
            if event.modifierFlags.contains(.shift) { window?.selectPreviousKeyView(nil) }
            else { window?.selectNextKeyView(nil) }
        } else {
            super.keyDown(with: event)
        }
    }
}

private struct StatusMenuRoot: View {
    let store: Store
    let openMainWindow: () -> Void
    let applyAppearance: (String) -> Void
    @AppStorage("appearance.language", store: PreviewOptions.preferences) private var language = AppLanguage.system.rawValue
    @AppStorage("appearance.scheme", store: PreviewOptions.preferences) private var scheme = AppAppearance.system.rawValue

    var body: some View {
        StatusMenu(openMainWindow: openMainWindow)
            .environment(store)
            .environment(\.locale, AppLanguage(rawValue: language)?.locale ?? .current)
            .modifier(AppAccentStyle())
            .onChange(of: scheme, initial: true) { _, value in applyAppearance(value) }
    }
}
