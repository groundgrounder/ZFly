import AppKit
import SwiftUI
import ZFlyKit

private struct WorkspaceDraft {
    var document: ManagedDocument
    var text: String
    var dirty: Bool { text != document.text }
}

struct WorkspaceView: View {
    @Environment(Store.self) private var store
    var active = true
    @State private var section: WorkspaceSection = .dns
    @State private var profileID = ""
    @State private var drafts: [String: WorkspaceDraft] = [:]
    @State private var message = ""
    @State private var failed = false
    @State private var reloadConfirmation = false
    @State private var source = false
    @State private var query = ""
    @State private var loadingKeys: Set<String> = []
    @State private var pendingLoad: String?

    private var key: String { section.rawValue + ":" + (section.profileSpecific ? profileID : "") }
    private var current: WorkspaceDraft? { drafts[key] }
    private var text: Binding<String> {
        Binding(get: { drafts[key]?.text ?? "" }, set: { drafts[key]?.text = $0 })
    }
    private var unavailable: Bool { store.management == nil || (section.profileSpecific && profileID.isEmpty) }

    var body: some View {
        VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
            PageHeader("规则", subtitle: "编辑 DNS、分流规则与配置覆盖。")
            if section == .dav {
                Text("password 为 null 时保留已有密码；填写字符串时更新密码，空字符串清除密码。备份和恢复入口在“工具”页。")
                    .font(.callout).foregroundStyle(.secondary)
            }
            HStack {
                Text("分类").font(.callout)
                Picker("分类", selection: $section) {
                    ForEach(WorkspaceSection.supported) { Text(LocalizedStringKey($0.title)).tag($0) }
                }.labelsHidden().frame(width: ZFlyMetrics.fieldWidth)
                if section.profileSpecific {
                    Text("配置").font(.callout)
                    Picker("配置", selection: $profileID) {
                        Text("选择配置").tag("")
                        ForEach(store.management?.profiles ?? []) { Text($0.label).tag($0.id) }
                    }.labelsHidden().frame(maxWidth: ZFlyMetrics.fieldWidth)
                }
                Spacer(minLength: 0)
            }.frame(minHeight: 44)
            if store.management == nil {
                ContentUnavailableView("等待本地运行时就绪", systemImage: "cable.connector",
                                       description: Text(LocalizedStringKey(store.managementStatus)))
            } else if let draft = current {
                VStack(spacing: 0) {
                    HStack {
                        Label(draft.dirty ? "有未保存的更改" : (draft.document.writable ? "已载入" : "只读记录"),
                              systemImage: draft.dirty ? "pencil.circle" : "checkmark.circle")
                            .font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        if draft.document.writable && draft.document.format == "json" {
                            Toggle("源文本", isOn: $source).toggleStyle(.switch).controlSize(.small)
                        } else if !draft.document.writable {
                            TextField("搜索记录", text: $query).textFieldStyle(.roundedBorder).frame(maxWidth: ZFlyMetrics.fieldWidth)
                        }
                    }
                    .padding(.horizontal, ZFlyMetrics.panelPadding).padding(.vertical, 16)
                    Divider().opacity(0.5)
                    if draft.document.writable && draft.document.format == "json" && !source {
                        JSONPreferencesEditor(text: text)
                    } else if draft.document.writable {
                        TextEditor(text: text).font(.system(.body, design: .monospaced)).scrollContentBackground(.hidden)
                            .padding(ZFlyMetrics.panelPadding)
                    } else {
                        ScrollView([.horizontal, .vertical]) {
                            Text(filtered(draft.text)).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading).padding(ZFlyMetrics.panelPadding)
                        }
                    }
                }.modifier(SurfacePanel())
            } else if loadingKeys.contains(key) {
                ProgressView("正在载入…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView(section.profileSpecific && profileID.isEmpty ? "先选择配置" : "尚未载入", systemImage: "doc.text")
            }
            if !message.isEmpty { Text(LocalizedStringKey(message)).font(.callout).foregroundStyle(failed ? Color.red : Color.secondary).textSelection(.enabled) }
            HStack {
                Button("导出文本") { export() }.disabled(current == nil || store.isBusy)
                Spacer()
                Button("重新载入") { if current?.dirty == true { reloadConfirmation = true } else { load() } }
                    .disabled(unavailable || store.isBusy)
                Button("保存并应用") { save() }.modifier(ActionStyle(prominent: true))
                    .disabled(current?.dirty != true || current?.document.writable != true || store.isBusy || unavailable)
            }
        }.padding(ZFlyMetrics.pagePadding)
            .task { if profileID.isEmpty { profileID = store.management?.currentProfileID ?? "" }; if current == nil { load() } }
            .onChange(of: key) { _, _ in message = ""; query = ""; if current == nil { load() } }
            .onChange(of: store.management?.currentProfileID) { _, value in if profileID.isEmpty { profileID = value ?? "" } }
            .onChange(of: store.management?.profiles.map(\.id)) { _, ids in
                if let ids, !profileID.isEmpty, !ids.contains(profileID) {
                    profileID = store.management?.currentProfileID ?? ""
                }
            }
            .onChange(of: active) { _, active in if active && current == nil { load() } }
            .onChange(of: store.management != nil) { _, available in if available && current == nil { load() } }
            .onChange(of: store.isBusy) { _, busy in
                if !busy, pendingLoad == key { pendingLoad = nil; load() }
            }
            .confirmationDialog("重新载入会丢弃此分类的未保存更改。", isPresented: $reloadConfirmation) {
                Button("丢弃并重新载入", role: .destructive) { load() }
                Button("取消", role: .cancel) {}
            }
    }

    private func filtered(_ value: String) -> String {
        guard !query.isEmpty, let data = value.data(using: .utf8),
              let records = try? JSONSerialization.jsonObject(with: data) as? [Any] else { return value }
        let matches = records.filter { String(describing: $0).localizedCaseInsensitiveContains(query) }
        guard let encoded = try? JSONSerialization.data(withJSONObject: matches, options: [.prettyPrinted, .sortedKeys]) else { return value }
        return String(decoding: encoded, as: UTF8.self)
    }

    private func load() {
        guard active, !unavailable, !loadingKeys.contains(key) else { return }
        if store.isBusy { pendingLoad = key; return }
        var command = ManagementCommand("workspace.read"); command.section = section.rawValue
        if section.profileSpecific { command.id = profileID }
        let target = key
        loadingKeys.insert(target)
        Task {
            defer { loadingKeys.remove(target) }
            do {
                guard let document = try await store.perform(command).document else { throw ControllerError.invalidResponse }
                drafts[target] = WorkspaceDraft(document: document, text: document.text)
                if target == key { failed = false; message = "" }
            } catch { if target == key { failed = true; message = error.localizedDescription } }
        }
    }

    private func save() {
        guard let draft = current else { return }
        var command = ManagementCommand("workspace.write"); command.section = section.rawValue
        command.text = draft.text; command.revision = draft.document.revision
        if section.profileSpecific { command.id = profileID }
        let target = key
        Task {
            do {
                guard let document = try await store.perform(command).document else { throw ControllerError.invalidResponse }
                if drafts[target]?.text == draft.text { drafts[target] = WorkspaceDraft(document: document, text: document.text) }
                else { drafts[target]?.document = document }
                if target == key { failed = false; message = store.preview ? "演示更改仅保存在内存中。" : "已保存并提交应用。" }
                await store.refresh()
            } catch { if target == key { failed = true; message = error.localizedDescription } }
        }
    }

    private func export() {
        guard let draft = current else { return }
        let panel = NSSavePanel(); panel.nameFieldStringValue = section.rawValue + (draft.document.format == "json" ? ".json" : ".txt")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try Data(draft.text.utf8).write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        }
        catch { failed = true; message = error.localizedDescription }
    }
}

