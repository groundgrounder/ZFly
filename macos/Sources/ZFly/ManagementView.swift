import AppKit
import SwiftUI
import UniformTypeIdentifiers
import ZFlyKit

struct ManagementView: View {
    @EnvironmentObject private var store: Store
    let network: Bool
    @State private var connection = false

    var body: some View {
        if let state = store.management {
            if network {
                NetworkManagement(state: state)
            } else {
                ProfileManagement(state: state)
            }
        } else {
            VStack(spacing: 20) {
                Image(systemName: "externaldrive.connected.to.line.below").font(.system(size: 42)).foregroundStyle(.secondary)
                Text("ZFly 正在准备运行环境").font(.title2.bold())
                Text("ZFly 会自动启动自己的本地服务和代理核心。准备完成后即可直接添加订阅或导入 YAML 配置。")
                    .multilineTextAlignment(.center).foregroundStyle(.secondary)
                Text(LocalizedStringKey(store.managementStatus)).font(.callout).textSelection(.enabled)
                HStack {
                    Button("重试启动") { Task { await store.startRuntime(); await store.refreshManagement() } }
                    Button("运行信息") { connection = true }.modifier(ActionStyle(prominent: true))
                }.disabled(store.isBusy)
            }.padding(36).frame(maxWidth: 620).frame(maxWidth: .infinity, maxHeight: .infinity)
                .sheet(isPresented: $connection) { ControllerSheet() }
        }
    }
}

private struct ProfileManagement: View {
    @EnvironmentObject private var store: Store
    @Environment(\.locale) private var locale
    let state: ManagedState
    @State private var adding = false
    @State private var importing = false
    @State private var editing: ManagedProfile?
    @State private var deleting: ManagedProfile?
    @State private var document: ProfileDocument?
    @State private var message: String?
    @State private var working = false
    @State private var failed = false
    @State private var creating = false
    @State private var customLabel = ""

    private var disabled: Bool { working || store.isBusy }

