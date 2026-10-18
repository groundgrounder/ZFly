import AppKit
import SwiftUI
import ZFlyKit

@main
struct ZFlyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = Store(preview: CommandLine.arguments.contains("--safe-mode"))
    @AppStorage("appearance.scheme") private var scheme = "system"
    @AppStorage("appearance.language") private var language = AppLanguage.system.rawValue

    var body: some Scene {
        Window("ZFly", id: "main") {
            MainView().environmentObject(store)
                .environment(\.locale, AppLanguage(rawValue: language)?.locale ?? .current)
                .frame(minWidth: 900, minHeight: 640)
                .tint(ZFlyPalette.tint)
                .onChange(of: scheme, initial: true) { _, value in
                    AppAppearance.apply(value, to: NSApplication.shared)
                }
                .onAppear { store.startPolling() }
        }
        .defaultSize(width: 1100, height: 760)
        .windowToolbarStyle(.unifiedCompact)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu("代理") {
                Button(store.management?.running == true ? "停止代理" : "启动代理") { Task { await store.toggleRunning() } }
                    .keyboardShortcut("p", modifiers: [.command, .shift])
                    .disabled(store.isBusy || store.management?.currentProfileID == nil)
                Button("刷新状态") { Task { await store.refreshStatus() } }.keyboardShortcut("r")
            }
        }
        MenuBarExtra("ZFly", systemImage: store.management?.running == true ? "paperplane.fill" : "paperplane") {
            StatusMenu().environmentObject(store)
                .environment(\.locale, AppLanguage(rawValue: language)?.locale ?? .current)
        }
        Settings {
            AppSettingsView().environmentObject(store)
                .environment(\.locale, AppLanguage(rawValue: language)?.locale ?? .current)
                .id(store.sessionID)
                .frame(width: 720, height: 720)
                .tint(ZFlyPalette.tint)
                .onChange(of: scheme, initial: true) { _, value in
                    AppAppearance.apply(value, to: NSApplication.shared)
                }
        }
    }
}

private enum Page: String, CaseIterable, Identifiable {
    case overview = "概览", profiles = "订阅", proxies = "代理", connections = "连接"
    case catalog = "资源", workspace = "规则", tools = "工具", settings = "设置"
    var id: String { rawValue }
    var title: LocalizedStringKey { LocalizedStringKey(rawValue) }
    var icon: String {
        switch self {
        case .overview: "square.grid.2x2"
        case .profiles: "doc.text"
        case .proxies: "point.3.connected.trianglepath.dotted"
        case .connections: "arrow.left.arrow.right"
        case .catalog: "square.stack.3d.up"
        case .workspace: "line.3.horizontal.decrease"
        case .tools: "wrench.and.screwdriver"
        case .settings: "gearshape"
        }
    }

    static var initial: Self {
        guard CommandLine.arguments.contains("--safe-mode"),
              let index = CommandLine.arguments.firstIndex(of: "--preview-page"),
              CommandLine.arguments.indices.contains(index + 1) else { return .overview }
        return Self(rawValue: CommandLine.arguments[index + 1]) ?? .overview
    }
}

private struct SidebarButton: View {
    let page: Page
    let selected: Bool
    let action: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                Image(systemName: page.icon).font(.system(size: 15, weight: .medium)).frame(width: 21)
                    .foregroundStyle(selected ? ZFlyPalette.tint : Color.secondary)
                Text(page.title).font(.system(size: 13, weight: selected ? .semibold : .medium))
                    .lineLimit(1).minimumScaleFactor(0.85)
                Spacer(minLength: 0)
                if selected { Capsule().fill(ZFlyPalette.tint).frame(width: 3, height: 14) }
            }
            .padding(.horizontal, 11).frame(height: 38)
            .contentShape(RoundedRectangle(cornerRadius: ZFlyMetrics.controlRadius))
            .background {
                RoundedRectangle(cornerRadius: ZFlyMetrics.controlRadius)
                    .fill(selected ? ZFlyPalette.tint.opacity(0.10) : Color.primary.opacity(hovering ? 0.04 : 0))
            }
        }
        .buttonStyle(.plain).foregroundStyle(selected ? Color.primary : Color.primary.opacity(0.75))
        .onHover { hovering = $0 }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: hovering)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: selected)
        .help(page.title).accessibilityLabel(page.title)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