private struct JSONPreferencesEditor: View {
    @Binding var text: String
    private var object: [String: Any]? { text.data(using: .utf8).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } }
    var body: some View {
        if let object {
            let keys = object.keys.sorted()
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(keys, id: \.self) { key in
                        if key != keys.first { Divider().opacity(0.5) }
                        HStack(spacing: 24) {
                            Text(LocalizedStringKey(key)).font(.callout)
                            Spacer(minLength: 0)
                            if let number = object[key] as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() {
                                Toggle(key, isOn: Binding(get: { (self.object?[key] as? Bool) ?? false }, set: { update(key, $0) }))
                                    .labelsHidden().toggleStyle(.switch).controlSize(.small)
                            } else if let value = object[key] as? String {
                                TextField(key, text: Binding(get: { (self.object?[key] as? String) ?? value }, set: { update(key, $0) }))
                                    .labelsHidden().textFieldStyle(.roundedBorder).frame(width: ZFlyMetrics.fieldWidth)
                            } else if let value = object[key] as? NSNumber {
                                (Text(value.stringValue) + Text(" · 在源文本中编辑"))
                                    .font(.callout).foregroundStyle(.secondary)
                            } else {
                                Text(LocalizedStringKey(object[key] is [Any] ? "列表 · 在源文本中编辑" : "嵌套配置 · 在源文本中编辑"))
                                    .font(.callout).foregroundStyle(.secondary)
                            }
                        }
                        .frame(minHeight: ZFlyMetrics.rowHeight)
                    }
                }
                .padding(ZFlyMetrics.panelPadding)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            TextEditor(text: $text).font(.system(.body, design: .monospaced))
        }
    }
    private func update(_ key: String, _ value: Any) {
        guard var object, JSONSerialization.isValidJSONObject(object) else { return }
        object[key] = value
        if let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]) { text = String(decoding: data, as: UTF8.self) }
    }
}

