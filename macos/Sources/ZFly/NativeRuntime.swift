import Foundation
import AppKit
import Darwin

extension Notification.Name {
    static let zflyPrepareToQuit = Notification.Name("ZFlyPrepareToQuit")
}

@objc(ZFlyApplication)
final class ZFlyApplication: NSApplication {
    override func terminate(_ sender: Any?) {
        let sheets = windows.filter { $0.sheetParent != nil }
        @MainActor func editable(_ view: NSView) -> Bool {
            if let text = view as? NSTextView, text.isEditable { return true }
            if let field = view as? NSTextField, field.isEditable { return true }
            return view.subviews.contains(where: editable)
        }
        if sheets.contains(where: { $0.contentView.map(editable) ?? false }) {
            let alert = NSAlert()
            alert.messageText = "退出 ZFly？"
            alert.informativeText = "未保存的编辑将会丢失。"
            alert.addButton(withTitle: "退出")
            alert.addButton(withTitle: "取消")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
        }
        // AppKit defers termination while a SwiftUI sheet is attached. End the
        // sheets before invoking the regular delegate/runtime shutdown path.
        NotificationCenter.default.post(name: .zflyPrepareToQuit, object: nil)
        if sheets.isEmpty { super.terminate(sender) }
        else {
            // Let AppKit finish unwinding its sheet session before terminating.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { self.finishTermination(sender) }
        }
    }

    private func finishTermination(_ sender: Any?) { super.terminate(sender) }
}

@MainActor
final class NativeRuntime {
    static let shared = NativeRuntime()
    private(set) var process: Process?
    private var sessionDirectory: URL?
    private var shutdownTask: Task<Void, Never>?

    struct Connection: Decodable {
        let endpoint: String
        let secret: String
        let dataDirectory: String
    }
    private struct Ready: Decodable {
        let endpoint: String?
        let secret: String?
        let dataDirectory: String?
        let error: String?
    }

    func start() async throws -> Connection {
        if let process, !process.isRunning {
            self.process = nil
            if let sessionDirectory { try? FileManager.default.removeItem(at: sessionDirectory) }
            sessionDirectory = nil
        }
        guard process == nil, shutdownTask == nil else { throw failure("ZFly 运行时正在启动，请稍候。") }
        guard let resources = Bundle.main.resourceURL else { throw failure("应用资源目录不存在。") }
        let python = resources.appendingPathComponent("python/bin/python3")
        let core = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/ZFlyCore")
        let runtime = resources.appendingPathComponent("runtime")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.isExecutableFile(atPath: core.path),
              FileManager.default.fileExists(atPath: runtime.path) else {
            throw failure("应用缺少内置核心或运行时，请运行 macos/build-app.sh 构建完整的 ZFly.app。")
        }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("zfly-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        sessionDirectory = directory
        var started = false
        defer {
            if !started {
                try? FileManager.default.removeItem(at: directory)
                sessionDirectory = nil
            }
        }
        let readyFile = directory.appendingPathComponent("ready.json")
        let child = Process()
        child.executableURL = python
        struct Manifest: Decodable { let coreKind: String }
        let manifestURL = resources.appendingPathComponent("runtime-manifest.json")
        let manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: manifestURL))
        child.arguments = ["-B", "-m", "zfly_runtime", "--core", core.path, "--core-kind", manifest.coreKind,
                           "--resources", resources.appendingPathComponent("geodata").path,
                           "--ready-file", readyFile.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = runtime.path
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment.removeValue(forKey: "PYTHONHOME")
        environment["SSL_CERT_FILE"] = resources.appendingPathComponent("python/cert.pem").path
        child.environment = environment
        child.standardOutput = FileHandle.nullDevice
        let errorFile = directory.appendingPathComponent("stderr.log")
        _ = FileManager.default.createFile(atPath: errorFile.path, contents: nil, attributes: [.posixPermissions: 0o600])
        let errors = try FileHandle(forWritingTo: errorFile)
        defer { try? errors.close() }
        child.standardError = errors
        child.standardInput = FileHandle.nullDevice
        try child.run()
        process = child
        started = true
        do {
            for _ in 0..<900 {
                if let data = try? Data(contentsOf: readyFile), let ready = try? JSONDecoder().decode(Ready.self, from: data) {
                    try? FileManager.default.removeItem(at: readyFile)
                    if let error = ready.error { throw failure(error) }
                    if let endpoint = ready.endpoint, let secret = ready.secret, let path = ready.dataDirectory {
                        return Connection(endpoint: endpoint, secret: secret, dataDirectory: path)
                    }
                    throw failure("ZFly 运行时返回了无效的启动信息。")
                }
                if !child.isRunning {
                    let diagnostic = (try? String(contentsOf: errorFile, encoding: .utf8)) ?? ""
                    throw failure("ZFly 内置运行时退出，未能启动核心。" + String(diagnostic.suffix(2000)))
                }
                try await Task.sleep(for: .milliseconds(100))
            }
            throw failure("ZFly 核心启动超时。请检查配置引用的远程资源是否可用。")
        } catch {
            await stop()
            throw error
        }
    }

    func stop() async {
        if let shutdownTask {
            await shutdownTask.value
            return
        }
        guard let child = process else { return }
        // Cleanup must survive cancellation of the startup/polling task. Share it
        // with the application delegate if quitting overlaps runtime startup.
        let task = Task {
            await Self.terminate(child)
            if !child.isRunning {
                process = nil
                if let sessionDirectory { try? FileManager.default.removeItem(at: sessionDirectory) }
                sessionDirectory = nil
            }
        }
        shutdownTask = task
        await task.value
        shutdownTask = nil
    }

