import Foundation
import Observation
import SwiftUI
import ZFlyKit

struct TrafficSample: Equatable, Identifiable {
    let id = UUID()
    let download: Double
    let upload: Double
}

@MainActor @Observable
final class Store {
    let preview: Bool
    private(set) var snapshot: CoreState?
    private(set) var busy = false
    private(set) var refreshingStatus = false
    private(set) var connected = false
    private(set) var status = "正在启动 ZFly…"
    private(set) var endpoint = "ZFly 内置核心"
    private(set) var dataDirectory = ""
    private(set) var updated: Date?
    private(set) var delays: [String: Int] = [:]
    private(set) var measuring: Set<String> = []
    private(set) var measuringGroups: Set<String> = []
    @ObservationIgnored private var delayTasks: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var groupTasks: [String: Task<Void, Never>] = [:]
    var actionError: String?
    private(set) var management: ManagedState?
    private(set) var managementBusy = false
    private(set) var managementStatus = "正在启动本地运行时…"
    private(set) var managementStale = false
    private(set) var managementEndpoint = ""
    private(set) var sessionID = 0
    private(set) var traffic: [TrafficSample] = []
    private(set) var starting = false
    @ObservationIgnored private var previousTraffic: (date: Date, download: Int64, upload: Int64)?
    var downloadRate: Int64 { Int64(traffic.last?.download ?? 0) }
    var uploadRate: Int64 { Int64(traffic.last?.upload ?? 0) }
    @ObservationIgnored private var controller: Controller?
    @ObservationIgnored private var managementClient: ManagementClient?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var coreRevision = 0
    @ObservationIgnored private var managementRevision = 0
    @ObservationIgnored private var managementRefreshing = false
    @ObservationIgnored private var refreshing = false
    @ObservationIgnored private var polling = false
    @ObservationIgnored private var pollingTask: Task<Void, Never>?

    var isBusy: Bool { starting || busy || managementBusy }

    func startPolling() {
        guard pollingTask == nil else { return }
        pollingTask = Task { await poll() }
    }

    func stopPolling() {
        for task in delayTasks.values { task.cancel() }
        for task in groupTasks.values { task.cancel() }
        generation += 1
        pollingTask?.cancel()
        pollingTask = nil
    }

    init(preview: Bool) {
        self.preview = preview
    }

