import Foundation

public enum ControllerError: LocalizedError, Equatable {
    case invalidEndpoint
    case invalidSecret
    case invalidResponse
    case invalidConfig(String)
    case http(Int)

    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint: "ZFly 本地运行时地址无效，请重试启动。"
        case .invalidSecret: "控制器密钥不能包含换行或控制字符。"
        case .invalidResponse: "控制器返回了无法识别的数据。"
        case .invalidConfig(let message): message
        case .http(401), .http(403): "控制器拒绝访问，请检查控制器密钥。"
        case .http(300..<400): "控制器发生重定向，已停止请求。"
        case .http(503), .http(504): "核心暂时无法完成请求，请稍后重试。"
        case .http(let status): "控制器请求失败（HTTP \(status)）。"
        }
    }
}

public struct ControllerSettings: Sendable {
    public let endpoint: URL
    private let secret: String

    public init(endpoint: String, secret: String) throws {
        guard let parts = URLComponents(string: endpoint.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(parts.scheme?.lowercased() ?? ""),
              ["localhost", "127.0.0.1", "[::1]", "::1"].contains(parts.host?.lowercased() ?? ""),
              parts.user == nil, parts.password == nil, parts.query == nil,
              parts.fragment == nil, parts.path.isEmpty || parts.path == "/",
              parts.port.map({ (1...65535).contains($0) }) ?? true,
              let url = parts.url else { throw ControllerError.invalidEndpoint }
        guard !secret.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw ControllerError.invalidSecret
        }
        self.endpoint = url
        self.secret = secret
    }

    public func request(
        _ method: String, segments: [String], query: [URLQueryItem] = [], body: Data? = nil
    ) throws -> URLRequest {
        guard var parts = URLComponents(url: endpoint, resolvingAgainstBaseURL: false) else {
            throw ControllerError.invalidEndpoint
        }
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
        parts.percentEncodedPath = "/" + segments.map {
            $0.addingPercentEncoding(withAllowedCharacters: allowed) ?? ""
        }.joined(separator: "/")
        parts.queryItems = query.isEmpty ? nil : query
        guard let url = parts.url else { throw ControllerError.invalidEndpoint }
        var request = URLRequest(url: url, timeoutInterval: 8)
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if !secret.isEmpty { request.setValue("Bearer " + secret, forHTTPHeaderField: "Authorization") }
        return request
    }
}

public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, Int)
}

final class NoRedirects: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(
        _ session: URLSession, task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

public final class SessionTransport: HTTPTransport {
    private let session: URLSession

    public init(resourceTimeout: TimeInterval = 10) {
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]
        config.urlCredentialStorage = nil
        config.httpCookieStorage = nil
        config.urlCache = nil
        config.timeoutIntervalForResource = resourceTimeout
        session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }

    deinit { session.invalidateAndCancel() }

    public func send(_ request: URLRequest) async throws -> (Data, Int) {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw ControllerError.invalidResponse }
        return (data, response.statusCode)
    }
}

public struct Controller: Sendable {
    private let settings: ControllerSettings
    private let transport: any HTTPTransport

    public init(settings: ControllerSettings, transport: any HTTPTransport = SessionTransport()) {
        self.settings = settings
        self.transport = transport
    }

    private func fetch<T: Decodable & Sendable>(_ segments: [String], as type: T.Type) async throws -> T {
        let data = try await perform(settings.request("GET", segments: segments))
        do { return try JSONDecoder().decode(type, from: data) }
        catch { throw ControllerError.invalidResponse }
    }

    private func perform(_ request: URLRequest) async throws -> Data {
        let (data, status) = try await transport.send(request)
        guard (200..<300).contains(status) else { throw ControllerError.http(status) }
        return data
    }

    public func snapshot() async throws -> Snapshot {
        async let version = fetch(["version"], as: VersionInfo.self)
        async let configs = fetch(["configs"], as: CoreConfig.self)
        async let proxies = fetch(["proxies"], as: ProxyResponse.self)
        async let connections = fetch(["connections"], as: ConnectionsSnapshot.self)
        return try await Snapshot(version: version, configs: configs, proxies: proxies, connections: connections)
    }

    public func connections() async throws -> ConnectionsSnapshot {
        try await fetch(["connections"], as: ConnectionsSnapshot.self)
    }

    public func select(group: String, proxy: String) async throws {
        let body = try JSONEncoder().encode(["name": proxy])
        _ = try await perform(settings.request("PUT", segments: ["proxies", group], body: body))
    }

    public func unfix(group: String) async throws {
        _ = try await perform(settings.request("DELETE", segments: ["proxies", group]))
    }

    public func configure(_ patch: CoreConfig) async throws {
        try patch.validate()
        guard !patch.isEmpty else { return }
        let body = try JSONEncoder().encode(patch)
        _ = try await perform(settings.request("PATCH", segments: ["configs"], body: body))
    }

    public func configuration() async throws -> CoreConfig {
        try await fetch(["configs"], as: CoreConfig.self)
    }

    public func delay(proxy: String) async throws -> Int {
        let query = [
            URLQueryItem(name: "timeout", value: "5000"),
        ]
        let data = try await perform(settings.request("GET", segments: ["proxies", proxy, "delay"], query: query))
        guard let result = try? JSONDecoder().decode(DelayInfo.self, from: data) else {
            throw ControllerError.invalidResponse
        }
        return result.delay
    }

    public func close(connection: String) async throws {
        _ = try await perform(settings.request("DELETE", segments: ["connections", connection]))
    }

    public func inspect(_ tool: String, name: String = "", type: String = "A") async throws -> String {
        let request: URLRequest
        switch tool {
        case "rules": request = try settings.request("GET", segments: ["rules"])
        case "dnsQuery":
            guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "HTTPS"].contains(type) else {
                throw ControllerError.invalidConfig("请输入域名并选择查询类型。")
            }
            request = try settings.request("GET", segments: ["dns", "query"], query: [.init(name: "name", value: name), .init(name: "type", value: type)])
        case "flushDNS": request = try settings.request("POST", segments: ["cache", "dns", "flush"])
        case "flushFakeIP": request = try settings.request("POST", segments: ["cache", "fakeip", "flush"])
        case "closeConnections": request = try settings.request("DELETE", segments: ["connections"])
        default: throw ControllerError.invalidConfig("不支持的核心工具。")
        }
        let data = try await perform(request)
        guard !data.isEmpty else { return "操作已完成。" }
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let formatted = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
              let result = String(data: formatted, encoding: .utf8) else { throw ControllerError.invalidResponse }
        return result
    }
}
