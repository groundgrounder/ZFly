import SwiftUI
import ZFlyKit

struct ProxiesView: View {
    @Environment(Store.self) private var store
    @State private var search = PreviewOptions.value("--preview-query") ?? ""

    private var names: [String] { store.snapshot?.proxies.groupNames(mode: store.snapshot?.configs.mode ?? "rule") ?? [] }
    private var filteredGroups: [String] {
        names.filter { name in
            search.isEmpty || name.localizedCaseInsensitiveContains(search)
                || (store.snapshot?.proxies.proxies[name]?.all ?? []).contains { $0.localizedCaseInsensitiveContains(search) }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
            PageHeader("代理", subtitle: "选择出口节点，查看连接延迟。")
            SearchField(prompt: "搜索节点或代理组", text: $search)
            if names.isEmpty {
                ContentUnavailableView("暂无代理组", systemImage: "network", description: Text("连接核心并加载含代理组的配置后显示。"))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if filteredGroups.isEmpty {
                ContentUnavailableView {
                    Label("没有匹配的节点", systemImage: "magnifyingglass")
                } description: { Text("试试其他名称，或清除搜索条件。") } actions: {
                    Button("清除搜索") { search = "" }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 26) {
                        ForEach(filteredGroups, id: \.self) { name in
                            if let group = store.snapshot?.proxies.proxies[name] { groupSection(name, group: group) }
                        }
                    }.padding(.bottom, 4)
                }
            }
        }.padding(ZFlyMetrics.pagePadding)
    }

    private func groupSection(_ name: String, group: ProxyInfo) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(name).font(.system(size: 14, weight: .semibold)).textSelection(.enabled)
                    HStack(spacing: 6) {
                        Text(group.type)
                        Text("·")
                        Text(group.type == "Selector" ? "手动选择" : "自动管理")
                    }.font(.system(size: 10)).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if store.measuringGroups.contains(name) {
                    ProgressView().controlSize(.small)
                    Button("取消测速") { store.cancelMeasurement(group: name) }
                } else {
                    Button("整组测速", systemImage: "bolt") { Task { await store.measureGroup(group.all ?? [], name: name) } }
                        .disabled(!store.connected)
                }
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 12)], spacing: 12) {
                ForEach((group.all ?? []).filter { search.isEmpty || name.localizedCaseInsensitiveContains(search) || $0.localizedCaseInsensitiveContains(search) }, id: \.self) { proxy in
                    ProxyCard(name: proxy, groupName: name, group: group)
                }
            }
        }
    }
}

private struct ProxyCard: View {
    @Environment(Store.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let name: String
    let groupName: String
    let group: ProxyInfo
    private var selected: Bool { group.now == name }
    private var delay: Int? { store.delays[name] ?? store.snapshot?.proxies.proxies[name]?.lastDelay }
    private var delayColor: Color {
        guard let delay else { return .secondary }
        return delay <= 0 ? .red : (delay < 150 ? ZFlyPalette.tint : (delay < 400 ? .orange : .red))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if group.type == "Selector" && !selected {
                Button { Task { await store.select(group: groupName, proxy: name) } } label: { identity }
                    .buttonStyle(.plain).disabled(store.isBusy || !store.connected)
                    .accessibilityLabel(Text(name))
                    .accessibilityValue(Text(selected ? "已选" : "选择节点"))
                    .accessibilityAddTraits(selected ? .isSelected : [])
            } else { identity }
            HStack {
                HStack(spacing: 5) {
                    Image(systemName: "waveform.path").font(.system(size: 10))
                    if store.measuring.contains(name) { ProgressView().controlSize(.mini) }
                    else if let delay {
                        if delay > 0 { Text("\(delay) ms") }
                        else { Text("不可用") }
                    } else { Text("未测速") }
                }.font(.system(size: 11, weight: .medium)).monospacedDigit().foregroundStyle(delayColor)
                Spacer()
                Button(store.measuring.contains(name) ? "取消" : "测速") {
                    if store.measuring.contains(name) { store.cancelMeasurement(proxy: name) }
                    else { Task { await store.measure(proxy: name) } }
                }
                    .buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(.secondary)
                    .disabled(!store.connected).help("测速")
            }
        }
        .padding(ZFlyMetrics.panelPadding).modifier(SurfacePanel(selected: selected))
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: selected)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var identity: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 6) {
                Text(name).font(.system(size: 13, weight: .semibold)).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text(store.snapshot?.proxies.proxies[name]?.type ?? "Proxy")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                .font(.system(size: 15)).foregroundStyle(selected ? ZFlyPalette.tint : Color.primary.opacity(0.15))
        }.frame(maxWidth: .infinity, minHeight: 36, alignment: .topLeading).contentShape(Rectangle())
    }
}