struct ToolsView: View {
    @Environment(Store.self) private var store
    @State private var domain = "example.com"
    @State private var recordType = "A"
    @State private var provider = ""
    @State private var output = PreviewOptions.value("--preview-tool-output") ?? ""
    @State private var category = PreviewOptions.value("--preview-tools") ?? "诊断"
    @State private var executing = false
    @State private var failed = false
    @State private var confirmation = false
    @State private var restoreTool: String?
    @State private var restoreConfirmation = false
    var body: some View {
        VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
            PageHeader("工具", subtitle: "诊断网络、维护核心与备份配置。")
            SectionTabs(title: "工具分类", selection: $category, items: [
                SectionTab(value: "诊断", title: "诊断", icon: "waveform.path.ecg"),
                SectionTab(value: "维护", title: "维护", icon: "wrench.and.screwdriver"),
                SectionTab(value: "备份与恢复", title: "备份与恢复", icon: "externaldrive")
            ])
            ScrollView {
                VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
                    Group {
                        if category == "诊断" {
                            ContentSection("诊断") {
                                VStack(spacing: 0) {
                                    PreferenceRow("域名") {
                                        TextField("域名", text: $domain).textFieldStyle(.roundedBorder).frame(width: ZFlyMetrics.fieldWidth)
                                    }
                                    Divider().opacity(0.5)
                                    PreferenceRow("记录类型") {
                                        Picker("记录类型", selection: $recordType) {
                                            ForEach(["A", "AAAA", "CNAME", "MX", "TXT", "NS", "HTTPS"], id: \.self) { Text($0) }
                                        }.frame(width: ZFlyMetrics.fieldWidth)
                                    }
                                }
                                LazyVGrid(columns: actionColumns, spacing: 12) {
                                    ToolAction("查询") { inspect("dnsQuery") }
                                        .disabled(domain.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                                    ToolAction("实际生效的规则") { inspect("rules") }
                                }
                            }
                        } else if category == "维护" {
                            ContentSection("核心") {
                                LazyVGrid(columns: actionColumns, spacing: 12) {
                                    ToolAction("清空 DNS 缓存") { inspect("flushDNS") }
                                    ToolAction("清空 Fake-IP") { inspect("flushFakeIP") }
                                    ToolAction("重新应用配置") { tool("apply") }
                                    ToolAction("重启核心") { tool("restart") }
                                    ToolAction("回收内存") { tool("gc") }
                                    ToolAction("关闭全部连接", role: .destructive) { confirmation = true }
                                }
                            }
                            ContentSection("订阅与资源") {
                                LazyVGrid(columns: actionColumns, spacing: 12) {
                                    ToolAction("更新全部订阅") { tool("updateProfiles") }
                                    ToolAction("更新全部资源") { tool("updateResources") }
                                }
                                PreferenceRow("提供者名称") {
                                    TextField("提供者名称", text: $provider).textFieldStyle(.roundedBorder).frame(width: ZFlyMetrics.fieldWidth)
                                }
                                HStack {
                                    Spacer(minLength: 0)
                                    ToolAction("更新指定提供者") { tool("updateProvider") }
                                        .frame(width: ZFlyMetrics.fieldWidth)
                                        .disabled(provider.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                                }
                            }
                        } else {
                            ContentSection("备份") {
                                Text("导出配置、订阅和偏好设置。备份包含私密链接及凭据，请妥善保管。WebDAV 连接在“规则”页配置。")
                                    .font(.callout).foregroundStyle(.secondary)
                                LazyVGrid(columns: actionColumns, spacing: 12) {
                                    ToolAction("本地备份") { tool("backupLocal") }
                                    ToolAction("备份到 WebDAV") { tool("backupDAV") }
                                }
                                LazyVGrid(columns: actionColumns, spacing: 12) {
                                    Text("WebDAV").font(.callout).frame(maxWidth: .infinity, alignment: .leading)
                                    ToolAction("测试 WebDAV") { tool("testDAV") }
                                }
                            }
                            ContentSection("恢复") {
                                LazyVGrid(columns: actionColumns, spacing: 12) {
                                    ToolAction("本地恢复配置") { restore("restoreLocalProfiles") }
                                    ToolAction("WebDAV 恢复配置") { restore("restoreDAVProfiles") }
                                    ToolAction("本地恢复全部", role: .destructive) { restore("restoreLocalAll") }
                                    ToolAction("WebDAV 恢复全部", role: .destructive) { restore("restoreDAVAll") }
                                }
                            }
                        }
                    }.disabled(store.isBusy || executing)
                    outputPanel
                }.padding(.bottom, 4)
            }
        }.padding(ZFlyMetrics.pagePadding)
            .confirmationDialog("关闭全部连接？应用需要重新建立网络连接。", isPresented: $confirmation) {
                Button("关闭全部连接", role: .destructive) { inspect("closeConnections") }
                Button("取消", role: .cancel) {}
            }
            .confirmationDialog("恢复会覆盖当前数据", isPresented: $restoreConfirmation) {
                Button("继续恢复", role: .destructive) { if let restoreTool { tool(restoreTool) } }
                Button("取消", role: .cancel) {}
            } message: {
                Text(restoreTool?.hasSuffix("All") == true ? "将替换配置和 ZFly 偏好，并停止代理。恢复后请检查配置再启动。" : "将替换现有配置和相关资源，保留主程序偏好。")
            }
    }

