import AppKit
import SwiftUI
import Charts
import ZFlyKit

struct DashboardView: View {
    @Environment(\.zflyAccent) private var accent
    @Environment(Store.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let active: Bool
    let openSubscriptions: () -> Void
    private var running: Bool { store.management?.running == true }
    private var connectionTitle: LocalizedStringKey {
        if store.starting { return "正在启动…" }
        if running && !store.connected { return "正在等待核心连接" }
        return running ? "代理正在运行" : "代理已停止"
    }
    private var profile: ManagedProfile? { store.management?.profiles.first { $0.id == store.management?.currentProfileID } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
                PageHeader("概览") {
                    if store.preview { StatusBadge(title: "演示模式", color: accent.color) }
                }
                connectionCard
                TrafficCard(active: active)
                quickControls
            }
            .padding(ZFlyMetrics.pagePadding).frame(maxWidth: .infinity)
        }
    }

    private var connectionCard: some View {
        HStack(spacing: 18) {
            Image(systemName: running ? "paperplane.fill" : "paperplane")
                .font(.system(size: 24, weight: .medium)).foregroundStyle(accent.color)
                .frame(width: 56, height: 56)
                .background(accent.color.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
                .symbolEffect(.pulse, options: .repeating, isActive: store.starting && !reduceMotion)
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 9) {
                    Text(connectionTitle).font(.system(size: 19, weight: .semibold))
                    Circle().fill(running ? (store.connected ? Color.green : .orange) : Color.secondary).frame(width: 6, height: 6)
                }
                if let profile {
                    HStack(spacing: 7) {
                        Text(profile.label).lineLimit(1)
                        Text("·")
                        Text("本地端口 \(String(store.management?.network.mixedPort ?? 7890))").monospacedDigit()
                    }.font(.callout).foregroundStyle(.secondary)
                } else {
                    Text("添加订阅链接或导入本地配置，开始连接。")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
            if profile == nil {
                Button("添加订阅", systemImage: "plus", action: openSubscriptions).modifier(ActionStyle(prominent: true))
            } else {
                Button {
                    Task { await store.toggleRunning() }
                } label: {
                    HStack(spacing: 7) {
                        if store.isBusy { ProgressView().controlSize(.mini) }
                        else { Image(systemName: running ? "paperplane" : "paperplane.fill") }
                        Text(running ? "停止代理" : "启动代理")
                    }
                }
                .modifier(ActionStyle(prominent: !running)).disabled(store.isBusy)
                .accessibilityLabel(running ? "停止代理" : "启动代理")
            }
        }.padding(ZFlyMetrics.panelPadding).modifier(SurfacePanel())
    }

    private var quickControls: some View {
        HStack(alignment: .top, spacing: 24) {
            VStack(alignment: .leading, spacing: 12) {
                Text("路由模式").font(.system(size: 12, weight: .semibold)).frame(height: 22)
                Picker("路由模式", selection: Binding(get: { store.management?.network.mode ?? "rule" }, set: { mode in
                    Task { do { try await store.setMode(mode) } catch { store.actionError = error.localizedDescription } }
                })) {
                    Text("规则").tag("rule"); Text("全局").tag("global"); Text("直连").tag("direct")
                }.pickerStyle(.segmented).labelsHidden().frame(height: 32).disabled(store.isBusy || !store.connected)
            }.frame(maxWidth: .infinity, alignment: .leading)
            Divider().frame(height: 58)
            VStack(alignment: .leading, spacing: 12) {
                Toggle("系统代理", isOn: Binding(get: { store.management?.network.systemProxy ?? false }, set: { enabled in
                    Task { await store.setSystemProxy(enabled) }
                }))
                .toggleStyle(.switch).controlSize(.small).font(.system(size: 12, weight: .semibold))
                .frame(height: 22).disabled(store.isBusy || store.management == nil)
                Text(running && store.connected && store.management?.network.systemProxy == true ? "系统代理已启用" : "随代理启动生效，停止时恢复原设置")
                    .font(.system(size: 11)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    .frame(minHeight: 32, alignment: .topLeading)
            }.frame(maxWidth: .infinity, alignment: .leading)
        }.padding(ZFlyMetrics.panelPadding).modifier(SurfacePanel())
    }

}

private struct TrafficCard: View {
    @Environment(\.zflyAccent) private var accent
    @Environment(Store.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let active: Bool
    // Freeze the hidden chart without removing it, preserving the overview scroll position.
    @State private var samples: [TrafficSample] = []
    @State private var counters: ConnectionsSnapshot?

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 20) {
                stat("实时下载", value: bytes(Int64(samples.last?.download ?? 0)), unit: "/s", detail: Text("总计 ") + Text(bytes(counters?.downloadTotal)), icon: "arrow.down", color: accent.color)
                Divider().frame(height: 56)
                stat("实时上传", value: bytes(Int64(samples.last?.upload ?? 0)), unit: "/s", detail: Text("总计 ") + Text(bytes(counters?.uploadTotal)), icon: "arrow.up", color: ZFlyPalette.upload)
                Divider().frame(height: 56)
                stat("活动连接", value: "\(counters?.items.count ?? 0)", unit: "", detail: Text("内存 ") + Text(bytes(counters?.memory)), icon: "arrow.left.arrow.right", color: .secondary)
            }
            Divider().opacity(0.55)
            HStack(spacing: 14) {
                Text("流量趋势").font(.system(size: 12, weight: .semibold))
                Spacer()
                legend("下载", color: accent.color)
                legend("上传", color: ZFlyPalette.upload)
                Text("最近 90 秒").font(.system(size: 10)).foregroundStyle(.tertiary)
            }
            Chart {
                ForEach(Array(samples.enumerated()), id: \.element.id) { index, item in
                    AreaMark(x: .value("时间", index), y: .value("下载", item.download))
                        .foregroundStyle(LinearGradient(colors: [accent.color.opacity(0.14), accent.color.opacity(0.01)], startPoint: .top, endPoint: .bottom))
                        .interpolationMethod(.monotone)
                    LineMark(x: .value("时间", index), y: .value("速度", item.download), series: .value("方向", "下载"))
                        .foregroundStyle(accent.color).lineStyle(StrokeStyle(lineWidth: 1.8)).interpolationMethod(.monotone)
                    LineMark(x: .value("时间", index), y: .value("速度", item.upload), series: .value("方向", "上传"))
                        .foregroundStyle(ZFlyPalette.upload).lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 4])).interpolationMethod(.monotone)
                }
            }
            .chartXAxis(.hidden)
            .chartYScale(domain: .automatic(includesZero: true))
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in
                    AxisGridLine().foregroundStyle(.primary.opacity(0.055))
                    AxisValueLabel {
                        if let rate = value.as(Double.self) { Text(bytes(Int64(rate)) + "/s").font(.system(size: 9)).foregroundStyle(.secondary) }
                    }
                }
            }
            .frame(height: 116)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.35), value: samples.count)
            .overlay {
                if samples.isEmpty {
                    Text("启动后开始采样").font(.caption).foregroundStyle(.secondary)
                        .padding(10).background(.regularMaterial, in: Capsule())
                }
            }
            .accessibilityLabel("流量趋势")
        }.padding(ZFlyMetrics.panelPadding).modifier(SurfacePanel())
        .onChange(of: active ? store.traffic : nil, initial: true) { _, value in
            if active { samples = value ?? [] }
        }
        .onChange(of: active ? store.snapshot?.connections : nil, initial: true) { _, value in
            if active { counters = value }
        }
    }

    private func stat(_ title: LocalizedStringKey, value: String, unit: String, detail: Text, icon: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: icon).font(.system(size: 11, weight: .medium)).foregroundStyle(color)
            (Text(value).font(.system(size: 25, weight: .semibold, design: .rounded)) + Text(unit).font(.system(size: 13, weight: .medium)))
                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
                .contentTransition(.numericText())
            detail.font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func legend(_ title: LocalizedStringKey, color: Color) -> some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 5, height: 5)
            Text(title).foregroundStyle(.secondary)
        }.font(.system(size: 10))
    }
}

