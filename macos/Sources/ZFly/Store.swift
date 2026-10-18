import Foundation
import SwiftUI
import ZFlyKit

struct TrafficSample: Identifiable {
    let id = UUID()
    let download: Double
    let upload: Double
}

@MainActor
final class Store: ObservableObject {
    let preview: Bool
    @Published private(set) var snapshot: Snapshot?
    @Published private(set) var busy = false
    @Published private(set) var refreshingStatus = false
    @Published private(set) var connected = false
    @Published private(set) var status = "正在启动 ZFly…"
    @Published private(set) var endpoint = "ZFly 内置核心"
    @Published private(set) var dataDirectory = ""
    @Published private(set) var updated: Date?
    @Published private(set) var delays: [String: Int] = [:]
    @Published var actionError: String?
    @Published private(set) var management: ManagedState?
    @Published private(set) var managementBusy = false
    @Published private(set) var managementStatus = "正在启动本地运行时…"
    @Published private(set) var managementEndpoint = ""
    @Published private(set) var sessionID = 0
    @Published private(set) var traffic: [TrafficSample] = []
    @Published private(set) var starting = false
    private var previousTraffic: (date: Date, download: Int64, upload: Int64)?
    var downloadRate: Int64 { Int64(traffic.last?.download ?? 0) }
    var uploadRate: Int64 { Int64(traffic.last?.upload ?? 0) }
    private var controller: Controller?
    private var managementClient: ManagementClient?
    private var previewFiles: [String: ManagedFile] = [:]
    private var previewDocuments: [String: ManagedDocument] = [:]
    private var previewCatalog = PreviewCatalog()
    private var generation = 0
    private var coreRevision = 0
    private var managementRevision = 0
    private var managementRefreshing = false
    private var refreshing = false
    private var polling = false
    private var pollingTask: Task<Void, Never>?

    var isBusy: Bool { starting || busy || managementBusy }

    func startPolling() {
        guard pollingTask == nil else { return }
        pollingTask = Task { await poll() }
    }

    init(preview: Bool) {
        self.preview = preview
        if preview {
            do {
                snapshot = try Snapshot.preview()
                management = try ManagedState.preview()
                managementStatus = "演示模式"
                connected = true
                status = "演示模式 · 不连接核心"
                traffic = (0..<40).map { index in
                    TrafficSample(download: (0.4 + abs(sin(Double(index) * 0.38)) * 2.6) * 1_048_576,
                                  upload: (0.08 + abs(cos(Double(index) * 0.3)) * 0.4) * 1_048_576)
                }
                switch PreviewOptions.value("--preview-state") {
                case "empty":
                    management?.profiles = []
                    management?.currentProfileID = nil
                    management?.running = false
                    snapshot?.proxies.proxies = [:]
                    snapshot?.connections.connections = []
                    traffic = []
                case "stopped":
                    management?.running = false
                    snapshot?.connections.connections = []
                    traffic = []
                default: break
                }
            } catch { status = error.localizedDescription }
        }
    }

    func startRuntime() async {
        guard !preview, !starting, !busy, !managementBusy else { return }
        starting = true
        status = "正在启动 ZFly 内置核心…"
        managementStatus = status
        defer { starting = false }
        do {
            if let client = managementClient, NativeRuntime.shared.process?.isRunning == true {
                var command = ManagementCommand("tool.run"); command.tool = "restart"
                managementBusy = true
                defer { managementBusy = false }
                let result = try await client.perform(command)
                management = result.state
                status = result.state.coreStatus
                return
            }
            let connection = try await NativeRuntime.shared.start()
            dataDirectory = connection.dataDirectory
            try connect(endpoint: connection.endpoint, secret: connection.secret, managementEndpoint: connection.endpoint)
            await refreshManagement()
            await refresh()
        } catch {
            status = error.localizedDescription
            managementStatus = status
        }
    }

    func connect(endpoint: String, secret: String, managementEndpoint: String) throws {
        guard !preview else { return }
        guard !busy, !managementBusy else { throw ControllerError.invalidConfig("请等待当前操作完成。") }
        let settings = try ControllerSettings(endpoint: endpoint, secret: secret)
        let managementSettings = try ControllerSettings(endpoint: managementEndpoint, secret: secret)
        generation += 1
        sessionID += 1
        controller = Controller(settings: settings)
        managementClient = ManagementClient(settings: managementSettings)
        self.managementEndpoint = managementSettings.endpoint.absoluteString
        management = nil
        managementStatus = "正在连接 ZFly 运行时…"
        self.endpoint = settings.endpoint.absoluteString
        snapshot = nil
        traffic = []
        previousTraffic = nil
        delays = [:]
        connected = false
        updated = nil
        status = "正在连接…"
        actionError = nil
    }