    var body: some View {
        VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
            PageHeader("订阅", subtitle: "管理订阅与本地配置，随时切换。") {
                HStack(spacing: 8) {
                    Menu {
                        Button("导入 YAML", systemImage: "square.and.arrow.down") { importing = true }
                        Button("自定义配置", systemImage: "doc.badge.plus") { customLabel = ""; creating = true }
                    } label: { Label("导入配置", systemImage: "square.and.arrow.down") }
                    Button("添加订阅", systemImage: "plus") { adding = true }.modifier(ActionStyle(prominent: true))
                }.disabled(disabled)
            }
            if state.profiles.isEmpty {
                ContentUnavailableView("暂无配置", systemImage: "doc.badge.plus", description: Text("导入本地 YAML 或添加订阅地址。"))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 14) {
                        ForEach(state.profiles) { profile in profileCard(profile) }
                    }.padding(.bottom, 4)
                }
            }
            if let message {
                Label(LocalizedStringKey(message), systemImage: failed ? "exclamationmark.circle" : "checkmark.circle")
                    .font(.callout).foregroundStyle(failed ? Color.red : ZFlyPalette.tint).textSelection(.enabled)
            }
        }.padding(ZFlyMetrics.pagePadding)
        .sheet(isPresented: $adding) { ProfileProperties(profile: nil) }
        .alert("创建自定义配置", isPresented: $creating) {
            TextField("名称", text: $customLabel)
            Button("取消", role: .cancel) {}
            Button("创建") {
                var command = ManagementCommand("profile.create"); command.label = customLabel.trimmingCharacters(in: .whitespacesAndNewlines)
                working = true
                Task {
                    defer { working = false }
                    do { _ = try await store.perform(command) }
                    catch { failed = true; message = error.localizedDescription }
                }
            }.disabled(customLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .sheet(item: $editing) { ProfileProperties(profile: $0) }
        .sheet(item: $document) { ProfileEditor(document: $0) }
        .alert("删除配置？", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
            Button("取消", role: .cancel) { deleting = nil }
            Button("删除", role: .destructive) {
                if let profile = deleting { send("profile.delete", profile) }
                deleting = nil
            }
        } message: { Text("将删除“\(deleting?.label ?? "")”及其本地文件。") }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.yaml, .plainText, .data]) { result in
            do {
                let url = try result.get()
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                let values = try url.resourceValues(forKeys: [.fileSizeKey])
                guard (values.fileSize ?? 0) <= 4 * 1024 * 1024 else { throw ControllerError.invalidConfig("配置文件不能超过 4 MiB。") }
                let data = try Data(contentsOf: url)
                var command = ManagementCommand("profile.import")
                command.label = url.deletingPathExtension().lastPathComponent
                command.data = data.base64EncodedString()
                perform(command)
            } catch { failed = true; message = error.localizedDescription }
        }
    }

    private func profileCard(_ profile: ManagedProfile) -> some View {
        let selected = state.currentProfileID == profile.id
        let usage = max(profile.upload ?? 0, 0).addingReportingOverflow(max(profile.download ?? 0, 0))
        let usedBytes = usage.overflow ? Int64.max : usage.partialValue
        return VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Image(systemName: profile.type == "url" ? "arrow.triangle.2.circlepath" : "doc.text")
                    .font(.system(size: 18)).foregroundStyle(selected ? ZFlyPalette.tint : Color.secondary)
                    .frame(width: 40, height: 40)
                    .background((selected ? ZFlyPalette.tint : Color.secondary).opacity(0.07), in: RoundedRectangle(cornerRadius: 11))
                VStack(alignment: .leading, spacing: 5) {
                    Text(profile.label).font(.system(size: 14, weight: .semibold)).lineLimit(1)
                    HStack(spacing: 6) {
                        Text(profile.type == "url" ? "订阅" : (profile.type == "file" ? "本地文件" : "自定义配置"))
                        if profile.type == "url" {
                            Text("·")
                            Group {
                                if let host = URL(string: profile.url)?.host { Text(host) }
                                else { Text("订阅链接") }
                            }.lineLimit(1)
                        }
                    }.font(.system(size: 11)).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if selected { StatusBadge(title: "使用中", color: ZFlyPalette.tint) }
                Menu {
                    Button("属性") { editing = profile }
                    if profile.type != "custom" { Button("查看 / 编辑") { read(profile, export: false) } }
                    Button("导出") { read(profile, export: true) }
                    Divider()
                    Button("删除", role: .destructive) { deleting = profile }
                } label: { Image(systemName: "ellipsis").frame(width: 24, height: 24) }
                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize().help("配置操作").disabled(disabled)
            }
            if profile.type == "url" {
                if let total = profile.total, total > 0 {
                    VStack(alignment: .leading, spacing: 8) {
                        ProgressView(value: min(Double(usedBytes) / Double(total), 1))
                            .tint(ZFlyPalette.tint)
                        HStack {
                            Text("已用 \(bytes(usedBytes)) / \(bytes(total))")
                            Spacer()
                            if let expiry = profile.expire, expiry > 0 {
                                (Text("到期：") + Text(Date(timeIntervalSince1970: Double(expiry)), format: .dateTime.year().month(.abbreviated).day()))
                            }
                        }.font(.system(size: 10)).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
                if let error = state.updateErrors?[profile.id] {
                    Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
            } else if profile.type == "custom" {
                Text("节点和代理组在“资源”页管理，规则及扩展方式在“规则”页管理。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 8) {
                if profile.type == "url" {
                    if profile.autoUpdate {
                        Text("每 \(profile.updateHours) 小时自动更新")
                    } else { Text("自动更新已关闭") }
                } else { Text("配置保存在本机") }
                Spacer(minLength: 12)
                if profile.type == "url" {
                    Button("更新订阅", systemImage: "arrow.clockwise") { send("profile.update", profile) }
                }
                if !selected {
                    Button("使用此配置", systemImage: "checkmark") { send("profile.select", profile) }
                        .modifier(ActionStyle(prominent: true))
                }
            }.font(.system(size: 10)).foregroundStyle(.secondary).disabled(disabled)
            if profile.type == "url", let updated = profile.updated {
                (Text("最近更新：") + Text(displayDate(updated, locale: locale)))
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
            }
        }.padding(ZFlyMetrics.panelPadding).modifier(SurfacePanel(selected: selected))
    }

    private func send(_ action: String, _ profile: ManagedProfile) {
        var command = ManagementCommand(action)
        command.id = profile.id
        command.revision = profile.revision
        perform(command)
    }

    private func perform(_ command: ManagementCommand) {
        working = true
        message = nil
        failed = false
        Task {
            defer { working = false }
            do {
                let result = try await store.perform(command)
                message = result.message ?? "配置已保存。"
            } catch { failed = true; message = error.localizedDescription }
        }
    }

    private func read(_ profile: ManagedProfile, export: Bool) {
        var command = ManagementCommand("profile.read")
        command.id = profile.id
        working = true
        Task {
            defer { working = false }
            do {
                guard let file = try await store.manage(command), let bytes = Data(base64Encoded: file.data) else {
                    throw ControllerError.invalidResponse
                }
                if export {
                    let panel = NSSavePanel()
                    panel.nameFieldStringValue = URL(fileURLWithPath: file.name).lastPathComponent
                    panel.allowedContentTypes = [.yaml, .plainText]
                    if await panel.begin() == .OK, let url = panel.url {
                        try bytes.write(to: url, options: .atomic)
                        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
                    }
                } else {
                    guard let text = String(data: bytes, encoding: .utf8) else {
                        throw ControllerError.invalidConfig("此文件不是 UTF-8 文本，请导出后使用外部编辑器。")
                    }
                    document = ProfileDocument(profile: profile, file: file, text: text)
                }
            } catch { failed = true; message = error.localizedDescription }
        }
    }
}

private func displayDate(_ value: String, locale: Locale) -> String {
    let iso = ISO8601DateFormatter()
    if let date = iso.date(from: value) {
        return date.formatted(Date.FormatStyle(date: .abbreviated, time: .shortened).locale(locale))
    }
    let fallback = DateFormatter()
    fallback.locale = Locale(identifier: "en_US_POSIX")
    fallback.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    if let date = fallback.date(from: value) {
        return date.formatted(Date.FormatStyle(date: .abbreviated, time: .shortened).locale(locale))
    }
    return value
}

private struct ProfileProperties: View {
    @EnvironmentObject private var store: Store
    @Environment(\.dismiss) private var dismiss
    let profile: ManagedProfile?
    @State private var label: String
    @State private var url: String
    @State private var ageKey = ""
    @State private var autoUpdate: Bool
    @State private var hours: String
    @State private var saving = false
    @State private var message: String?

    init(profile: ManagedProfile?) {
        self.profile = profile
        _label = State(initialValue: profile?.label ?? "")
        _url = State(initialValue: profile?.url ?? "")
        _autoUpdate = State(initialValue: profile?.autoUpdate ?? true)
        _hours = State(initialValue: String(profile?.updateHours ?? 24))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(profile == nil ? "添加订阅" : "配置属性").font(.title2.bold())
            Form {
                TextField("名称", text: $label)
                if profile == nil || profile?.type == "url" {
                    TextField("订阅 URL", text: $url)
                    if profile == nil { SecureField("Age 解密密钥（可选）", text: $ageKey) }
                    Toggle("自动更新", isOn: $autoUpdate)
                    TextField("更新间隔（小时）", text: $hours).disabled(!autoUpdate)
                }
            }.disabled(saving)
            if let message { Text(LocalizedStringKey(message)).foregroundStyle(.red).font(.callout) }
            HStack {
                if saving { ProgressView().controlSize(.small); Text("正在下载、校验或保存…").font(.caption) }
                Spacer()
                Button("取消", role: .cancel) { dismiss() }.disabled(saving)
                Button("保存") { save() }.modifier(ActionStyle(prominent: true)).disabled(saving || store.isBusy)
            }
        }.padding(28).frame(width: 510).interactiveDismissDisabled(saving)
    }

    private func save() {
        let normalizedLabel = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedLabel.isEmpty else {
            message = "名称不能为空。"; return
        }
        let interval: Int
        if autoUpdate {
            guard let value = Int(hours), (1...8760).contains(value) else {
                message = "自动更新间隔应为 1–8760 小时。"; return
            }
            interval = value
        } else {
            // Keep a valid persisted interval while auto-update is disabled. This
            // lets users turn the feature off even if the disabled field is stale.
            interval = profile?.updateHours ?? 24
        }
        var command = ManagementCommand(profile == nil ? "profile.add" : "profile.edit")
        command.id = profile?.id
        command.revision = profile?.revision
        command.label = normalizedLabel
        if profile == nil || profile?.type == "url" {
            command.url = url.trimmingCharacters(in: .whitespacesAndNewlines)
            command.ageSecretKey = ageKey.isEmpty ? nil : ageKey
            command.autoUpdate = autoUpdate
            command.updateHours = interval
        }
        saving = true
        Task {
            defer { saving = false }
            do { _ = try await store.manage(command); ageKey = ""; dismiss() }
            catch { message = error.localizedDescription }
        }
    }
}

private struct ProfileDocument: Identifiable {
    var id: String { profile.id }
    let profile: ManagedProfile
    let file: ManagedFile
    let text: String
}

private struct ProfileEditor: View {
    @EnvironmentObject private var store: Store
    @Environment(\.dismiss) private var dismiss
    let document: ProfileDocument
    @State private var text: String
    @State private var saving = false
    @State private var message: String?
    @State private var discarding = false

    init(document: ProfileDocument) {
        self.document = document
        _text = State(initialValue: document.text)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(document.profile.label).font(.title2.bold())
            Text(document.profile.type == "file" ? "保存前由核心校验；当前配置保存后重新应用。加密文件请使用外部编辑器。" : "订阅内容为只读，请编辑订阅源后更新。")
                .font(.callout).foregroundStyle(.secondary)
            TextEditor(text: $text).font(.system(.body, design: .monospaced)).scrollContentBackground(.hidden)
                .padding(12).background(.background.opacity(0.6), in: RoundedRectangle(cornerRadius: ZFlyMetrics.editorRadius, style: .continuous))
                .disabled(saving || document.profile.type != "file")
            if let message { Text(LocalizedStringKey(message)).foregroundStyle(.red).font(.callout) }
            HStack {
                Spacer()
                Button("关闭") {
                    if text == document.text { dismiss() }
                    else { discarding = true }
                }.disabled(saving)
                if document.profile.type == "file" {
                    Button(saving ? "校验并保存中…" : "校验并保存") {
                        var command = ManagementCommand("profile.write")
                        command.id = document.profile.id
                        command.revision = document.file.revision
                        command.data = Data(text.utf8).base64EncodedString()
                        saving = true
                        Task {
                            defer { saving = false }
                            do { _ = try await store.manage(command); dismiss() }
                            catch { message = error.localizedDescription }
                        }
                    }.modifier(ActionStyle(prominent: true)).disabled(saving || store.isBusy || text == document.text)
                }
            }
        }.padding(24).frame(width: 720, height: 600).interactiveDismissDisabled(saving || text != document.text)
        .confirmationDialog("放弃尚未保存的修改？", isPresented: $discarding, titleVisibility: .visible) {
            Button("放弃修改", role: .destructive) { dismiss() }
            Button("继续编辑", role: .cancel) {}
        }
    }
}

