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

public struct PreviewCatalog {
    private var documents: [String: [String: ManagedDocument]] = [:]
    private var versions: [String: Int] = [:]
    public init() {}

    public mutating func perform(_ command: ManagementCommand, state: ManagedState) throws -> ManagementResult {
        guard let kind = CatalogKind(rawValue: command.section ?? "") else { throw ControllerError.invalidConfig("不支持的资源分类。") }
        if kind == .groups, !state.profiles.contains(where: { $0.id == command.name && $0.type == "custom" }) {
            throw ControllerError.invalidConfig("请先选择自定义配置。")
        }
        let scope = kind.rawValue + ":" + (kind == .groups ? command.name ?? "" : "")
        let records = documents[scope] ?? [:]
        let current: ManagedDocument
        if let id = command.id {
            guard let document = records[id] else { throw ControllerError.invalidConfig("资源已被删除。") }
            current = document
        } else {
            current = ManagedDocument(section: kind.rawValue, title: kind.title, text: kind.template,
                                      revision: "new:\(scope):\(versions[scope] ?? 0)", writable: true)
        }
        if command.action == "catalog.read" { return ManagementResult(state: state, document: current) }
        if ["catalog.save", "catalog.delete"].contains(command.action) {
            guard current.revision == command.revision else { throw ControllerError.invalidConfig("资源已修改，请重新载入。") }
            if command.action == "catalog.delete" {
                guard let id = command.id else { throw ControllerError.invalidConfig("请先选择资源。") }
                documents[scope, default: [:]].removeValue(forKey: id)
            } else {
                let text = command.text ?? ""
                guard let value = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
                      let label = value["name"] as? String, !label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    throw ControllerError.invalidConfig("资源需要有效的 JSON 对象和名称。")
                }
                for (id, document) in records where id != command.id {
                    let existing = try JSONSerialization.jsonObject(with: Data(document.text.utf8)) as? [String: Any]
                    if existing?["name"] as? String == label { throw ControllerError.invalidConfig("名称已存在。") }
                }
                let id = command.id ?? UUID().uuidString
                documents[scope, default: [:]][id] = ManagedDocument(section: kind.rawValue, title: kind.title, text: text,
                                                                    revision: UUID().uuidString, writable: true)
            }
            versions[scope, default: 0] += 1
        } else if command.action != "catalog.list" { throw ControllerError.invalidConfig("不支持的资源操作。") }
        let items = try (documents[scope] ?? [:]).map { id, document in
            let value = try JSONSerialization.jsonObject(with: Data(document.text.utf8)) as? [String: Any] ?? [:]
            return CatalogItem(id: id, label: value["name"] as? String ?? "", detail: value["type"] as? String ?? "本地资源")
        }.sorted { $0.label < $1.label }
        return ManagementResult(state: state, items: items)
    }
}
