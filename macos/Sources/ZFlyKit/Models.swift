import Foundation

public struct VersionInfo: Decodable, Equatable, Sendable {
    public let version: String
}

public struct CoreConfig: Codable, Equatable, Sendable {
    public var mode: String?
    public var mixedPort: Int?
    public var port: Int?
    public var socksPort: Int?
    public var logLevel: String?
    public var ipv6: Bool?
    public var allowLan: Bool?
    public var bindAddress: String?
    public var tcpConcurrent: Bool?
    public var sniffing: Bool?
    public var findProcessMode: String?
    public var interfaceName: String?

    public init() {}

    public var isEmpty: Bool { self == CoreConfig() }

    public func changes(from baseline: Self) -> Self {
        var patch = Self()
        for key in stringKeys where self[keyPath: key] != baseline[keyPath: key] {
            patch[keyPath: key] = self[keyPath: key]
        }
        for key in portKeys where self[keyPath: key] != baseline[keyPath: key] {
            patch[keyPath: key] = self[keyPath: key]
        }
        for key in boolKeys where self[keyPath: key] != baseline[keyPath: key] {
            patch[keyPath: key] = self[keyPath: key]
        }
        return patch
    }

    public func merging(_ patch: Self) -> Self {
        var result = self
        for key in stringKeys where patch[keyPath: key] != nil { result[keyPath: key] = patch[keyPath: key] }
        for key in portKeys where patch[keyPath: key] != nil { result[keyPath: key] = patch[keyPath: key] }
        for key in boolKeys where patch[keyPath: key] != nil { result[keyPath: key] = patch[keyPath: key] }
        return result
    }

    public func matches(_ actual: Self) -> Bool { actual.merging(self) == actual }

    public func conflicts(with current: Self, baseline: Self) -> Bool {
        for key in stringKeys where self[keyPath: key] != nil {
            if current[keyPath: key] != baseline[keyPath: key], current[keyPath: key] != self[keyPath: key] { return true }
        }
        for key in portKeys where self[keyPath: key] != nil {
            if current[keyPath: key] != baseline[keyPath: key], current[keyPath: key] != self[keyPath: key] { return true }
        }
        for key in boolKeys where self[keyPath: key] != nil {
            if current[keyPath: key] != baseline[keyPath: key], current[keyPath: key] != self[keyPath: key] { return true }
        }
        return false
    }

    public func validate() throws {
        for key in portKeys {
            if let value = self[keyPath: key], !(0...65535).contains(value) {
                throw ControllerError.invalidConfig("端口应为 0–65535，0 表示关闭该监听端口。")
            }
        }
        let ports = portKeys.compactMap { self[keyPath: $0] }.filter { $0 != 0 }
        guard Set(ports).count == ports.count else {
            throw ControllerError.invalidConfig("混合、HTTP 和 SOCKS 端口不能重复。")
        }
        for (value, allowed) in [
            (mode, ["rule", "global", "direct"]),
            (logLevel, ["silent", "error", "warning", "info", "debug"]),
            (findProcessMode, ["off", "strict", "always"]),
        ] {
            if let value, !allowed.contains(value) { throw ControllerError.invalidConfig("不支持的设置值：" + value) }
        }
        for key in stringKeys {
            if let value = self[keyPath: key], value.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) {
                throw ControllerError.invalidConfig("设置不能包含换行或控制字符。")
            }
        }
        if let bindAddress, bindAddress.isEmpty || bindAddress.contains(where: \.isWhitespace) {
            throw ControllerError.invalidConfig("请输入有效的监听地址，例如 127.0.0.1 或 *。")
        }
    }

    private var stringKeys: [WritableKeyPath<Self, String?>] {
        [\.mode, \.logLevel, \.bindAddress, \.findProcessMode, \.interfaceName]
    }
    private var portKeys: [WritableKeyPath<Self, Int?>] { [\.mixedPort, \.port, \.socksPort] }
    private var boolKeys: [WritableKeyPath<Self, Bool?>] { [\.ipv6, \.allowLan, \.tcpConcurrent, \.sniffing] }

    enum CodingKeys: String, CodingKey {
        case mode, port, ipv6, sniffing
        case mixedPort = "mixed-port"
        case socksPort = "socks-port"
        case logLevel = "log-level"
        case allowLan = "allow-lan"
        case bindAddress = "bind-address"
        case tcpConcurrent = "tcp-concurrent"
        case findProcessMode = "find-process-mode"
        case interfaceName = "interface-name"
    }
}

public struct ProxyInfo: Decodable, Equatable, Sendable {
    public let type: String
    public let all: [String]?
    public var now: String?
    public var fixed: String?
    public let history: [DelayInfo]?
    public let hidden: Bool?

    public var lastDelay: Int? { history?.last?.delay }
    public var isAutomatic: Bool { type == "URLTest" || type == "Fallback" }
    public var canSelect: Bool { type == "Selector" || isAutomatic }
}

public struct DelayInfo: Decodable, Equatable, Sendable {
    public let delay: Int
}

public struct ProxyResponse: Decodable, Equatable, Sendable {
    public var proxies: [String: ProxyInfo]

    public var groupNames: [String] {
        proxies.keys.filter { proxies[$0]?.all != nil }.sorted()
    }

    public func groupNames(mode: String) -> [String] {
        let groups = groupNames.filter { proxies[$0]?.hidden != true && ($0 != "GLOBAL" || mode == "global") }
        let order = proxies["GLOBAL"]?.all ?? []
        return order.filter { groups.contains($0) } + groups.filter { !order.contains($0) }
    }
}

public struct ConnectionMetadata: Decodable, Equatable, Sendable {
    public let host: String?
    public let destinationIP: String?
    public let destinationPort: String?
    public let network: String?
    public let process: String?
}

public struct ConnectionInfo: Decodable, Equatable, Identifiable, Sendable {
    public let id: String
    public let metadata: ConnectionMetadata?
    public let upload: Int64?
    public let download: Int64?
    public let chains: [String]?
    public let rule: String?

    public var destination: String {
        let host = metadata?.host.flatMap { $0.isEmpty ? nil : $0 }
            ?? metadata?.destinationIP ?? "未知目标"
        return host + (metadata?.destinationPort.map { ":" + $0 } ?? "")
    }
}

public struct ConnectionsSnapshot: Decodable, Equatable, Sendable {
    public let downloadTotal: Int64?
    public let uploadTotal: Int64?
    public let memory: Int64?
    public var connections: [ConnectionInfo]?

    public var items: [ConnectionInfo] { connections ?? [] }
}

public struct Snapshot: Decodable, Sendable {
    public let version: VersionInfo
    public var configs: CoreConfig
    public var proxies: ProxyResponse
    public var connections: ConnectionsSnapshot

    public static func preview() throws -> Snapshot {
        try JSONDecoder().decode(Snapshot.self, from: previewData())
    }

    static func previewData() throws -> Data {
        let packaged = Bundle.main.resourceURL
            .flatMap { Bundle(url: $0.appendingPathComponent("ZFly_ZFlyKit.bundle")) }
        guard let url = (packaged ?? Bundle.module).url(forResource: "preview", withExtension: "json") else {
            throw ControllerError.invalidResponse
        }
        return try Data(contentsOf: url)
    }
}
