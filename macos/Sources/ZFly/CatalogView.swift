import SwiftUI
import ZFlyKit

private struct CatalogSelection: Identifiable {
    let id = UUID()
    let command: ManagementCommand
    let document: ManagedDocument
}

struct CatalogView: View {
    @Environment(Store.self) private var store
    var active = true
    @State private var kind: CatalogKind = .proxies
    @State private var profileID = ""
    @State private var cache: [String: [CatalogItem]] = [:]
    @State private var editing: CatalogSelection?
    @State private var message = ""
    @State private var loading = false
    @State private var pending = false
    @State private var query = ""

    private var scope: String { kind.rawValue + ":" + (kind == .groups ? profileID : "") }
    private var profiles: [ManagedProfile] { (store.management?.profiles ?? []).filter { $0.type == "custom" } }
    private var unavailable: Bool { store.management == nil || (kind == .groups && profileID.isEmpty) }
    private var busy: Bool { loading || store.isBusy }
    private var items: [CatalogItem] { (cache[scope] ?? []).filter { query.isEmpty || ($0.label + " " + $0.detail).localizedCaseInsensitiveContains(query) } }

    var body: some View {
        VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
            PageHeader("资源", subtitle: "管理自定义节点与代理组。")
            HStack {
                SectionTabs(title: "分类", selection: $kind, items: [
                    SectionTab(value: .proxies, title: "自定义节点", icon: "network"),
                    SectionTab(value: .groups, title: "自定义代理组", icon: "square.stack.3d.up")
                ])
                if kind == .groups {
                    Picker("配置", selection: $profileID) {
                        Text("选择自定义配置").tag("")
                        ForEach(profiles) { Text($0.label).tag($0.id) }
                    }
                }
                Spacer()
            }
            HStack {
                SearchField(prompt: "搜索名称或类型", text: $query)
                Button("刷新", systemImage: "arrow.clockwise") { load() }.labelStyle(.iconOnly).help("刷新").disabled(busy || unavailable)
                Button("新建", systemImage: "plus") { open(nil) }.modifier(ActionStyle(prominent: true)).disabled(busy || unavailable)
            }
            if unavailable {
                ContentUnavailableView(store.management == nil ? "等待本地运行时就绪" : "请先创建或选择自定义配置", systemImage: "doc.badge.plus")
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(items) { item in
                            HStack(spacing: 12) {
                                Image(systemName: kind == .groups ? "square.stack.3d.up" : "network")
                                    .foregroundStyle(ZFlyPalette.tint).frame(width: 36, height: 36)
                                    .background(ZFlyPalette.tint.opacity(0.07), in: RoundedRectangle(cornerRadius: 9))
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(item.label).font(.headline)
                                    Text(item.detail).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button("编辑") { open(item.id) }.disabled(busy)
                            }.padding(.horizontal, ZFlyMetrics.panelPadding).padding(.vertical, 16)
                            if item.id != items.last?.id {
                                Divider().opacity(0.5).padding(.horizontal, ZFlyMetrics.panelPadding)
                            }
                        }
                    }
                }.modifier(SurfacePanel())
                    .overlay {
                        if items.isEmpty && !busy {
                            ContentUnavailableView(query.isEmpty ? "暂无资源" : "没有匹配的资源", systemImage: query.isEmpty ? "square.stack.3d.up" : "magnifyingglass",
                                                   description: Text(query.isEmpty ? (kind == .groups ? "为当前配置创建代理组。" : "新建节点，供自定义配置使用。") : "试试其他名称，或清除搜索条件。"))
                        }
                    }
            }
            if busy { ProgressView().controlSize(.small) }
            if !message.isEmpty { Text(verbatim: message).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
        }.padding(ZFlyMetrics.pagePadding)
            .task { chooseProfile(); if active { load() } }
            .onChange(of: scope) { _, _ in message = ""; query = ""; if cache[scope] == nil { load() } }
            .onChange(of: active) { _, value in if value && cache[scope] == nil { load() } }
            .onChange(of: store.management != nil) { _, available in if available { chooseProfile(); load() } }
            .onChange(of: profiles.map(\.id)) { _, _ in chooseProfile() }
            .onChange(of: store.isBusy || loading) { _, value in if !value && pending { pending = false; load() } }
            .sheet(item: $editing) { selection in
                CatalogEditor(selection: selection) { cache.removeAll(); load() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .zflyPrepareToQuit)) { _ in editing = nil }
    }

    private func chooseProfile() {
        guard store.management != nil else { return }
        if !profiles.contains(where: { $0.id == profileID }) { profileID = profiles.first(where: { $0.id == store.management?.currentProfileID })?.id ?? profiles.first?.id ?? "" }
    }

    private func command(_ action: String, id: String? = nil) -> ManagementCommand {
        var value = ManagementCommand(action); value.section = kind.rawValue; value.id = id
        if kind == .groups { value.name = profileID }
        return value
    }

    private func load() {
        guard active, !unavailable else { return }
        if busy { pending = true; return }
        let request = command("catalog.list"), target = scope
        loading = true
        Task {
            defer { loading = false }
            do {
                let result = try await store.perform(request)
                guard let items = result.items else { throw ControllerError.invalidResponse }
                cache[target] = items
                if target == scope { message = "" }
            } catch { if target == scope { message = error.localizedDescription } }
        }
    }

    private func open(_ id: String?) {
        let request = command("catalog.read", id: id)
        loading = true
        Task {
            defer { loading = false }
            do {
                guard let document = try await store.perform(request).document else { throw ControllerError.invalidResponse }
                editing = CatalogSelection(command: request, document: document)
            } catch { message = error.localizedDescription }
        }
    }
}

