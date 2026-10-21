import SwiftUI
import ZFlyKit

struct AppSettingsView: View {
    private static var initialSection: String {
        let requested = PreviewOptions.value("--preview-settings") ?? "核心"
        return ["核心", "网络接管", "外观", "启动"].contains(requested) ? requested : "核心"
    }
    @State private var section = Self.initialSection
    @State private var visited: Set<String> = [Self.initialSection]
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
                PageHeader("设置")
                SectionTabs(title: "设置分类", selection: $section, items: [
                    SectionTab(value: "核心", title: "核心", icon: "cpu"),
                    SectionTab(value: "网络接管", title: "网络接管", icon: "network"),
                    SectionTab(value: "外观", title: "外观", icon: "paintpalette"),
                    SectionTab(value: "启动", title: "启动", icon: "power")
                ])
            }
            .padding(.horizontal, ZFlyMetrics.pagePadding).padding(.top, ZFlyMetrics.pagePadding)
            .padding(.bottom, ZFlyMetrics.sectionSpacing)
            ZStack {
                retained("核心") { CoreSettingsView() }
                retained("网络接管") { ManagementView(network: true) }
                retained("外观") { AppearanceSettingsView() }
                retained("启动") { StartupSettingsView() }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: section)
        }
        .buttonStyle(ZFlyButtonStyle())
        .onChange(of: section) { _, value in visited.insert(value) }
    }

    private func retained<Content: View>(_ name: String, @ViewBuilder content: () -> Content) -> some View {
        Group {
            if visited.contains(name) {
                content().opacity(section == name ? 1 : 0)
                    .disabled(section != name)
                    .allowsHitTesting(section == name).accessibilityHidden(section != name)
            }
        }
    }
}

private struct CoreSettingsView: View {
    @Environment(Store.self) private var store
    @State private var baseline = CoreConfig()
    @State private var draft = CoreConfig()
    @State private var ports = [String: String]()
    @State private var loaded = false
    @State private var saving = false
    @State private var message: String?
    @State private var failed = false

    private var dirty: Bool {
        draft != baseline || ports != portStrings(baseline)
    }

