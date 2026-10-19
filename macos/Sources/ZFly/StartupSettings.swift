import ServiceManagement
import SwiftUI

@MainActor
private final class LoginItemController: ObservableObject {
    @Published private(set) var enabled = false
    @Published private(set) var needsApproval = false
    @Published var error: String?

    init() {
        refresh()
    }

    func refresh() {
        switch SMAppService.mainApp.status {
        case .enabled:
            enabled = true
            needsApproval = false
        case .requiresApproval:
            enabled = true
            needsApproval = true
        default:
            enabled = false
            needsApproval = false
        }
    }

    func setEnabled(_ value: Bool) {
        error = nil
        do {
            if value {
                try SMAppService.mainApp.register()
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            self.error = error.localizedDescription
        }
        refresh()
    }

    func openLoginItems() {
        SMAppService.openSystemSettingsLoginItems()
    }
}

struct StartupSettingsView: View {
    @Environment(Store.self) private var store
    @AppStorage("startup.startHidden") private var startHidden = false
    @StateObject private var loginItem = LoginItemController()

    private var loginBinding: Binding<Bool> {
        Binding(get: { loginItem.enabled }, set: { loginItem.setEnabled($0) })
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: ZFlyMetrics.sectionSpacing) {
                ContentSection("登录与窗口") {
                    VStack(spacing: 0) {
                        PreferenceRow("登录时启动 ZFly") {
                            Toggle("登录时启动 ZFly", isOn: loginBinding)
                                .toggleStyle(.switch).controlSize(.small)
                                .disabled(store.preview)
                        }
                        Divider().opacity(0.5)
                        PreferenceRow("启动时隐藏窗口") {
                            Toggle("启动时隐藏窗口", isOn: $startHidden)
                                .toggleStyle(.switch).controlSize(.small)
                                .disabled(store.preview)
                        }
                    }
                    Text("隐藏窗口后，ZFly 仍可从菜单栏打开。")
                        .font(.caption).foregroundStyle(.secondary)
                    if loginItem.needsApproval {
                        HStack {
                            Text("请在系统设置的登录项中允许 ZFly。")
                                .font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Button("打开登录项设置", action: loginItem.openLoginItems)
                                .disabled(store.preview)
                        }
                    }
                    if let error = loginItem.error {
                        Text(error).font(.caption).foregroundStyle(.red)
                    }
                }
            }
            .padding(.horizontal, ZFlyMetrics.pagePadding)
            .padding(.bottom, ZFlyMetrics.pagePadding)
        }
        .onAppear { loginItem.refresh() }
    }
}
