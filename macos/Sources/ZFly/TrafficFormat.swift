import Foundation

private let trafficLocale = Locale(identifier: "en_US_POSIX")
private let trafficUnits = ["B", "KB", "MB", "GB", "TB", "PB", "EB"]

/// Network counters always use the same English symbols, regardless of app/system language.
func bytes(_ value: Int64?) -> String {
    var amount = Double(max(0, value ?? 0))
    var unit = 0
    while amount >= 1024, unit < trafficUnits.count - 1 {
        amount /= 1024
        unit += 1
    }
    // Promote values that would otherwise round to "1024 KB", etc.
    if amount >= 1023.95, unit < trafficUnits.count - 1 {
        amount /= 1024
        unit += 1
    }
    let rounded = (amount * 10).rounded() / 10
    let number = String(format: rounded == rounded.rounded() ? "%.0f" : "%.1f", locale: trafficLocale, rounded)
    return number + " " + trafficUnits[unit]
}
