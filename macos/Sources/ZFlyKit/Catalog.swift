import Foundation

public enum CatalogKind: String, CaseIterable, Identifiable, Sendable {
    case proxies, groups
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .proxies: "自定义节点"
        case .groups: "自定义代理组"
        }
    }
    public var template: String {
        switch self {
        case .proxies: "{\n  \"name\": \"新节点\",\n  \"type\": \"socks5\",\n  \"server\": \"127.0.0.1\",\n  \"port\": 1080\n}"
        case .groups: "{\n  \"name\": \"新代理组\",\n  \"type\": \"select\",\n  \"proxies\": [\"DIRECT\"]\n}"
        }
    }
}

public struct CatalogItem: Decodable, Identifiable, Sendable {
    public let id: String
    public let label: String
    public let detail: String
    public init(id: String, label: String, detail: String) { self.id = id; self.label = label; self.detail = detail }
}