    func startRuntime() async {
        guard !starting, !busy, !managementBusy else { return }
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
            let connection = try await NativeRuntime.shared.start(preview: preview, previewState: PreviewOptions.value("--preview-state") ?? "default")
            try Task.checkCancellation()
            dataDirectory = connection.dataDirectory
            try connect(endpoint: connection.endpoint, secret: connection.secret, managementEndpoint: connection.endpoint)
            await refreshManagement()
            await refresh()
        } catch {
            guard !Task.isCancelled else { return }
            status = error.localizedDescription
            managementStatus = status
        }
    }

    func connect(endpoint: String, secret: String, managementEndpoint: String, transport: (any HTTPTransport)? = nil) throws {
        guard !busy, !managementBusy else { throw ControllerError.invalidConfig("请等待当前操作完成。") }
        let settings = try ControllerSettings(endpoint: endpoint, secret: secret)
        let managementSettings = try ControllerSettings(endpoint: managementEndpoint, secret: secret)
        generation += 1
        sessionID += 1
        controller = Controller(settings: settings, transport: transport ?? SessionTransport())
        managementClient = ManagementClient(settings: managementSettings, transport: transport ?? SessionTransport(resourceTimeout: 300))
        self.managementEndpoint = managementSettings.endpoint.absoluteString
        managementStale = management != nil
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
        var cycle = snapshot == nil ? 0 : 1
        while !Task.isCancelled {
            // Sample live counters every two seconds; reload stable metadata every ten seconds.
            // Explicit refreshes and completed actions still request a full snapshot immediately.
            let full = cycle == 0 || snapshot == nil
            await refresh(full: full)
            if full { await refreshManagement() }
            cycle = (cycle + 1) % 5
            do { try await Task.sleep(for: .seconds(2)) }
            catch { return }
        }
    }

    func refresh(full: Bool = true) async {
        // An action can invalidate an in-flight poll. Wait for it, then fetch a
        // complete post-action snapshot instead of dropping the refresh request.
        while refreshing {
            do { try await Task.sleep(for: .milliseconds(10)) } catch { return }
        }
        guard !busy, !managementBusy, let controller else { return }
        refreshing = true
        let revision = generation
        let operation = coreRevision
        defer { refreshing = false }
        do {
            if full || snapshot == nil {
                let next = try await controller.snapshot()
                guard revision == generation, operation == coreRevision, !Task.isCancelled else { return }
                applySnapshot(next)
                recordTraffic(next.connections)
            } else {
                let next = try await controller.connections()
                guard revision == generation, operation == coreRevision, !Task.isCancelled else { return }
                snapshot?.applyConnections(next)
                recordTraffic(next)
            }
            if !connected { connected = true }
            updateConnectedStatus()
            updated = Date()
        } catch {
            guard revision == generation, operation == coreRevision, !Task.isCancelled else { return }
            connected = false
            status = "连接失败：" + error.localizedDescription
        }
    }

    private func applySnapshot(_ next: Snapshot) {
        if let snapshot { snapshot.apply(next) }
        else { snapshot = CoreState(next) }
    }

    func refreshStatus() async {
        guard !refreshingStatus, !busy, !managementBusy else { return }
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

    private func recordTraffic(_ next: ConnectionsSnapshot) {
        let date = Date()
        let down = next.downloadTotal ?? 0, up = next.uploadTotal ?? 0
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
        guard !managementRefreshing, !busy, !managementBusy, let client = managementClient else { return }
        managementRefreshing = true
        let revision = generation
        let operation = managementRevision
        defer { managementRefreshing = false }
        do {
            let state = try await client.snapshot()
            guard revision == generation, operation == managementRevision, !Task.isCancelled else { return }
            if management != state { management = state }
            managementStale = false
            if managementStatus != "ZFly 运行时已就绪" { managementStatus = "ZFly 运行时已就绪" }
            updateConnectedStatus()
        } catch {
            guard revision == generation, operation == managementRevision, !Task.isCancelled else { return }
            managementStale = management != nil
            managementStatus = "状态可能过期，请重试：" + error.localizedDescription
        }
    }

    func manage(_ command: ManagementCommand) async throws -> ManagedFile? {
        try await perform(command).file
    }

    func perform(_ command: ManagementCommand) async throws -> ManagementResult {
        guard !busy, !managementBusy else { throw ControllerError.invalidConfig("请等待当前管理操作完成。") }
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
            managementStale = false
            updateConnectedStatus()
            return result
        } catch {
            if let state = try? await client.snapshot(), revision == generation { management = state }
            throw error
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

    func unfix(group: String) async {
        if management != nil {
            var command = ManagementCommand("proxy.unfix"); command.group = group
            do { _ = try await manage(command); await refresh() }
            catch { actionError = error.localizedDescription }
            return
        }
        await act { try await $0.unfix(group: group) }
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

    func measure(proxy: String) async {
        guard !measuring.contains(proxy), !Task.isCancelled else { return }
        guard let controller else { return }
        let revision = generation
        measuring.insert(proxy)
        let task = Task {
            defer { measuring.remove(proxy); delayTasks.removeValue(forKey: proxy) }
            do {
                let delay = try await controller.delay(proxy: proxy)
                if generation == revision, !Task.isCancelled { delays[proxy] = delay }
            } catch {
                if generation == revision, !Task.isCancelled { delays[proxy] = 0 }
            }
        }
        delayTasks[proxy] = task
        await withTaskCancellationHandler { await task.value } onCancel: { task.cancel() }
    }

    func cancelMeasurement(proxy: String) { delayTasks[proxy]?.cancel() }
    func cancelMeasurement(group: String) { groupTasks[group]?.cancel() }

    func close(connection: String) async {
        await act { try await $0.close(connection: connection) }
    }

    func inspect(_ tool: String, name: String = "", type: String = "A") async throws -> String {
        guard !busy, !managementBusy else { throw ControllerError.invalidConfig("请等待当前操作完成。") }
        guard ["rules", "dnsQuery", "flushDNS", "flushFakeIP", "closeConnections"].contains(tool) else { throw ControllerError.invalidConfig("不支持的核心工具。") }
        if tool == "dnsQuery", name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !["A", "AAAA", "CNAME", "MX", "TXT", "NS", "HTTPS"].contains(type) {
            throw ControllerError.invalidConfig("请输入域名并选择查询类型。")
        }
        guard connected, let controller else { throw ControllerError.invalidConfig("请先连接核心。") }
        busy = true
        coreRevision += 1
        defer { busy = false }
        return try await controller.inspect(tool, name: name, type: type)
    }

    func measureGroup(_ names: [String], name: String = "group") async {
        guard !measuringGroups.contains(name) else { return }
        measuringGroups.insert(name)
        let task = Task {
            defer { measuringGroups.remove(name); groupTasks.removeValue(forKey: name) }
            await withTaskGroup(of: Void.self) { group in
                var iterator = Array(Set(names)).makeIterator()
                for _ in 0..<min(4, names.count) {
                    if let proxy = iterator.next() { group.addTask { await self.measure(proxy: proxy) } }
                }
                for await _ in group {
                    if Task.isCancelled { group.cancelAll(); break }
                    if let proxy = iterator.next() { group.addTask { await self.measure(proxy: proxy) } }
                }
            }
        }
        groupTasks[name] = task
        await task.value
    }

    func configure(_ draft: CoreConfig, baseline: CoreConfig) async throws {
        let patch = draft.changes(from: baseline)
        guard !patch.isEmpty else { return }
        guard !busy, !managementBusy, connected else { throw ControllerError.invalidConfig("请等待当前操作完成并连接核心。") }
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
            if let latest = try? await controller.snapshot(), revision == generation { applySnapshot(latest) }
            throw error
        }
        let latest = try await controller.snapshot()
        guard revision == generation else { throw CancellationError() }
        applySnapshot(latest)
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