private struct MainView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var page = Page.initial
    @State private var visited: Set<Page> = [Page.initial]
    @State private var controllerSettings = false
    @State private var sidebar = true

    private var profile: ManagedProfile? {
        store.management?.profiles.first { $0.id == store.management?.currentProfileID }
    }

    var body: some View {
        HStack(spacing: 0) {
            if sidebar {
                sidebarContent.frame(width: 190).modifier(SidebarSurface())
                Color.primary.opacity(0.08).frame(width: 1)
            }
            VStack(spacing: 0) {
                if !store.connected && !store.preview {
                    HStack(spacing: 10) {
                        Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
                        Text(LocalizedStringKey(store.status)).lineLimit(2)
                        Spacer(minLength: 8)
                        Button("重试启动") { Task { await store.startRuntime() } }.disabled(store.isBusy)
                    }
                    .font(.callout).padding(.horizontal, 24).padding(.vertical, 10)
                    .background(Color.orange.opacity(0.06))
                }
                ZStack(alignment: .topLeading) {
                    // Keep visited pages alive so drafts, filters and scroll positions survive navigation.
                    ForEach(Page.allCases.filter { visited.contains($0) }) { item in
                        content(for: item)
                            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                            .opacity(page == item ? 1 : 0)
                            .offset(x: page == item ? 0 : (pageIndex(item) < pageIndex(page) ? -18 : 18))
                            .disabled(page != item).allowsHitTesting(page == item)
                            .accessibilityHidden(page != item)
                            .zIndex(page == item ? 1 : 0)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .animation(reduceMotion ? nil : .easeInOut(duration: 0.22), value: page)
                .id(store.sessionID)
                Divider().opacity(0.5)
                statusBar
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background { WindowBackdrop() }
        }
        .onChange(of: page) { _, value in visited.insert(value) }
        .navigationTitle("ZFly")
        .buttonStyle(ZFlyButtonStyle())
        .toolbarBackground(.hidden, for: .windowToolbar)
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button("侧边栏", systemImage: "sidebar.left") {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { sidebar.toggle() }
                }.help("显示或隐藏侧边栏")
            }
        }
        .sheet(isPresented: $controllerSettings) { ControllerSheet() }
        .alert("操作未完成", isPresented: Binding(
            get: { store.actionError != nil },
            set: { if !$0 { store.actionError = nil } }
        )) {
            Button("好") { store.actionError = nil }
        } message: { Text(LocalizedStringKey(store.actionError ?? "")) }
    }

    private var sidebarContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(nsImage: NSApplication.shared.applicationIconImage)
                    .resizable().scaledToFit().frame(width: 32, height: 32)
                VStack(alignment: .leading, spacing: 3) {
                    Text("ZFly").font(.system(size: 18, weight: .semibold, design: .rounded))
                    Text("简单连接，自在掌控").font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }.padding(.horizontal, 9).padding(.top, 24).padding(.bottom, 28)
            sidebarGroup("工作区", pages: [.overview, .profiles, .proxies, .connections])
            sidebarGroup("管理", pages: [.catalog, .workspace, .tools]).padding(.top, 22)
            Spacer(minLength: 24)
            SidebarButton(page: .settings, selected: page == .settings) { page = .settings }
            Divider().padding(.vertical, 14).padding(.horizontal, 8)
            Button { page = .profiles } label: {
                HStack(spacing: 9) {
                    Image(systemName: "doc.text").font(.system(size: 16)).foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("当前配置").font(.system(size: 10)).foregroundStyle(.secondary)
                        Group {
                            if let profile { Text(profile.label) }
                            else { Text("未选择配置") }
                        }.font(.system(size: 11, weight: .medium)).lineLimit(1)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.up.chevron.down").font(.system(size: 9)).foregroundStyle(.tertiary)
                }.padding(.horizontal, 10).padding(.bottom, 18).contentShape(Rectangle())
            }.buttonStyle(.plain).help("切换配置")
        }
        .padding(.horizontal, 12)
    }

    private func sidebarGroup(_ title: LocalizedStringKey, pages: [Page]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.system(size: 10, weight: .medium)).foregroundStyle(.tertiary)
                .padding(.leading, 12).padding(.bottom, 6)
            ForEach(pages) { item in SidebarButton(page: item, selected: page == item) { page = item } }
        }
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            Circle().fill(store.preview ? ZFlyPalette.tint : (store.connected ? Color.green : .orange)).frame(width: 5, height: 5)
            Text(LocalizedStringKey(store.preview ? "演示模式 · 不连接核心" : store.status)).lineLimit(1)
            Spacer(minLength: 8)
            if store.isBusy || store.refreshingStatus { ProgressView().controlSize(.mini) }
            Button("刷新", systemImage: "arrow.clockwise") { Task { await store.refreshStatus() } }
                .labelStyle(.iconOnly).help("刷新核心状态").disabled(store.isBusy || store.preview)
            Button("运行信息") { controllerSettings = true }
        }
        .font(.system(size: 11)).foregroundStyle(.secondary).buttonStyle(.plain)
        .padding(.horizontal, ZFlyMetrics.pagePadding).frame(height: 34)
    }

    @ViewBuilder private func content(for page: Page) -> some View {
        switch page {
        case .overview: DashboardView(openSubscriptions: { self.page = .profiles })
        case .profiles: ManagementView(network: false)
        case .proxies: ProxiesView()
        case .connections: ConnectionsView()
        case .settings: AppSettingsView()
        case .workspace: WorkspaceView(active: self.page == .workspace)
        case .catalog: CatalogView(active: self.page == .catalog)
        case .tools: ToolsView()
        }
    }

    private func pageIndex(_ page: Page) -> Int {
        Page.allCases.firstIndex(of: page) ?? 0
    }
}

struct ControllerSheet: View {
    @EnvironmentObject private var store: Store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("ZFly 运行信息").font(.title2.bold())
            Text("代理核心与本地服务由 ZFly 自动启动和退出。直接在“订阅”页添加订阅或导入 YAML。")
                .foregroundStyle(.secondary)
            Form {
                LabeledContent("状态", value: store.status)
                LabeledContent("数据目录", value: store.dataDirectory.isEmpty ? "启动后创建" : store.dataDirectory)
                LabeledContent("本地服务", value: store.endpoint)
            }
            Text("本地接口使用每次启动生成的随机凭据，无需手动填写。默认混合代理端口为 7890；若被其他代理占用，可在设置 → 网络接管中修改。")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("关闭", role: .cancel) { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }.padding(28).frame(width: 480)
    }
}
