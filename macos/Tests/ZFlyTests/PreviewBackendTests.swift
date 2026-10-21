import Foundation
import XCTest
@testable import ZFly

extension XCTestCase {
    @MainActor func connectedPreviewStore(variant: String = "default") async throws -> Store {
        let runtime = NativeRuntime()
        var environment = ProcessInfo.processInfo.environment
        // This is an explicit test dependency, never application discovery.
        environment["ZFLY_DEV_PYTHON"] = "/usr/bin/python3"
        let repository = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        environment["ZFLY_DEV_RUNTIME"] = repository.appendingPathComponent("runtime").path
        let connection = try await runtime.start(preview: true, previewState: variant, environment: environment)
        print("Owned preview test runtime PID: \(runtime.process!.processIdentifier)")
        addTeardownBlock {
            await runtime.stop()
            XCTAssertFalse(FileManager.default.fileExists(atPath: connection.dataDirectory))
        }
        let store = Store(preview: true)
        try store.connect(endpoint: connection.endpoint, secret: connection.secret, managementEndpoint: connection.endpoint)
        await store.refreshManagement()
        await store.refresh()
        XCTAssertTrue(store.connected)
        XCTAssertNotNil(store.snapshot)
        return store
    }
}

final class PreviewBackendTests: XCTestCase, @unchecked Sendable {
    func testPreviewPreferencesStayInMemory() {
        let preferences = PreviewPreferences()
        preferences.set("dark", forKey: "appearance.scheme")
        preferences.set(false, forKey: "appearance.glass")
        XCTAssertEqual(preferences.string(forKey: "appearance.scheme"), "dark")
        XCTAssertFalse(preferences.bool(forKey: "appearance.glass"))
        XCTAssertNil(PreviewPreferences().object(forKey: "appearance.scheme"))
        XCTAssertTrue((preferences.persistentDomain(forName: preferences.domain) ?? [:]).isEmpty)
        preferences.removeObject(forKey: "appearance.scheme")
        XCTAssertNil(preferences.object(forKey: "appearance.scheme"))
    }

    @MainActor func testPreviewVariantsUseRuntimeAndIndependentDirectories() async throws {
        let empty = try await connectedPreviewStore(variant: "empty")
        let stopped = try await connectedPreviewStore(variant: "stopped")
        XCTAssertNotEqual(empty.endpoint, stopped.endpoint)
        XCTAssertTrue(empty.management!.profiles.isEmpty)
        XCTAssertFalse(empty.management!.running)
        XCTAssertEqual(stopped.management!.profiles.count, 2)
        XCTAssertFalse(stopped.management!.running)
        XCTAssertTrue(stopped.snapshot!.connections.items.isEmpty)
    }

    @MainActor func testDeveloperPreviewRequiresExplicitPythonAndRuntime() async {
        let runtime = NativeRuntime()
        do {
            _ = try await runtime.start(preview: true, environment: [:])
            XCTFail("unbundled preview guessed a Python interpreter")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("ZFLY_DEV_PYTHON"))
            XCTAssertTrue(error.localizedDescription.contains("ZFLY_DEV_RUNTIME"))
        }
        XCTAssertNil(runtime.process)
        do {
            _ = try await runtime.start(preview: true, environment: ["ZFLY_DEV_PYTHON": "/test/python"])
            XCTFail("accepted an incomplete developer override")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("同时设置"))
        }
        XCTAssertNil(runtime.process)
        do {
            _ = try await runtime.start(environment: ["ZFLY_DEV_PYTHON": "relative", "ZFLY_DEV_RUNTIME": "relative"])
            XCTFail("unbundled normal mode started")
        } catch {
            XCTAssertFalse(error.localizedDescription.contains("ZFLY_DEV_PYTHON"))
        }
        XCTAssertNil(runtime.process)
    }

    @MainActor func testRealPreviewMeasurementIsBusyAndCancels() async throws {
        let store = try await connectedPreviewStore()
        let task = Task { await store.measure(proxy: "东京 / Tokyo") }
        while !store.measuring.contains("东京 / Tokyo") { await Task.yield() }
        XCTAssertNil(store.delays["东京 / Tokyo"])
        XCTAssertFalse(store.isBusy)
        store.cancelMeasurement(proxy: "东京 / Tokyo")
        await task.value
        XCTAssertTrue(store.measuring.isEmpty)
        XCTAssertNil(store.delays["东京 / Tokyo"])
        await store.measure(proxy: "东京 / Tokyo")
        XCTAssertEqual(store.delays["东京 / Tokyo"], 42)
    }
}
