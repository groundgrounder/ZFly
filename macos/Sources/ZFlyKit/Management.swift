import Foundation

public struct ManagedProfile: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public var label: String
    public let type: String
    public var url: String
    public var autoUpdate: Bool
    public var updateHours: Int
    public let updated: String?
    public let upload: Int64?
    public let download: Int64?
    public let total: Int64?
    public let expire: Int64?
    public var revision: String?

    public init(id: String, label: String, type: String, url: String = "") {
        self.id = id
        self.label = label
        self.type = type
        self.url = url
        autoUpdate = type == "url"
        updateHours = 24
        updated = nil
        upload = nil
        download = nil
        total = nil
        expire = nil
        revision = nil
    }
    private enum CodingKeys: String, CodingKey { case id, label, type, url, autoUpdate, updateHours, updated, upload, download, total, expire, revision }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        label = try c.decode(String.self, forKey: .label)
        type = try c.decode(String.self, forKey: .type)
        url = try c.decodeIfPresent(String.self, forKey: .url) ?? ""
        autoUpdate = try c.decodeIfPresent(Bool.self, forKey: .autoUpdate) ?? false
        updateHours = try c.decodeIfPresent(Int.self, forKey: .updateHours) ?? 24
        updated = try c.decodeIfPresent(String.self, forKey: .updated)
        upload = try c.decodeIfPresent(Int64.self, forKey: .upload)
        download = try c.decodeIfPresent(Int64.self, forKey: .download)
        total = try c.decodeIfPresent(Int64.self, forKey: .total)
        expire = try c.decodeIfPresent(Int64.self, forKey: .expire)
        revision = try c.decodeIfPresent(String.self, forKey: .revision)
    }

}

public struct ManagedNetwork: Codable, Equatable, Sendable {
    public var tunEnabled: Bool
    public var tunStack: String
    public var tunDevice: String
    public var systemProxy: Bool
    public var bypassDomains: [String]
    public var bypassPrivateRoute: Bool
    public var autoSetSystemDns: Bool
    public var appendSystemDns: Bool
    public var mode: String
    public var mixedPort: Int
    public var ipv6: Bool
    public var allowLan: Bool
    public var tcpConcurrent: Bool
    public var logLevel: String
    private enum CodingKeys: String, CodingKey { case tunEnabled, tunStack, tunDevice, systemProxy, bypassDomains, bypassPrivateRoute, autoSetSystemDns, appendSystemDns, mode, mixedPort, ipv6, allowLan, tcpConcurrent, logLevel }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tunEnabled = try c.decodeIfPresent(Bool.self, forKey: .tunEnabled) ?? false
        tunStack = try c.decodeIfPresent(String.self, forKey: .tunStack) ?? "mixed"
        tunDevice = try c.decodeIfPresent(String.self, forKey: .tunDevice) ?? "zfly"
        systemProxy = try c.decodeIfPresent(Bool.self, forKey: .systemProxy) ?? false
        bypassDomains = try c.decodeIfPresent([String].self, forKey: .bypassDomains) ?? []
        bypassPrivateRoute = try c.decodeIfPresent(Bool.self, forKey: .bypassPrivateRoute) ?? true
        autoSetSystemDns = try c.decodeIfPresent(Bool.self, forKey: .autoSetSystemDns) ?? false
        appendSystemDns = try c.decodeIfPresent(Bool.self, forKey: .appendSystemDns) ?? false
        mode = try c.decodeIfPresent(String.self, forKey: .mode) ?? "rule"
        mixedPort = try c.decodeIfPresent(Int.self, forKey: .mixedPort) ?? 7890
        ipv6 = try c.decodeIfPresent(Bool.self, forKey: .ipv6) ?? false
        allowLan = try c.decodeIfPresent(Bool.self, forKey: .allowLan) ?? false
        tcpConcurrent = try c.decodeIfPresent(Bool.self, forKey: .tcpConcurrent) ?? true
        logLevel = try c.decodeIfPresent(String.self, forKey: .logLevel) ?? "info"
    }

}

public struct ManagedState: Decodable, Equatable, Sendable {
    public let apiVersion: Int
    public var currentProfileID: String?
    public var running: Bool
    public let coreStatus: String
    public let tunAuthorization: String
    public var profiles: [ManagedProfile]
    public var network: ManagedNetwork
    public let updateErrors: [String: String]?

    private enum CodingKeys: String, CodingKey { case apiVersion, currentProfileID, running, coreStatus, tunAuthorization, profiles, network, updateErrors }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        apiVersion = try c.decode(Int.self, forKey: .apiVersion)
        currentProfileID = try c.decodeIfPresent(String.self, forKey: .currentProfileID)
        running = try c.decodeIfPresent(Bool.self, forKey: .running) ?? false
        coreStatus = try c.decodeIfPresent(String.self, forKey: .coreStatus) ?? "状态暂不可用"
        tunAuthorization = try c.decodeIfPresent(String.self, forKey: .tunAuthorization) ?? "权限状态暂不可用"
        profiles = try c.decode([ManagedProfile].self, forKey: .profiles)
        network = try c.decode(ManagedNetwork.self, forKey: .network)
        updateErrors = try c.decodeIfPresent([String: String].self, forKey: .updateErrors)
    }

    public static func preview() throws -> Self {
        struct Fixture: Decodable { let management: ManagedState }
        var state = try JSONDecoder().decode(Fixture.self, from: Snapshot.previewData()).management
        state.profiles = state.profiles.map { profile in
            var profile = profile
            profile.revision = "preview:\(profile.id)"
            return profile
        }
        return state
    }
}

public struct ManagedFile: Decodable, Sendable {
    public let name: String
    public let data: String
    public let revision: String

