import Foundation

/// AppStorage still receives normal defaults observation, while writes remain volatile.
final class PreviewPreferences: UserDefaults, @unchecked Sendable {
    let domain: String
    private let mutationLock = NSRecursiveLock()

    init(domain: String = "io.github.zfly.preview." + UUID().uuidString) {
        self.domain = domain
        super.init(suiteName: domain)!
        setVolatileDomain([:], forName: UserDefaults.argumentDomain)
    }

    override func set(_ value: Any?, forKey key: String) {
        mutationLock.lock()
        defer { mutationLock.unlock() }
        willChangeValue(forKey: key)
        var values = volatileDomain(forName: UserDefaults.argumentDomain)
        values[key] = value
        setVolatileDomain(values, forName: UserDefaults.argumentDomain)
        didChangeValue(forKey: key)
        NotificationCenter.default.post(name: UserDefaults.didChangeNotification, object: self)
    }

    override func set(_ value: Bool, forKey key: String) { set(value as Any, forKey: key) }
    override func set(_ value: Int, forKey key: String) { set(value as Any, forKey: key) }
    override func set(_ value: Float, forKey key: String) { set(value as Any, forKey: key) }
    override func set(_ value: Double, forKey key: String) { set(value as Any, forKey: key) }
    override func removeObject(forKey key: String) { set(nil as Any?, forKey: key) }
}

/// All review overrides are inert unless the app was explicitly launched in safe mode.
enum PreviewOptions {
    // A fresh volatile domain keeps preview controls away from the installed
    // application's preferences, including when the bundle identifier matches.
    @MainActor static let preferences: UserDefaults = {
        guard CommandLine.arguments.contains("--safe-mode") else { return .standard }
        let preferences = PreviewPreferences()
        if let accent = value("--preview-accent"), AppAccent(rawValue: accent) != nil {
            preferences.set(accent, forKey: "appearance.accent")
        }
        if let scheme = value("--preview-scheme"), AppAppearance(rawValue: scheme) != nil {
            preferences.set(scheme, forKey: "appearance.scheme")
        }
        if let language = value("--preview-language"), AppLanguage(rawValue: language) != nil {
            preferences.set(language, forKey: "appearance.language")
        }
        return preferences
    }()

    static func value(_ option: String) -> String? {
        let arguments = CommandLine.arguments
        guard arguments.contains("--safe-mode"), let index = arguments.firstIndex(of: option),
              arguments.indices.contains(index + 1) else { return nil }
        return arguments[index + 1]
    }
}