    func poll() async {
        guard !polling else { return }
        polling = true
        defer { polling = false }
        await startRuntime()
        while !Task.isCancelled {
            await refresh()
            await refreshManagement()
            do { try await Task.sleep(for: .seconds(2)) }
            catch { return }
        }
    }

    func refresh() async {
        guard !preview, !refreshing, !busy, !managementBusy, let controller else { return }
        refreshing = true
        let revision = generation
        let operation = coreRevision
        defer { refreshing = false }
        do {
            let next = try await controller.snapshot()
            guard revision == generation, operation == coreRevision, !Task.isCancelled else { return }
            snapshot = next
            recordTraffic(next)
            connected = true
            updateConnectedStatus()
            updated = Date()
        } catch {
            guard revision == generation, operation == coreRevision, !Task.isCancelled else { return }
            connected = false
            status = "连接失败：" + error.localizedDescription
        }
    }

    func refreshStatus() async {
        guard !preview, !refreshingStatus, !busy, !managementBusy else { return }
        refreshingStatus = true
        defer { refreshingStatus = false }
        await refresh()
        await refreshManagement()
    }

    private func updateConnectedStatus() {
        // A reachable management service does not mean the core snapshot succeeded.
        guard connected else { return }
        let next = management?.coreStatus ?? "核心已连接"
        if status != next { status = next }
    }

    private func recordTraffic(_ next: Snapshot) {
        let date = Date()
        let down = next.connections.downloadTotal ?? 0, up = next.connections.uploadTotal ?? 0
        if let previous = previousTraffic {
            let elapsed = date.timeIntervalSince(previous.date)
            if elapsed > 0 {
                traffic.append(TrafficSample(download: Double(max(0, down - previous.download)) / elapsed,
                                             upload: Double(max(0, up - previous.upload)) / elapsed))
                if traffic.count > 45 { traffic.removeFirst(traffic.count - 45) }
            }
        }
        previousTraffic = (date, down, up)
    }

    func toggleRunning() async {
        guard let state = management else { await startRuntime(); return }
        var command = ManagementCommand("running.set"); command.running = !state.running
        do { _ = try await perform(command); await refresh() }
        catch { actionError = error.localizedDescription }
    }

    func setSystemProxy(_ enabled: Bool) async {
        guard let state = management else { return }
        var command = ManagementCommand("network.save"); command.expected = state.network
        var network = state.network; network.systemProxy = enabled; command.network = network
        do { _ = try await perform(command); await refresh() }
        catch { actionError = error.localizedDescription }
    }

    func refreshManagement() async {
        guard !preview, !managementRefreshing, !busy, !managementBusy, let client = managementClient else { return }
        managementRefreshing = true
        let revision = generation
        let operation = managementRevision
        defer { managementRefreshing = false }
        do {
            let state = try await client.snapshot()
            guard revision == generation, operation == managementRevision, !Task.isCancelled else { return }
            management = state
            managementStatus = "ZFly 运行时已就绪"
            updateConnectedStatus()
        } catch {
            guard revision == generation, operation == managementRevision, !Task.isCancelled else { return }
            management = nil
            managementStatus = "ZFly 运行时未连接：" + error.localizedDescription
        }
    }

    func manage(_ command: ManagementCommand) async throws -> ManagedFile? {
        try await perform(command).file
    }

    func perform(_ command: ManagementCommand) async throws -> ManagementResult {
        guard !busy, !managementBusy else { throw ControllerError.invalidConfig("请等待当前管理操作完成。") }
        if preview {
            if command.action.hasPrefix("catalog."), let state = management {
                return try previewCatalog.perform(command, state: state)
            }
            let file: ManagedFile?
            let document: ManagedDocument?
            if command.action.hasPrefix("workspace.") {
                document = try workspacePreview(command)
                file = nil
            } else {
                file = try managePreview(command)
                document = nil
            }
            guard let state = management else { throw ControllerError.invalidResponse }
            return ManagementResult(state: state, file: file, document: document, message: "演示操作已完成。")
        }
        guard let client = managementClient, management != nil else {
            throw ControllerError.invalidConfig("ZFly 本地运行时尚未就绪，请重试启动。")
        }
        let revision = generation
        managementRevision += 1
        coreRevision += 1
        managementBusy = true
        defer { managementBusy = false }
        do {
            let result = try await client.perform(command)
            guard revision == generation else { throw CancellationError() }
            management = result.state
            updateConnectedStatus()
            return result
        } catch {
            if let state = try? await client.snapshot(), revision == generation { management = state }
            throw error
        }
    }

