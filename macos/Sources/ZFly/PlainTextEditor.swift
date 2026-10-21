import AppKit
import SwiftUI

// Native text handling keeps read-only documents selectable and YAML literal.
struct PlainTextEditor: NSViewRepresentable {
    @Binding var text: String
    var editable: Bool

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    static func textView(editable: Bool) -> NSTextView {
        let view = NSTextView()
        view.isEditable = editable
        view.isSelectable = true
        view.isRichText = false
        view.font = .monospacedSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
        view.drawsBackground = false
        view.isAutomaticQuoteSubstitutionEnabled = false
        view.isAutomaticDashSubstitutionEnabled = false
        view.isAutomaticTextReplacementEnabled = false
        view.isAutomaticSpellingCorrectionEnabled = false
        view.isContinuousSpellCheckingEnabled = false
        view.allowsUndo = editable
        view.autoresizingMask = [.width]
        view.isVerticallyResizable = true
        view.textContainer?.widthTracksTextView = true
        return view
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false
        let view = Self.textView(editable: editable)
        view.string = text
        view.delegate = context.coordinator
        scroll.documentView = view
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let view = scroll.documentView as? NSTextView else { return }
        view.isEditable = editable
        if view.string != text { view.string = text }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: PlainTextEditor
        init(_ parent: PlainTextEditor) { self.parent = parent }
        func textDidChange(_ notification: Notification) {
            guard parent.editable, let view = notification.object as? NSTextView else { return }
            parent.text = view.string
        }
    }
}