    public init(name: String, data: String, revision: String) {
        self.name = name
        self.data = data
        self.revision = revision
    }
}

public struct ManagementResult: Decodable, Sendable {
    public let state: ManagedState
    public let file: ManagedFile?
    public let document: ManagedDocument?
    public let message: String?
    public let items: [CatalogItem]?

    public init(state: ManagedState, file: ManagedFile? = nil, document: ManagedDocument? = nil, message: String? = nil, items: [CatalogItem]? = nil) {
        self.state = state; self.file = file; self.document = document; self.message = message
        self.items = items
    }
}

public struct ManagedDocument: Codable, Sendable {
    public let section: String
    public let title: String
    public var text: String
    public let revision: String
    public let writable: Bool
    public let format: String

    public init(section: String, title: String, text: String, revision: String, writable: Bool, format: String = "json") {
        self.section = section; self.title = title; self.text = text
        self.revision = revision; self.writable = writable; self.format = format
    }
}

public enum WorkspaceSection: String, CaseIterable, Identifiable, Sendable {
    case core, dns, tun, geo, app, dav, profileOverrides
    case globalRules, profileRules, logs, dnsQueries, requests, providers, resources
    public var id: String { rawValue }
    public static var supported: [Self] { [.core, .dns, .tun, .geo, .app, .dav, .profileOverrides, .globalRules, .profileRules, .logs, .dnsQueries, .requests, .providers, .resources] }
    public var title: String {
        switch self {
        case .core: "持久化核心设置"
        case .dns: "DNS 与 hosts"
        case .tun: "TUN 与路由覆盖"
        case .geo: "Geo 资源"
        case .app: "启动与测试"
        case .dav: "WebDAV 备份连接"
        case .profileOverrides: "配置专属覆盖"
        case .globalRules: "全局追加规则"
        case .profileRules: "配置追加规则"
        case .logs: "日志"
        case .dnsQueries: "DNS 记录"
        case .requests: "请求历史"
        case .providers: "运行中的提供者"
        case .resources: "资源清单"
        }
    }
    public var profileSpecific: Bool { [.profileOverrides, .profileRules].contains(self) }
    public var writable: Bool { ![.logs, .dnsQueries, .requests, .providers, .resources].contains(self) }
}

public struct ManagementCommand: Encodable, Sendable {
    public var action: String
    public var id: String?
    public var label: String?
    public var url: String?
    public var ageSecretKey: String?
    public var autoUpdate: Bool?
    public var updateHours: Int?
    public var running: Bool?
    public var data: String?
    public var revision: String?
    public var network: ManagedNetwork?
    public var expected: ManagedNetwork?
    public var section: String?
    public var text: String?
    public var tool: String?
    public var name: String?
    public var group: String?
    public var proxy: String?

    public init(_ action: String) { self.action = action }
}

public struct ManagementClient: Sendable {
    private struct Failure: Decodable { let message: String }
    private let settings: ControllerSettings
    private let transport: any HTTPTransport

    public init(settings: ControllerSettings, transport: any HTTPTransport = SessionTransport(resourceTimeout: 300)) {
        self.settings = settings
        self.transport = transport
    }

    private func request<T: Decodable>(_ method: String, path: String, body: Data? = nil) async throws -> T {
        var request = try settings.request(method, segments: [path], body: body)
        request.timeoutInterval = method == "POST" ? 240 : 8
        let (data, status) = try await transport.send(request)
        guard (200..<300).contains(status) else {
            if let failure = try? JSONDecoder().decode(Failure.self, from: data) {
                throw ControllerError.invalidConfig(failure.message)
            }
            throw ControllerError.http(status)
        }
        guard let value = try? JSONDecoder().decode(T.self, from: data) else { throw ControllerError.invalidResponse }
        return value
    }

    public func snapshot() async throws -> ManagedState {
        let state: ManagedState = try await request("GET", path: "state")
        return try validate(state)
    }

    public func perform(_ command: ManagementCommand) async throws -> ManagementResult {
        let result: ManagementResult = try await request("POST", path: "action", body: JSONEncoder().encode(command))
        return ManagementResult(state: try validate(result.state), file: result.file, document: result.document, message: result.message, items: result.items)
    }

    private func validate(_ state: ManagedState) throws -> ManagedState {
        guard state.apiVersion == 1 else { throw ControllerError.invalidConfig("本地运行时版本不兼容，请重新安装完整的 ZFly。") }
        guard ["rule", "global", "direct"].contains(state.network.mode),
              ["silent", "error", "warning", "info", "debug"].contains(state.network.logLevel),
              ["gvisor", "system", "mixed"].contains(state.network.tunStack),
              (1...65535).contains(state.network.mixedPort),
              !state.network.tunDevice.isEmpty,
              state.network.tunDevice.count <= 32,
              state.network.tunDevice.allSatisfy({ $0.isLetter || $0.isNumber || "_.-".contains($0) }) else {
            throw ControllerError.invalidResponse
        }
        let identities = Set(state.profiles.map(\.id))
        guard identities.count == state.profiles.count,
              state.currentProfileID == nil || identities.contains(state.currentProfileID!),
              state.profiles.allSatisfy({ profile in
                  !profile.id.isEmpty && !profile.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                      && ["file", "custom", "url"].contains(profile.type)
                      && (1...8760).contains(profile.updateHours)
                      && (profile.type != "url" || Self.validHTTPURL(profile.url))
              }) else {
            throw ControllerError.invalidResponse
        }
        return state
    }

    private static func validHTTPURL(_ value: String) -> Bool {
        guard let components = URLComponents(string: value),
              let scheme = components.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              components.host != nil,
              components.user == nil,
              components.password == nil else { return false }
        return true
    }
}
