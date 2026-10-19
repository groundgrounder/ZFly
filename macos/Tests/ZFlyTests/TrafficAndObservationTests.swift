import Foundation
import Observation
import XCTest
import ZFlyKit
@testable import ZFly

private final class ChangeFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var changed = false

    func mark() { lock.withLock { changed = true } }
    var value: Bool { lock.withLock { changed } }
}

private actor RecordingTransport: HTTPTransport {
    private(set) var paths: [String] = []

    func send(_ request: URLRequest) async throws -> (Data, Int) {
        paths.append(request.url?.path ?? "")
        return (Data(#"{"downloadTotal":4096,"uploadTotal":1024,"connections":[]}"#.utf8), 200)
    }
}

final class TrafficAndObservationTests: XCTestCase, @unchecked Sendable {
    func testEnglishTrafficUnitsAndBoundaries() {
        let cases: [(Int64?, String)] = [
            (nil, "0 B"), (0, "0 B"), (-1, "0 B"), (.min, "0 B"),
            (1, "1 B"), (1023, "1023 B"), (1024, "1 KB"), (1536, "1.5 KB"),
            (1_048_575, "1 MB"), (1_048_576, "1 MB"), (1_610_612_736, "1.5 GB"),
            (1_099_511_627_776, "1 TB"), (1_125_899_906_842_624, "1 PB"), (.max, "8 EB"),
        ]
        for (value, expected) in cases { XCTAssertEqual(bytes(value), expected) }
    }

    @MainActor func testIdenticalPollDoesNotInvalidateAnyCoreSection() throws {
        let fixture = try Snapshot.preview()
        let state = CoreState(fixture)
        let flag = ChangeFlag()
        withObservationTracking {
            _ = state.version
            _ = state.configs
            _ = state.proxies
            _ = state.connections
        } onChange: { flag.mark() }
        for _ in 0..<100 { state.apply(fixture) }
        XCTAssertFalse(flag.value)
    }

    @MainActor func testTrafficPollDoesNotInvalidateSettingsOrProxies() throws {
        let state = CoreState(try Snapshot.preview())
        let unrelated = ChangeFlag()
        let traffic = ChangeFlag()
        withObservationTracking {
            _ = state.version
            _ = state.configs
            _ = state.proxies
        } onChange: { unrelated.mark() }
        withObservationTracking { _ = state.connections } onChange: { traffic.mark() }
        for counter in 1...100 {
            state.apply(try snapshot(connections: ["downloadTotal": counter * 1024, "uploadTotal": counter * 64, "connections": []]))
        }
        XCTAssertFalse(unrelated.value)
        XCTAssertTrue(traffic.value)
        XCTAssertEqual(state.connections.downloadTotal, 102_400)
    }

    @MainActor func testConfigChangesStillNotifyAndApply() throws {
        var next = try Snapshot.preview()
        let state = CoreState(next)
        let flag = ChangeFlag()
        withObservationTracking { _ = state.configs } onChange: { flag.mark() }
        next.configs.mode = next.configs.mode == "direct" ? "rule" : "direct"
        state.apply(next)
        XCTAssertTrue(flag.value)
        XCTAssertEqual(state.configs, next.configs)
    }

    func testLivePollOnlyRequestsConnections() async throws {
        let transport = RecordingTransport()
        let controller = Controller(settings: try ControllerSettings(endpoint: "http://127.0.0.1:12345", secret: ""), transport: transport)
        let result = try await controller.connections()
        let paths = await transport.paths
        XCTAssertEqual(paths, ["/connections"])
        XCTAssertEqual(result.downloadTotal, 4096)
        XCTAssertEqual(result.uploadTotal, 1024)
    }

    @MainActor func testPreviewMutationsRetainObservationAndState() async throws {
        let store = Store(preview: true)
        let state = try XCTUnwrap(store.snapshot)
        let flag = ChangeFlag()
        withObservationTracking { _ = store.snapshot?.configs } onChange: { flag.mark() }
        var draft = state.configs
        draft.mode = draft.mode == "direct" ? "rule" : "direct"
        try await store.configure(draft, baseline: state.configs)
        XCTAssertTrue(flag.value)
        XCTAssertTrue(store.snapshot === state)
        XCTAssertEqual(state.configs.mode, draft.mode)
        let connection = try XCTUnwrap(state.connections.items.first)
        await store.close(connection: connection.id)
        XCTAssertFalse(state.connections.items.contains { $0.id == connection.id })
    }

    @MainActor func testDelayChangesDoNotInvalidateManagementViews() async throws {
        let store = Store(preview: true)
        let flag = ChangeFlag()
        withObservationTracking { _ = store.management } onChange: { flag.mark() }
        await store.measure(proxy: "test-proxy")
        XCTAssertNotNil(store.delays["test-proxy"])
        XCTAssertFalse(flag.value)
    }

    private func snapshot(connections: [String: Any]) throws -> Snapshot {
        let fixture = try Snapshot.preview()
        // Decode the public wire model, just as a real controller poll does.
        let object: [String: Any] = [
            "version": ["version": fixture.version.version],
            "configs": try JSONSerialization.jsonObject(with: JSONEncoder().encode(fixture.configs)),
            "proxies": ["proxies": try proxyObject()],
            "connections": connections,
        ]
        return try JSONDecoder().decode(Snapshot.self, from: JSONSerialization.data(withJSONObject: object))
    }

    private func proxyObject() throws -> Any {
        // The preview is packaged into ZFlyKit; no user's runtime or subscription data is read.
        let fixture = try Snapshot.preview()
        return fixture.proxies.proxies.mapValues { proxy -> [String: Any] in
            var object: [String: Any] = ["type": proxy.type]
            if let all = proxy.all { object["all"] = all }
            if let now = proxy.now { object["now"] = now }
            if let history = proxy.history { object["history"] = history.map { ["delay": $0.delay] } }
            return object
        }
    }
}