    var body: some View {
        if store.snapshot == nil {
            ContentUnavailableView("尚未连接核心", systemImage: "network",
                                   description: Text("ZFly 会自动启动自己的核心。可在运行信息中检查启动情况。"))
        } else {
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
                        ContentSection("路由与连接") {
                            VStack(spacing: 0) {
                                if draft.mode != nil {
                                    PreferenceRow("路由模式") {
                                        Picker("路由模式", selection: string(\.mode)) {
                                            Text("规则").tag("rule")
                                            Text("全局").tag("global")
                                            Text("直连").tag("direct")
                                        }.frame(width: ZFlyMetrics.fieldWidth)
                                    }
                                    Divider().opacity(0.5)
                                }
                                if draft.logLevel != nil {
                                    PreferenceRow("日志级别") {
                                        Picker("日志级别", selection: string(\.logLevel)) {
                                            ForEach(["silent", "error", "warning", "info", "debug"], id: \.self) { Text($0) }
                                        }.frame(width: ZFlyMetrics.fieldWidth)
                                    }
                                    Divider().opacity(0.5)
                                }
                                if draft.findProcessMode != nil {
                                    PreferenceRow("进程匹配") {
                                        Picker("进程匹配", selection: string(\.findProcessMode)) {
                                            Text("关闭进程匹配").tag("off")
                                            Text("严格").tag("strict")
                                            Text("始终").tag("always")
                                        }.frame(width: ZFlyMetrics.fieldWidth)
                                    }
                                    Divider().opacity(0.5)
                                }
                                if draft.ipv6 != nil {
                                    PreferenceRow("IPv6") { Toggle("IPv6", isOn: flag(\.ipv6)).toggleStyle(.switch).controlSize(.small) }
                                    Divider().opacity(0.5)
                                }
                                if draft.tcpConcurrent != nil {
                                    PreferenceRow("TCP 并发连接") { Toggle("TCP 并发连接", isOn: flag(\.tcpConcurrent)).toggleStyle(.switch).controlSize(.small) }
                                    Divider().opacity(0.5)
                                }
                                if draft.sniffing != nil {
                                    PreferenceRow("域名嗅探") { Toggle("域名嗅探", isOn: flag(\.sniffing)).toggleStyle(.switch).controlSize(.small) }
                                }
                            }
                        }
                        ContentSection("监听端口") {
                            VStack(spacing: 0) {
                                ForEach([("mixed-port", "混合端口"), ("port", "HTTP 端口"), ("socks-port", "SOCKS 端口")], id: \.0) { key, title in
                                    if ports[key] != nil {
                                        PreferenceRow(LocalizedStringKey(title)) {
                                            TextField(LocalizedStringKey(title), text: Binding(get: { ports[key] ?? "" }, set: { ports[key] = $0 }))
                                                .textFieldStyle(.roundedBorder).frame(width: ZFlyMetrics.fieldWidth)
                                        }
                                        Divider().opacity(0.5)
                                    }
                                }
                                if draft.allowLan != nil {
                                    PreferenceRow("允许局域网访问") { Toggle("允许局域网访问", isOn: flag(\.allowLan)).toggleStyle(.switch).controlSize(.small) }
                                    Divider().opacity(0.5)
                                }
                                if draft.bindAddress != nil {
                                    PreferenceRow("监听地址") {
                                        TextField("监听地址", text: string(\.bindAddress)).textFieldStyle(.roundedBorder).frame(width: ZFlyMetrics.fieldWidth)
                                    }
                                }
                            }
                            Text("混合端口范围 1–65535；HTTP / SOCKS 端口可填 0 关闭。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Text("设置由 ZFly 持久保存，修改失败时自动恢复原配置。系统代理和 TUN 状态在网络接管中管理。")
                            .font(.caption).foregroundStyle(.secondary)
                    }.padding(.horizontal, ZFlyMetrics.pagePadding).padding(.bottom, ZFlyMetrics.pagePadding)
                }.disabled(!store.connected || store.isBusy || saving)
                HStack {
                    if let message { Text(verbatim: message).font(.callout).foregroundStyle(failed ? Color.red : Color.secondary) }
                    Spacer()
                    Button("重新载入") { reload() }.disabled(saving || store.isBusy)
                    Button(saving ? "应用中…" : "应用更改") { save() }
                        .modifier(ActionStyle(prominent: true))
                        .disabled(!dirty || saving || store.isBusy || !store.connected)
                }.padding(.horizontal, ZFlyMetrics.pagePadding).padding(.vertical, 16)
            }
            .onAppear { if !loaded { reload() } }
            .onChange(of: store.snapshot?.configs) { _, _ in
                if !dirty && !saving { reload() }
            }
        }
    }

    private func string(_ key: WritableKeyPath<CoreConfig, String?>) -> Binding<String> {
        Binding(get: { draft[keyPath: key] ?? "" }, set: { draft[keyPath: key] = $0 })
    }

    private func flag(_ key: WritableKeyPath<CoreConfig, Bool?>) -> Binding<Bool> {
        Binding(get: { draft[keyPath: key] ?? false }, set: { draft[keyPath: key] = $0 })
    }

    private func portStrings(_ config: CoreConfig) -> [String: String] {
        var values: [String: String] = [:]
        values["mixed-port"] = config.mixedPort.map(String.init)
        values["port"] = config.port.map(String.init)
        values["socks-port"] = config.socksPort.map(String.init)
        return values
    }

    private func reload() {
        guard let current = store.snapshot?.configs else { return }
        baseline = current
        draft = current
        ports = portStrings(current)
        loaded = true
        message = nil
    }

    private func save() {
        do {
            var config = draft
            for (key, value) in ports {
                let text = value.trimmingCharacters(in: .whitespaces)
                guard !text.isEmpty, text.allSatisfy(\.isNumber), let port = Int(text), (0...65535).contains(port) else {
                    throw ControllerError.invalidConfig("端口应为 0–65535 的整数。")
                }
                switch key {
                case "mixed-port": config.mixedPort = port
                case "port": config.port = port
                default: config.socksPort = port
                }
            }
            saving = true
            message = nil
            Task {
                defer { saving = false }
                do {
                    try await store.configure(config, baseline: baseline)
                    reload()
                    failed = false
                    message = "已应用到当前核心"
                } catch {
                    failed = true
                    message = error.localizedDescription
                }
            }
        } catch {
            failed = true
            message = error.localizedDescription
        }
    }
}