struct ConnectionsView: View {
    @Environment(Store.self) private var store
    let active: Bool
    @State private var search = PreviewOptions.value("--preview-query") ?? ""
    @State private var current: ConnectionsSnapshot?
    private var all: [ConnectionInfo] { current?.items ?? [] }
    private var connections: [ConnectionInfo] {
        all.filter { search.isEmpty || $0.destination.localizedCaseInsensitiveContains(search)
            || ($0.metadata?.process ?? "").localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        let visibleConnections = connections
        let lastID = visibleConnections.last?.id
        VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
            PageHeader("连接", subtitle: "查看实时请求、匹配规则与流量。") {
                StatusBadge(title: "\(all.count) 条活动连接", color: ZFlyPalette.tint)
            }
            SearchField(prompt: "搜索目标或进程", text: $search)
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Text("目标 / 路由").frame(maxWidth: .infinity, alignment: .leading).padding(.leading, 42)
                    Text("下载").frame(width: 82, alignment: .trailing)
                    Text("上传").frame(width: 82, alignment: .trailing)
                    Color.clear.frame(width: 32, height: 1)
                }
                .font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                .padding(.horizontal, ZFlyMetrics.panelPadding).padding(.vertical, 12)
                Divider().opacity(0.5)
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(visibleConnections) { connection in
                            ConnectionRow(connection: connection).padding(.horizontal, ZFlyMetrics.panelPadding)
                            if connection.id != lastID {
                                Divider().opacity(0.5).padding(.horizontal, ZFlyMetrics.panelPadding)
                            }
                        }
                    }
                }
                .overlay {
                    if visibleConnections.isEmpty {
                        ContentUnavailableView(search.isEmpty ? "暂无连接" : "没有匹配的连接", systemImage: search.isEmpty ? "link" : "magnifyingglass",
                                               description: Text(search.isEmpty ? "核心的活动连接会显示在这里。" : "试试其他名称，或清除搜索条件。"))
                    }
                }
            }.modifier(SurfacePanel())
        }.padding(ZFlyMetrics.pagePadding)
        .onChange(of: active ? store.snapshot?.connections : nil, initial: true) { _, value in
            // Preserve the hidden page's rows and scroll position without subscribing to live traffic.
            if active { current = value }
        }
    }
}

private struct ConnectionRow: View {
    @Environment(Store.self) private var store
    let connection: ConnectionInfo

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "arrow.up.arrow.down").font(.system(size: 12)).foregroundStyle(ZFlyPalette.tint)
                .frame(width: 30, height: 30).background(ZFlyPalette.tint.opacity(0.07), in: RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 5) {
                Text(connection.destination).font(.system(size: 12, weight: .semibold)).textSelection(.enabled)
                    .lineLimit(1).truncationMode(.middle)
                Text([connection.metadata?.process, connection.metadata?.network?.uppercased(), connection.rule]
                    .compactMap { $0 }.joined(separator: " · "))
                    .font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
                Text((connection.chains ?? []).joined(separator: " ← "))
                    .font(.system(size: 10)).foregroundStyle(.tertiary).textSelection(.enabled).lineLimit(1)
            }.frame(maxWidth: .infinity, alignment: .leading)
            Text(bytes(connection.download)).frame(width: 82, alignment: .trailing)
            Text(bytes(connection.upload)).frame(width: 82, alignment: .trailing)
            Button("关闭", systemImage: "xmark") { Task { await store.close(connection: connection.id) } }
                .labelStyle(.iconOnly).buttonStyle(.plain).foregroundStyle(.secondary).frame(width: 32, height: 28)
                .help("关闭这条连接").disabled(store.isBusy || !store.connected)
        }.font(.system(size: 11)).monospacedDigit().padding(.vertical, 10)
    }
}