private struct CatalogEditor: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let selection: CatalogSelection
    let changed: () -> Void
    @State private var text: String
    @State private var revision: String
    @State private var message = ""
    @State private var working = false
    @State private var deleting = false
    @State private var discarding = false

    init(selection: CatalogSelection, changed: @escaping () -> Void) {
        self.selection = selection; self.changed = changed
        _text = State(initialValue: selection.document.text)
        _revision = State(initialValue: selection.document.revision)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(verbatim: selection.document.title).font(.title2.bold())
            Text("使用 JSON 编辑节点或代理组。保存前会检查版本；字段需符合核心配置格式。")
                .font(.callout).foregroundStyle(.secondary)
            TextEditor(text: $text).font(.system(.body, design: .monospaced)).disabled(working)
            if !message.isEmpty { Text(verbatim: message).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                Button("重新载入") {
                    working = true
                    Task {
                        defer { working = false }
                        do {
                            guard let document = try await store.perform(selection.command).document else { throw ControllerError.invalidResponse }
                            text = document.text; revision = document.revision; message = ""
                        } catch { message = error.localizedDescription }
                    }
                }
                if selection.command.id != nil { Button("删除", role: .destructive) { deleting = true } }
                Spacer()
                Button("取消") { if text != selection.document.text { discarding = true } else { dismiss() } }
                Button("保存并应用") { send("catalog.save") }.modifier(ActionStyle(prominent: true))
            }.disabled(working)
        }.padding(24).frame(width: 700, height: 580).interactiveDismissDisabled(text != selection.document.text || working)
            .confirmationDialog("删除此资源？使用此节点或代理组的引用可能失效。", isPresented: $deleting) {
                Button("删除", role: .destructive) { send("catalog.delete") }
                Button("取消", role: .cancel) {}
            }
            .confirmationDialog("丢弃未保存的编辑？", isPresented: $discarding) {
                Button("丢弃", role: .destructive) { dismiss() }
                Button("继续编辑", role: .cancel) {}
            }
    }

    private func send(_ action: String) {
        var request = selection.command; request.action = action; request.revision = revision; request.text = text
        working = true
        Task {
            defer { working = false }
            do { _ = try await store.perform(request); dismiss(); changed() }
            catch { message = error.localizedDescription }
        }
    }
}