private struct NetworkManagement: View {
    @EnvironmentObject private var store: Store
    let state: ManagedState
    @State private var baseline: ManagedNetwork
    @State private var draft: ManagedNetwork
    @State private var port: String
    @State private var domains: String
    @State private var saving = false
    @State private var message: String?
    @State private var failed = false

    init(state: ManagedState) {
        self.state = state
        _baseline = State(initialValue: state.network)
        _draft = State(initialValue: state.network)
        _port = State(initialValue: String(state.network.mixedPort))
        _domains = State(initialValue: state.network.bypassDomains.joined(separator: "\n"))
    }

    private var dirty: Bool {
        draft != baseline || port != String(baseline.mixedPort) || domains != baseline.bypassDomains.joined(separator: "\n")
    }
    private var disabled: Bool { saving || store.isBusy }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
                    ContentSection("运行") {
                        VStack(spacing: 0) {
                            PreferenceRow("核心状态") { Text(state.coreStatus).foregroundStyle(.secondary) }
                            Divider().opacity(0.5)
                            PreferenceRow("运行请求") {
                                HStack(spacing: 12) {
                                    Text(state.running ? "开启" : "关闭").foregroundStyle(.secondary)
                                    Button(state.running ? "停止核心" : "启动核心") {
                                        var command = ManagementCommand("running.set")
                                        command.running = !state.running
                                        run(command)
                                    }
                                }
                            }
                        }
                    }
                    ContentSection("系统网络") {
                        VStack(spacing: 0) {
                            PreferenceRow("系统代理") { Toggle("系统代理", isOn: $draft.systemProxy).toggleStyle(.switch).controlSize(.small) }
                            Divider().opacity(0.5)
                            PreferenceRow("混合代理端口") {
                                TextField("混合代理端口", text: $port).textFieldStyle(.roundedBorder).frame(width: ZFlyMetrics.fieldWidth)
                            }
                        }
                        VStack(alignment: .leading, spacing: 8) {
                            Text("绕过地址（每行一个）").font(.callout)
                            TextEditor(text: $domains).font(.system(.body, design: .monospaced)).scrollContentBackground(.hidden)
                                .padding(8).frame(height: 104)
                                .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: ZFlyMetrics.controlRadius))
                        }
                        VStack(alignment: .leading, spacing: 8) {
                            Text("核心 DNS 可在“规则 → DNS 与 hosts”配置。系统 DNS 接管尚未提供。")
                            Text("启动代理后，ZFly 将设置本机系统代理；停止或退出时还原原设置。系统拒绝修改时会显示错误。")
                        }.font(.caption).foregroundStyle(.secondary)
                    }
                    ContentSection("虚拟网卡") {
                        Text("macOS TUN 权限助手尚未内置，当前可使用系统代理。")
                            .font(.caption).foregroundStyle(.secondary)
                        VStack(spacing: 0) {
                            PreferenceRow("启用 TUN") { Toggle("启用 TUN", isOn: $draft.tunEnabled).toggleStyle(.switch).controlSize(.small).disabled(!store.preview) }
                            Divider().opacity(0.5)
                            PreferenceRow("协议栈") {
                                Picker("协议栈", selection: $draft.tunStack) {
                                    ForEach(["gvisor", "system", "mixed"], id: \.self) { Text($0) }
                                }.frame(width: ZFlyMetrics.fieldWidth)
                            }
                            Divider().opacity(0.5)
                            PreferenceRow("设备名称") {
                                TextField("设备名称", text: $draft.tunDevice).textFieldStyle(.roundedBorder).frame(width: ZFlyMetrics.fieldWidth)
                            }
                            Divider().opacity(0.5)
                            PreferenceRow("绕过私有路由") { Toggle("绕过私有路由", isOn: $draft.bypassPrivateRoute).toggleStyle(.switch).controlSize(.small) }
                            Divider().opacity(0.5)
                            PreferenceRow("TUN 授权") { Text(state.tunAuthorization).foregroundStyle(.secondary) }
                        }
                    }
                    VStack(alignment: .leading, spacing: 16) {
                        DisclosureGroup {
                            VStack(spacing: 0) {
                                PreferenceRow("路由模式") {
                                    Picker("路由模式", selection: $draft.mode) {
                                        Text("规则").tag("rule")
                                        Text("全局").tag("global")
                                        Text("直连").tag("direct")
                                    }.frame(width: ZFlyMetrics.fieldWidth)
                                }
                                Divider().opacity(0.5)
                                PreferenceRow("日志级别") {
                                    Picker("日志级别", selection: $draft.logLevel) {
                                        ForEach(["silent", "error", "warning", "info", "debug"], id: \.self) { Text($0) }
                                    }.frame(width: ZFlyMetrics.fieldWidth)
                                }
                                Divider().opacity(0.5)
                                PreferenceRow("IPv6") { Toggle("IPv6", isOn: $draft.ipv6).toggleStyle(.switch).controlSize(.small) }
                                Divider().opacity(0.5)
                                PreferenceRow("允许局域网访问") { Toggle("允许局域网访问", isOn: $draft.allowLan).toggleStyle(.switch).controlSize(.small) }
                                Divider().opacity(0.5)
                                PreferenceRow("TCP 并发连接") { Toggle("TCP 并发连接", isOn: $draft.tcpConcurrent).toggleStyle(.switch).controlSize(.small) }
                            }
                        } label: {
                            Text("持久化核心设置").font(.system(size: 12, weight: .semibold)).accessibilityAddTraits(.isHeader)
                        }
                        Text("核心停止时也可保存，供下次启动使用。")
                            .font(.caption).foregroundStyle(.secondary)
                    }.padding(ZFlyMetrics.panelPadding).frame(maxWidth: .infinity, alignment: .leading)
                        .modifier(SurfacePanel())
                }.padding(.horizontal, ZFlyMetrics.pagePadding).padding(.bottom, ZFlyMetrics.pagePadding)
            }.disabled(disabled)
            HStack {
                if let message { Text(LocalizedStringKey(message)).font(.callout).foregroundStyle(failed ? Color.red : Color.secondary) }
                Spacer()
                if disabled { ProgressView().controlSize(.small) }
                Button("重新载入") { reload(state.network) }.disabled(disabled)
                Button("保存设置") { save() }.modifier(ActionStyle(prominent: true)).disabled(disabled || !dirty)
            }.padding(.horizontal, ZFlyMetrics.pagePadding).padding(.vertical, 16)
        }
        .onChange(of: state.network) { _, current in if !dirty && !saving { reload(current) } }
    }

    private func reload(_ network: ManagedNetwork) {
        baseline = network; draft = network
        port = String(network.mixedPort)
        domains = network.bypassDomains.joined(separator: "\n")
        message = nil
    }

    private func save() {
        guard let value = Int(port), (1...65535).contains(value), !draft.tunDevice.trimmingCharacters(in: .whitespaces).isEmpty else {
            failed = true; message = "端口应为 1–65535，TUN 设备名称不能为空。"; return
        }
        var network = draft
        network.mixedPort = value
        network.bypassDomains = domains.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        var command = ManagementCommand("network.save")
        command.network = network; command.expected = baseline
        run(command)
    }

    private func run(_ command: ManagementCommand) {
        saving = true; message = nil
        Task {
            defer { saving = false }
            do {
                _ = try await store.manage(command)
                if command.action == "network.save", let network = store.management?.network { reload(network) }
                failed = false
                message = store.preview ? "已保存演示设置" : "设置已保存到 ZFly。"
            } catch { failed = true; message = error.localizedDescription }
        }
    }
}