private struct AppearanceSettingsView: View {
    @AppStorage("appearance.scheme", store: PreviewOptions.preferences) private var scheme = "system"
    @AppStorage("appearance.language", store: PreviewOptions.preferences) private var language = AppLanguage.system.rawValue
    @AppStorage("appearance.glass", store: PreviewOptions.preferences) private var glass = true
    @AppStorage("appearance.ambient", store: PreviewOptions.preferences) private var ambient = true
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
                ContentSection("主题") {
                    HStack(spacing: 12) {
                        themeChoice("跟随系统", value: "system", icon: "circle.lefthalf.filled")
                        themeChoice("浅色", value: "light", icon: "sun.max")
                        themeChoice("深色", value: "dark", icon: "moon")
                    }
                }
                ContentSection("外观") {
                    VStack(spacing: 0) {
                        PreferenceRow("语言") {
                            Picker("语言", selection: $language) {
                                Text("跟随系统").tag(AppLanguage.system.rawValue)
                                Text("中文").tag(AppLanguage.chinese.rawValue)
                                Text("English").tag(AppLanguage.english.rawValue)
                            }.frame(width: ZFlyMetrics.fieldWidth)
                        }
                        Divider().opacity(0.5)
                        PreferenceRow("侧栏透明效果") { Toggle("侧栏透明效果", isOn: $glass).toggleStyle(.switch).controlSize(.small) }
                        Divider().opacity(0.5)
                        PreferenceRow("背景色彩") { Toggle("背景色彩", isOn: $ambient).toggleStyle(.switch).controlSize(.small) }
                    }
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text("减少背景干扰，让文字与图表更清晰。透明效果仅用于侧边栏。")
                    if reduceTransparency {
                        Label("系统已开启减少透明度，当前使用不透明外观。", systemImage: "accessibility")
                    }
                }.font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, ZFlyMetrics.pagePadding).padding(.bottom, ZFlyMetrics.pagePadding)
        }
    }

    private func themeChoice(_ title: LocalizedStringKey, value: String, icon: String) -> some View {
        Button { scheme = value } label: {
            VStack(spacing: 12) {
                Image(systemName: icon).font(.system(size: 24, weight: .light))
                    .foregroundStyle(scheme == value ? ZFlyPalette.tint : Color.secondary)
                HStack(spacing: 6) {
                    Text(title).font(.system(size: 12, weight: .medium))
                    if scheme == value { Image(systemName: "checkmark.circle.fill").foregroundStyle(ZFlyPalette.tint).font(.system(size: 11)) }
                }
            }
            .frame(maxWidth: .infinity).frame(height: 96)
            .background((scheme == value ? ZFlyPalette.tint : Color.secondary).opacity(scheme == value ? 0.07 : 0.035), in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(scheme == value ? ZFlyPalette.tint.opacity(0.5) : Color.primary.opacity(0.07)))
            .contentShape(RoundedRectangle(cornerRadius: 10))
        }.buttonStyle(.plain).accessibilityAddTraits(scheme == value ? .isSelected : [])
    }
}
