import Foundation

/// All review overrides are inert unless the app was explicitly launched in safe mode.
enum PreviewOptions {
    static func value(_ option: String) -> String? {
        let arguments = CommandLine.arguments
        guard arguments.contains("--safe-mode"), let index = arguments.firstIndex(of: option),
              arguments.indices.contains(index + 1) else { return nil }
        return arguments[index + 1]
    }
}