    static func terminate(_ child: Process, grace: Duration = .seconds(3)) async {
        guard child.isRunning else { return }
        child.terminate()
        let deadline = ContinuousClock.now.advanced(by: grace)
        while child.isRunning && ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(50))
        }
        if child.isRunning { Darwin.kill(child.processIdentifier, SIGKILL) }
        while child.isRunning { try? await Task.sleep(for: .milliseconds(20)) }
    }

    private func failure(_ message: String) -> NSError {
        NSError(domain: "ZFly.Runtime", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var prepareForTermination: (() -> Void)?
    var reopenMainWindow: (() -> Void)?
    private var terminating = false
    private var suppressInitialWindow = false
    private let initializedWindows = NSHashTable<NSWindow>.weakObjects()

    func applicationWillFinishLaunching(_ notification: Notification) {
        suppressInitialWindow = !CommandLine.arguments.contains("--safe-mode")
            && UserDefaults.standard.bool(forKey: "startup.startHidden")
        if suppressInitialWindow { NSApp.setActivationPolicy(.accessory) }
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(windowWillClose(_:)), name: NSWindow.willCloseNotification, object: nil)
        center.addObserver(self, selector: #selector(windowDidOpen(_:)), name: NSWindow.didBecomeMainNotification, object: nil)
        center.addObserver(self, selector: #selector(windowDidOpen(_:)), name: NSWindow.didBecomeKeyNotification, object: nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if sender.activationPolicy() != .regular { sender.setActivationPolicy(.regular) }
        if !flag {
            if let window = sender.windows.first(where: isAppWindow) {
                if window.isMiniaturized { window.deminiaturize(nil) }
                window.makeKeyAndOrderFront(nil)
            } else { reopenMainWindow?() }
        }
        return true
    }

    @objc private func windowDidOpen(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, isAppWindow(window) else { return }
        let app = NSApplication.shared
        if suppressInitialWindow {
            guard notification.name == NSWindow.didBecomeKeyNotification else { return }
            suppressInitialWindow = false
            window.orderOut(nil)
            if app.activationPolicy() != .accessory { app.setActivationPolicy(.accessory) }
            return
        }
        if app.activationPolicy() != .regular { app.setActivationPolicy(.regular) }
        guard notification.name == NSWindow.didBecomeKeyNotification,
              !initializedWindows.contains(window) else { return }
        initializedWindows.add(window)
        // SwiftUI initially focuses the first sidebar button. Start with the window
        // focused instead; later Tab navigation and reactivation retain normal focus.
        DispatchQueue.main.async { [weak window] in
            guard let window, window.isKeyWindow else { return }
            window.makeFirstResponder(nil)
        }
    }

    @objc private func windowWillClose(_ notification: Notification) {
        guard let closing = notification.object as? NSWindow, isAppWindow(closing) else { return }
        let app = NSApplication.shared
        // willClose arrives before isVisible changes. A minimized window still needs its Dock entry.
        let hasWindow = app.windows.contains { window in
            window !== closing && isAppWindow(window) && (window.isVisible || window.isMiniaturized)
        }
        if !hasWindow, app.activationPolicy() != .accessory { app.setActivationPolicy(.accessory) }
    }

    private func isAppWindow(_ window: NSWindow) -> Bool {
        // MenuBarExtra menus and sheets must not keep the app in the Dock.
        window.styleMask.contains(.titled) && window.level == .normal && window.parent == nil && window.sheetParent == nil
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard CommandLine.arguments.contains("--safe-mode"),
              let index = CommandLine.arguments.firstIndex(of: "--render-preview"),
              CommandLine.arguments.indices.contains(index + 1) else { return }
        let output = URL(fileURLWithPath: CommandLine.arguments[index + 1])
        DispatchQueue.main.async {
            guard let size = PreviewOptions.value("--preview-size")?.split(separator: "x"), size.count == 2,
                  let width = Double(size[0]), let height = Double(size[1]),
                  (900...2400).contains(width), (640...1600).contains(height),
                  let window = NSApplication.shared.windows.first(where: { $0.isVisible && $0.contentView != nil }) else { return }
            window.setContentSize(NSSize(width: width, height: height))
            window.center()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            guard let view = NSApplication.shared.windows.first(where: { $0.title != "" && $0.isVisible })?.contentView,
                  let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
                NSApplication.shared.terminate(nil)
                return
            }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            if let data = bitmap.representation(using: .png, properties: [:]) {
                try? data.write(to: output, options: .atomic)
            }
            NSApplication.shared.terminate(nil)
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        prepareForTermination?()
        guard NativeRuntime.shared.process != nil else { return .terminateNow }
        guard !terminating else { return .terminateLater }
        terminating = true
        Task {
            await NativeRuntime.shared.stop()
            let stopped = NativeRuntime.shared.process == nil
            terminating = false
            sender.reply(toApplicationShouldTerminate: stopped)
            if !stopped {
                let alert = NSAlert()
                alert.messageText = "ZFly 仍在恢复系统代理"
                alert.informativeText = "运行时尚未退出。请稍后再次退出，或在系统网络设置中检查代理。"
                alert.runModal()
            }
        }
        return .terminateLater
    }
}