    private func managePreview(_ command: ManagementCommand) throws -> ManagedFile? {
        guard var state = management else { return nil }
        if command.action.hasPrefix("profile."), !["profile.add", "profile.import", "profile.create"].contains(command.action),
           !state.profiles.contains(where: { $0.id == command.id }) { throw ControllerError.invalidConfig("配置已不存在。") }
        let profile = state.profiles.first(where: { $0.id == command.id })
        try validatePreviewProfileCommand(command, profile: profile)
        if ["profile.edit", "profile.delete"].contains(command.action),
           let profile = state.profiles.first(where: { $0.id == command.id }), command.revision != profile.revision {
            throw ControllerError.invalidConfig("配置已被更新，请刷新列表后重试。")
        }
        switch command.action {
        case "network.save":
            guard command.expected == state.network else { throw ControllerError.invalidConfig("设置已修改，请重新载入。") }
            guard let network = command.network else { throw ControllerError.invalidConfig("网络设置无效。") }
            state.network = network
            snapshot?.configs.mode = state.network.mode
        case "running.set":
            guard let running = command.running else { throw ControllerError.invalidConfig("运行状态无效。") }
            state.running = running
        case "profile.select": state.currentProfileID = command.id
        case "profile.add", "profile.import", "profile.create":
            let id = UUID().uuidString
            var profile = ManagedProfile(id: id, label: command.label ?? "演示配置", type: command.action == "profile.create" ? "custom" : (command.action == "profile.add" ? "url" : "file"), url: command.url ?? "")
            profile.autoUpdate = command.autoUpdate ?? profile.autoUpdate
            profile.updateHours = command.updateHours ?? 24
            profile.revision = UUID().uuidString
            state.profiles.append(profile)
            if state.currentProfileID == nil { state.currentProfileID = id }
            if let data = command.data { previewFiles[id] = ManagedFile(name: "演示配置.yaml", data: data, revision: UUID().uuidString) }
        case "profile.delete":
            state.profiles.removeAll { $0.id == command.id }
            previewFiles.removeValue(forKey: command.id ?? "")
            if state.currentProfileID == command.id { state.currentProfileID = state.profiles.first?.id }
        case "profile.edit":
            if let index = state.profiles.firstIndex(where: { $0.id == command.id }) {
                if let label = command.label { state.profiles[index].label = label }
                if let url = command.url { state.profiles[index].url = url }
                if let autoUpdate = command.autoUpdate { state.profiles[index].autoUpdate = autoUpdate }
                if let hours = command.updateHours { state.profiles[index].updateHours = hours }
                state.profiles[index].revision = UUID().uuidString
            }
        case "profile.read":
            return previewFiles[command.id ?? ""] ?? ManagedFile(name: "演示配置.yaml", data: Data("mixed-port: 7890\nmode: rule\nproxies: []\nrules:\n  - MATCH,DIRECT\n".utf8).base64EncodedString(), revision: "preview")
        case "profile.write":
            let current = previewFiles[command.id ?? ""]?.revision ?? "preview"
            guard state.profiles.first(where: { $0.id == command.id })?.type == "file", command.revision == current else {
                throw ControllerError.invalidConfig("文件只读或已被修改，请重新打开。")
            }
            guard let data = command.data,
                  let decoded = Data(base64Encoded: data),
                  !decoded.isEmpty,
                  !decoded.contains(0),
                  String(data: decoded, encoding: .utf8) != nil else {
                throw ControllerError.invalidConfig("配置文件编码无效。")
            }
            previewFiles[command.id ?? ""] = ManagedFile(name: "演示配置.yaml", data: data, revision: UUID().uuidString)
        case "proxy.select": snapshot?.proxies.proxies[command.group ?? ""]?.now = command.proxy
        case "tool.run":
            guard ["apply", "restart", "gc", "closeConnections", "updateProfiles", "updateResources", "updateProvider", "backupLocal", "testDAV", "backupDAV", "restoreLocalProfiles", "restoreLocalAll", "restoreDAVProfiles", "restoreDAVAll"].contains(command.tool ?? "") else {
                throw ControllerError.invalidConfig("不支持的工具操作。")
            }
            if command.tool == "closeConnections" { snapshot?.connections.connections = [] }
        case "profile.update": break
        default: throw ControllerError.invalidConfig("不支持的管理操作。")
        }
        management = state
        return nil
    }

