import AppKit

enum AppAppearance: String {
    case system, light, dark

    @MainActor
    static func apply(_ preference: String, to target: any NSAppearanceCustomization) {
        let selection = AppAppearance(rawValue: preference) ?? .system
        switch selection {
        case .system: target.appearance = nil
        case .light: target.appearance = NSAppearance(named: .aqua)
        case .dark: target.appearance = NSAppearance(named: .darkAqua)
        }
    }
}

enum AppLanguage: String, CaseIterable, Identifiable {
    case system
    case chinese = "zh-Hans"
    case english = "en"

    var id: String { rawValue }

    var locale: Locale {
        switch self {
        case .system: .current
        case .chinese: Locale(identifier: "zh-Hans")
        case .english: Locale(identifier: "en")
        }
    }
}
