import AppKit
import SwiftUI
import Charts
import ZFlyKit

struct DashboardView: View {
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
                    if store.preview { StatusBadge(title: "演示模式", color: ZFlyPalette.tint) }
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
                .font(.system(size: 24, weight: .medium)).foregroundStyle(ZFlyPalette.tint)
                .frame(width: 56, height: 56)
                .background(ZFlyPalette.tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
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
    @Environment(Store.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let active: Bool
    // Freeze the hidden chart without removing it, preserving the overview scroll position.
    @State private var samples: [TrafficSample] = []
    @State private var counters: ConnectionsSnapshot?

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 20) {
                stat("实时下载", value: bytes(Int64(samples.last?.download ?? 0)), unit: "/s", detail: Text("总计 ") + Text(bytes(counters?.downloadTotal)), icon: "arrow.down", color: ZFlyPalette.tint)
                Divider().frame(height: 56)
                stat("实时上传", value: bytes(Int64(samples.last?.upload ?? 0)), unit: "/s", detail: Text("总计 ") + Text(bytes(counters?.uploadTotal)), icon: "arrow.up", color: ZFlyPalette.upload)
                Divider().frame(height: 56)
                stat("活动连接", value: "\(counters?.items.count ?? 0)", unit: "", detail: Text("内存 ") + Text(bytes(counters?.memory)), icon: "arrow.left.arrow.right", color: .secondary)
            }
            Divider().opacity(0.55)
            HStack(spacing: 14) {
                Text("流量趋势").font(.system(size: 12, weight: .semibold))
                Spacer()
                legend("下载", color: ZFlyPalette.tint)
                legend("上传", color: ZFlyPalette.upload)
                Text("最近 90 秒").font(.system(size: 10)).foregroundStyle(.tertiary)
            }
            Chart {
                ForEach(Array(samples.enumerated()), id: \.element.id) { index, item in
                    AreaMark(x: .value("时间", index), y: .value("下载", item.download))
                        .foregroundStyle(LinearGradient(colors: [ZFlyPalette.tint.opacity(0.14), ZFlyPalette.tint.opacity(0.01)], startPoint: .top, endPoint: .bottom))
                        .interpolationMethod(.monotone)
                    LineMark(x: .value("时间", index), y: .value("速度", item.download), series: .value("方向", "下载"))
                        .foregroundStyle(ZFlyPalette.tint).lineStyle(StrokeStyle(lineWidth: 1.8)).interpolationMethod(.monotone)
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
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Text(verbatim: store.preview ? "ZFly · 演示模式" : "ZFly · " + (store.management?.coreStatus ?? "正在启动"))
        if let profile = store.management?.profiles.first(where: { $0.id == store.management?.currentProfileID }) { Text(profile.label) }
        Button(
            store.management?.running == true ? "停止代理" : "启动代理",
            systemImage: store.management?.running == true ? "paperplane" : "paperplane.fill"
        ) { Task { await store.toggleRunning() } }
            .disabled(store.isBusy || store.management?.currentProfileID == nil)
        Menu("路由模式") {
            ForEach([("rule", "规则"), ("global", "全局"), ("direct", "直连")], id: \.0) { mode, title in
                Button {
                    Task { do { try await store.setMode(mode) } catch { store.actionError = error.localizedDescription } }
                } label: {
                    Text(store.management?.network.mode == mode ? "✓ " : "") + Text(LocalizedStringKey(title))
                }
            }
        }.disabled(store.isBusy)
        Toggle("系统代理", isOn: Binding(get: { store.management?.network.systemProxy ?? false }, set: { enabled in Task { await store.setSystemProxy(enabled) } }))
            .disabled(store.isBusy || store.management == nil)
        if let profiles = store.management?.profiles, !profiles.isEmpty {
            Menu("配置") {
                ForEach(profiles) { profile in
                    Button((store.management?.currentProfileID == profile.id ? "✓ " : "") + profile.label) {
                        var command = ManagementCommand("profile.select"); command.id = profile.id
                        Task { do { _ = try await store.perform(command); await store.refresh() } catch { store.actionError = error.localizedDescription } }
                    }
                }
            }.disabled(store.isBusy)
        }
        Divider()
        Button("打开 ZFly") {
            if NSApp.activationPolicy() != .regular { NSApp.setActivationPolicy(.regular) }
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        Button("退出 ZFly") { NSApp.terminate(nil) }.keyboardShortcut("q")
    }
}