    private func validatePreviewProfileCommand(_ command: ManagementCommand, profile: ManagedProfile?) throws {
        func validURL(_ value: String) -> Bool {
            guard let components = URLComponents(string: value),
                  let scheme = components.scheme?.lowercased(),
                  ["http", "https"].contains(scheme),
                  components.host != nil,
                  components.user == nil,
                  components.password == nil else { return false }
            return true
        }
        switch command.action {
        case "profile.add", "profile.import", "profile.create":
            if let label = command.label, !label.isEmpty && label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                throw ControllerError.invalidConfig("配置名称不能为空。")
            }
            if command.action == "profile.add", !(command.url.map(validURL) ?? false) {
                throw ControllerError.invalidConfig("请输入有效的 HTTP / HTTPS 订阅地址。")
            }
            if let url = command.url, command.action != "profile.add", !url.isEmpty && url.contains(where: { $0.isNewline }) {
                throw ControllerError.invalidConfig("配置地址格式无效。")
            }
            if let autoUpdate = command.autoUpdate, autoUpdate != true && autoUpdate != false {
                throw ControllerError.invalidConfig("自动更新开关无效。")
            }
            if let hours = command.updateHours, !(1...8760).contains(hours) {
                throw ControllerError.invalidConfig("更新间隔应为 1–8760 小时。")
            }
            if command.action == "profile.import" {
                guard let encoded = command.data,
                      let data = Data(base64Encoded: encoded),
                      !data.isEmpty,
                      !data.contains(0),
                      String(data: data, encoding: .utf8) != nil else {
                    throw ControllerError.invalidConfig("配置文件编码无效。")
                }
            }
        case "profile.edit":
            if let label = command.label, label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                throw ControllerError.invalidConfig("配置名称不能为空。")
            }
            if let autoUpdate = command.autoUpdate, autoUpdate != true && autoUpdate != false {
                throw ControllerError.invalidConfig("自动更新开关无效。")
            }
            if let hours = command.updateHours, !(1...8760).contains(hours) {
                throw ControllerError.invalidConfig("更新间隔应为 1–8760 小时。")
            }
            if profile?.type == "url", !(command.url.map(validURL) ?? profile.map { validURL($0.url) } ?? false) {
                throw ControllerError.invalidConfig("请输入有效的 HTTP / HTTPS 订阅地址。")
            }
        default:
            break
        }
    }

    func select(group: String, proxy: String) async {
        if management != nil {
            var command = ManagementCommand("proxy.select"); command.group = group; command.proxy = proxy
            do { _ = try await manage(command); await refresh() }
            catch { actionError = error.localizedDescription }
            return
        }
        await act { try await $0.select(group: group, proxy: proxy) }
    }

    func setMode(_ mode: String) async throws {
        if let state = management {
            var command = ManagementCommand("network.save")
            command.expected = state.network
            var network = state.network; network.mode = mode; command.network = network
            _ = try await manage(command)
            await refresh()
        } else if var config = snapshot?.configs {
            let baseline = config; config.mode = mode
            try await configure(config, baseline: baseline)
        }
    }

    private func workspacePreview(_ command: ManagementCommand) throws -> ManagedDocument {
        guard ["workspace.read", "workspace.write"].contains(command.action) else { throw ControllerError.invalidConfig("不支持的管理操作。") }
        guard let section = WorkspaceSection(rawValue: command.section ?? "") else { throw ControllerError.invalidConfig("不支持的分类。") }
        if section.profileSpecific, !(management?.profiles.contains(where: { $0.id == command.id }) ?? false) { throw ControllerError.invalidConfig("请先选择配置。") }
        let key = section.rawValue + ":" + (section.profileSpecific ? command.id ?? "" : "")
        let initial = PreviewWorkspace.document(section)
        let current = previewDocuments[key] ?? initial
        guard command.action == "workspace.write" else { return current }
        guard current.writable, current.revision == command.revision else { throw ControllerError.invalidConfig("内容只读或已修改，请重新载入。") }
        let text = command.text ?? ""
        if current.format == "json" {
            do {
                guard try JSONSerialization.jsonObject(with: Data(text.utf8), options: []) is [String: Any] else {
                    throw ControllerError.invalidConfig("请输入有效的 JSON 对象。")
                }
            } catch let error as ControllerError {
                throw error
            } catch {
                throw ControllerError.invalidConfig("请输入有效的 JSON 对象。")
            }
        }
        let saved = ManagedDocument(section: section.rawValue, title: section.title, text: text, revision: UUID().uuidString, writable: current.writable, format: current.format)
        previewDocuments[key] = saved
        return saved
    }

    func measure(proxy: String) async {
        if preview { delays[proxy] = snapshot?.proxies.proxies[proxy]?.lastDelay ?? 42; return }
        let revision = generation
        await act { [weak self] controller in
            let delay = try await controller.delay(proxy: proxy)
            if self?.generation == revision { self?.delays[proxy] = delay }
        }
    }

    func close(connection: String) async {
        if preview { snapshot?.connections.connections?.removeAll { $0.id == connection }; return }
        await act { try await $0.close(connection: connection) }
    }

    func inspect(_ tool: String, name: String = "", type: String = "A") async throws -> String {
        guard !busy, !managementBusy else { throw ControllerError.invalidConfig("请等待当前操作完成。") }
        guard ["rules", "dnsQuery", "flushDNS", "flushFakeIP", "closeConnections"].contains(tool) else { throw ControllerError.invalidConfig("不支持的核心工具。") }
        if tool == "dnsQuery", name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !["A", "AAAA", "CNAME", "MX", "TXT", "NS", "HTTPS"].contains(type) {
            throw ControllerError.invalidConfig("请输入域名并选择查询类型。")
        }
        if preview {
            if tool == "closeConnections" { snapshot?.connections.connections = [] }
            let value: [String: Any]
            if tool == "rules" { value = ["rules": [["type": "Match", "payload": "", "proxy": "DIRECT"]]] }
            else if tool == "dnsQuery" { value = ["Answer": [["name": name, "type": type, "data": "192.0.2.1"]]] }
            else { return "演示操作已完成。" }
            return String(decoding: try JSONSerialization.data(withJSONObject: value, options: .prettyPrinted), as: UTF8.self)
        }
        guard connected, let controller else { throw ControllerError.invalidConfig("请先连接核心。") }
        busy = true
        coreRevision += 1
        defer { busy = false }
        return try await controller.inspect(tool, name: name, type: type)
    }

    func measureGroup(_ names: [String]) async {
        if preview { for name in names { delays[name] = snapshot?.proxies.proxies[name]?.lastDelay ?? 42 }; return }
        let revision = generation
        await act { [weak self] controller in
            let values = await withTaskGroup(of: (String, Int).self, returning: [String: Int].self) { group in
                var iterator = names.makeIterator()
                for _ in 0..<min(4, names.count) {
                    if let name = iterator.next() { group.addTask { (name, (try? await controller.delay(proxy: name)) ?? 0) } }
                }
                var result: [String: Int] = [:]
                for await (name, delay) in group {
                    result[name] = delay
                    if let next = iterator.next() { group.addTask { (next, (try? await controller.delay(proxy: next)) ?? 0) } }
                }
                return result
            }
            if self?.generation == revision { self?.delays.merge(values) { _, new in new } }
        }
    }

    func configure(_ draft: CoreConfig, baseline: CoreConfig) async throws {
        let patch = draft.changes(from: baseline)
        guard !patch.isEmpty else { return }
        guard !busy, !managementBusy, connected else { throw ControllerError.invalidConfig("请等待当前操作完成并连接核心。") }
        if preview {
            try draft.validate()
            snapshot?.configs = draft
            return
        }
        guard let controller else { return }
        busy = true
        coreRevision += 1
        let revision = generation
        defer { busy = false }
        let current = try await controller.configuration()
        guard revision == generation else { throw CancellationError() }
        guard !patch.conflicts(with: current, baseline: baseline) else {
            throw ControllerError.invalidConfig("正在编辑的设置已被其他界面修改。请重新载入核心设置后再保存。")
        }
        try current.merging(patch).validate()
        do { try await controller.configure(patch) }
        catch {
            if let latest = try? await controller.snapshot(), revision == generation { snapshot = latest }
            throw error
        }
        let latest = try await controller.snapshot()
        guard revision == generation else { throw CancellationError() }
        snapshot = latest
        updated = Date()
        guard patch.matches(latest.configs) else {
            throw ControllerError.invalidConfig("核心未应用全部设置，请重新载入检查端口占用。")
        }
    }

    private func act(_ operation: @MainActor (Controller) async throws -> Void) async {
        guard !busy, !managementBusy, connected, let controller else { return }
        let revision = generation
        busy = true
        coreRevision += 1
        actionError = nil
        do { try await operation(controller) }
        catch {
            if revision == generation { actionError = error.localizedDescription }
        }
        busy = false
        if revision == generation { await refresh() }
    }
}

func bytes(_ value: Int64?) -> String {
    guard let value, value != 0 else { return "0 KB" }
    return ByteCountFormatter.string(fromByteCount: value, countStyle: .binary)
}