struct StatusMenu: View {
    @Environment(Store.self) private var store
    var openMainWindow: () -> Void

    private var running: Bool { store.management?.running == true }
    private var statusIcon: String {
        if store.starting { return "arrow.triangle.2.circlepath" }
        return running ? "checkmark.circle.fill" : "pause.circle"
    }
    private var statusColor: Color {
        guard running else { return .secondary }
        return store.connected ? .green : .orange
    }
    private var connectionTitle: LocalizedStringKey {
        if store.starting { return "正在启动…" }
        guard store.management != nil else { return "尚未连接核心" }
        if running && !store.connected { return "正在等待核心连接" }
        return running ? "代理正在运行" : "代理已停止"
    }
    private var actionTitle: LocalizedStringKey {
        store.management == nil ? "重试启动" : (running ? "停止代理" : "启动代理")
    }
    private var actionIcon: String {
        store.management == nil ? "arrow.clockwise" : (running ? "stop.fill" : "paperplane.fill")
    }
    private var selectedMode: Binding<String> {
        Binding(
            get: { store.management?.network.mode ?? "rule" },
            set: { mode in
                Task {
                    do { try await store.setMode(mode) }
                    catch { store.actionError = error.localizedDescription }
                }
            }
        )
    }
    private var systemProxy: Binding<Bool> {
        Binding(
            get: { store.management?.network.systemProxy ?? false },
            set: { enabled in Task { await store.setSystemProxy(enabled) } }
        )
    }
    private var profileSelection: Binding<String?> {
        Binding(
            get: { store.management?.currentProfileID },
            set: { id in
                guard let id, id != store.management?.currentProfileID else { return }
                var command = ManagementCommand("profile.select")
                command.id = id
                Task {
                    do {
                        _ = try await store.perform(command)
                        await store.refresh()
                    } catch {
                        store.actionError = error.localizedDescription
                    }
                }
            }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            primaryAction
            controls
                .padding(14)
                .modifier(StatusMenuSurface())
            traffic

            if let error = store.actionError {
                HStack(alignment: .top) {
                    Label {
                        Text(verbatim: error).fixedSize(horizontal: false, vertical: true)
                    } icon: {
                        Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.red)
                    }
                    Spacer(minLength: 0)
                    Button("关闭", systemImage: "xmark") { store.actionError = nil }
                        .labelStyle(.iconOnly)
                        .modifier(StatusMenuButton())
                        .help("关闭")
                }
                .font(.callout)
            }

            Divider()
            HStack {
                Button("打开 ZFly") {
                    if NSApp.activationPolicy() != .regular { NSApp.setActivationPolicy(.regular) }
                    openMainWindow()
                    NSApp.activate(ignoringOtherApps: true)
                }
                Spacer()
                Button("退出 ZFly", systemImage: "power") { NSApp.terminate(nil) }
                    .labelStyle(.iconOnly)
                    .help("退出 ZFly")
            }
            .modifier(StatusMenuButton())
        }
        .padding(16)
        .frame(width: 280)
        .onAppear {
            store.startPolling()
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 8) {
                    Text("ZFly").font(.title3.weight(.semibold))
                    if store.preview {
                        Text("演示模式").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Label {
                    Text(connectionTitle).foregroundStyle(.secondary)
                } icon: {
                    Image(systemName: statusIcon).foregroundStyle(statusColor)
                }
                .font(.callout)
                if store.management == nil && !store.starting {
                    Text(verbatim: store.managementStatus)
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer(minLength: 0)
            Button("刷新核心状态", systemImage: "arrow.clockwise") {
                Task { await store.refreshStatus() }
            }
            .labelStyle(.iconOnly)
            .modifier(StatusMenuButton())
            .help("刷新核心状态")
            .disabled(store.isBusy || store.refreshingStatus)
        }
    }

    private var primaryAction: some View {
        Button {
            Task { await store.toggleRunning() }
        } label: {
            HStack(spacing: 8) {
                if store.isBusy { ProgressView().controlSize(.small) }
                Label(actionTitle, systemImage: actionIcon)
            }
            .frame(maxWidth: .infinity)
        }
        .modifier(StatusMenuButton(prominent: !running))
        .controlSize(.large)
        .disabled(store.isBusy || (store.management != nil && store.management?.currentProfileID == nil))
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 14) {
            LabeledContent("当前配置") {
                Picker("当前配置", selection: profileSelection) {
                    if store.management?.currentProfileID == nil {
                        Text("未选择配置").tag(nil as String?)
                    }
                    ForEach(store.management?.profiles ?? []) { item in
                        Text(item.label).tag(Optional(item.id))
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .lineLimit(1)
                .frame(maxWidth: 180, alignment: .trailing)
                .disabled(store.isBusy || store.management?.profiles.isEmpty != false)
            }
            Divider()
            VStack(alignment: .leading, spacing: 8) {
                Text("路由模式").font(.callout).foregroundStyle(.secondary)
                Picker("路由模式", selection: selectedMode) {
                    Text("规则").tag("rule")
                    Text("全局").tag("global")
                    Text("直连").tag("direct")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .disabled(store.isBusy || store.management == nil)
            }
            Divider()
            Toggle("系统代理", isOn: systemProxy)
                .toggleStyle(.switch)
                .controlSize(.small)
                .disabled(store.isBusy || store.management == nil)
        }
    }

    private var traffic: some View {
        HStack(spacing: 16) {
            trafficRate("实时下载", icon: "arrow.down", value: store.downloadRate)
            Divider().frame(height: 28)
            trafficRate("实时上传", icon: "arrow.up", value: store.uploadRate)
        }
        .padding(.horizontal, 6)
    }

    private func trafficRate(_ title: LocalizedStringKey, icon: String, value: Int64) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Label(title, systemImage: icon).font(.caption).foregroundStyle(.secondary)
            Text(bytes(value) + "/s")
                .font(.system(.body, weight: .medium)).monospacedDigit().lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Use the system's current glass rendering rather than painting a glass imitation.
private struct StatusMenuButton: ViewModifier {
    var prominent = false

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *) {
            if prominent { content.buttonStyle(.glassProminent) }
            else { content.buttonStyle(.glass).tint(nil) }
        } else {
            if prominent { content.buttonStyle(.borderedProminent) }
            else { content.buttonStyle(.bordered).tint(nil) }
        }
    }
}

/// Keep a definite inner radius; the system popover draws the outer contour.
private struct StatusMenuSurface: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    private var fill: Color {
        reduceTransparency ? Color(nsColor: .controlBackgroundColor) : Color.primary.opacity(0.035)
    }

    func body(content: Content) -> some View {
        content.background(fill, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}