    private var actionColumns: [GridItem] { [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)] }

    private var outputPanel: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("执行结果", systemImage: "terminal").font(.system(size: 12, weight: .semibold))
                Spacer()
                if executing { ProgressView().controlSize(.small) }
                Button("复制结果", systemImage: "doc.on.doc") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(output, forType: .string)
                }.labelStyle(.iconOnly).buttonStyle(.plain).help("复制结果").disabled(output.isEmpty)
            }
            if output.isEmpty {
                Text("选择工具后，执行结果会显示在这里。")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                ScrollView([.vertical, .horizontal]) {
                    Text(LocalizedStringKey(output)).foregroundStyle(failed ? Color.red : Color.primary)
                        .font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }.frame(height: 118)
            }
        }.padding(ZFlyMetrics.panelPadding).modifier(SurfacePanel())
    }
    private func restore(_ name: String) { restoreTool = name; restoreConfirmation = true }
    private func inspect(_ name: String) {
        guard !executing, !store.isBusy else { return }
        executing = true
        failed = false
        Task {
            defer { executing = false }
            do { output = try await store.inspect(name, name: domain.trimmingCharacters(in: .whitespacesAndNewlines), type: recordType) }
            catch { failed = true; output = error.localizedDescription }
        }
    }
    private func tool(_ name: String) {
        guard !executing, !store.isBusy else { return }
        executing = true
        failed = false
        var command = ManagementCommand("tool.run"); command.tool = name; command.name = provider.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            defer { executing = false }
            do {
                if name.hasPrefix("restoreLocal") {
                    let panel = NSOpenPanel(); panel.allowsMultipleSelection = false
                    guard await panel.begin() == .OK, let url = panel.url else { return }
                    let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                    guard size <= 16 * 1024 * 1024 else { throw ControllerError.invalidConfig("备份超过 16 MiB。") }
                    command.data = try Data(contentsOf: url).base64EncodedString()
                }
                let result = try await store.perform(command)
                if let file = result.file {
                    guard let data = Data(base64Encoded: file.data) else { throw ControllerError.invalidResponse }
                    let panel = NSSavePanel(); panel.nameFieldStringValue = URL(fileURLWithPath: file.name).lastPathComponent
                    guard await panel.begin() == .OK, let url = panel.url else { return }
                    try data.write(to: url, options: .atomic)
                    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
                    output = "备份已保存。文件包含订阅链接和凭据，请妥善保管。"
                } else { output = result.message ?? "操作已完成。" }
                await store.refresh()
            } catch { failed = true; output = error.localizedDescription }
        }
    }
}
