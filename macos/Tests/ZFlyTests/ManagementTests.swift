import AppKit
import Foundation
import XCTest
@testable import ZFlyKit
@testable import ZFly

private actor StateTransport: HTTPTransport {
    var fail = false
    func setFailure(_ value: Bool) { fail = value }
    func send(_ request: URLRequest) async throws -> (Data, Int) {
        if fail { throw URLError(.timedOut) }
        let object = try JSONSerialization.jsonObject(with: Snapshot.previewData()) as! [String: Any]
        return (try JSONSerialization.data(withJSONObject: object["management"]!), 200)
    }
}

final class ManagementTests: XCTestCase, @unchecked Sendable {
    @MainActor func testReadOnlyTextRemainsSelectableAndLiteral() {
        let view = PlainTextEditor.textView(editable: false)
        XCTAssertFalse(view.isEditable)
        XCTAssertTrue(view.isSelectable)
        XCTAssertFalse(view.isAutomaticQuoteSubstitutionEnabled)
        XCTAssertFalse(view.isAutomaticDashSubstitutionEnabled)
        XCTAssertFalse(view.isAutomaticTextReplacementEnabled)
        let editor = PlainTextEditor.textView(editable: true)
        XCTAssertTrue(editor.isEditable)
        XCTAssertFalse(editor.isAutomaticQuoteSubstitutionEnabled)
    }

    @MainActor func testManagementTimeoutAndReconnectRetainLastState() async throws {
        let transport = StateTransport()
        let store = Store(preview: false)
        try store.connect(endpoint: "http://127.0.0.1:12345", secret: "", managementEndpoint: "http://127.0.0.1:12345", transport: transport)
        await store.refreshManagement()
        let state = try XCTUnwrap(store.management)
        await transport.setFailure(true)
        await store.refreshManagement()
        XCTAssertEqual(store.management, state)
        XCTAssertTrue(store.managementStale)
        try store.connect(endpoint: "http://127.0.0.1:12345", secret: "", managementEndpoint: "http://127.0.0.1:12345", transport: transport)
        XCTAssertEqual(store.management, state)
        await transport.setFailure(false)
        await store.refreshManagement()
        XCTAssertFalse(store.managementStale)
    }
}

private actor ProxyTransport: HTTPTransport {
    private(set) var configReads = 0
    func send(_ request: URLRequest) async throws -> (Data, Int) {
        let object = try JSONSerialization.jsonObject(with: Snapshot.previewData()) as! [String: Any]
        let path = request.url!.path
        if path.hasSuffix("/delay") {
            try await Task.sleep(for: .seconds(10))
            return (Data(#"{"delay":42}"#.utf8), 200)
        }
        if path == "/configs" {
            configReads += 1
            if configReads == 1 { try await Task.sleep(for: .milliseconds(150)) }
        }
        let value: Any = path == "/state" ? object["management"]! : path == "/action" ? ["state": object["management"]!] : object[String(path.dropFirst())]!
        return (try JSONSerialization.data(withJSONObject: value), 200)
    }
}

extension ManagementTests {
    func testProxyGroupsFollowGlobalOrderAndVisibility() throws {
        let data = Data(#"{"proxies":{"GLOBAL":{"type":"Selector","all":["Z","hidden","A","DIRECT"]},"A":{"type":"Selector","all":[]},"Z":{"type":"URLTest","all":[]},"hidden":{"type":"Selector","all":[],"hidden":true}}}"#.utf8)
        let proxies = try JSONDecoder().decode(ProxyResponse.self, from: data)
        XCTAssertEqual(proxies.groupNames(mode: "rule"), ["Z", "A"])
        XCTAssertEqual(proxies.groupNames(mode: "global"), ["Z", "A", "GLOBAL"])
    }

    @MainActor func testActionWaitsForPollAndRunsFullRefresh() async throws {
        let transport = ProxyTransport()
        let store = Store(preview: false)
        try store.connect(endpoint: "http://127.0.0.1:12345", secret: "", managementEndpoint: "http://127.0.0.1:12345", transport: transport)
        await store.refreshManagement()
        let poll = Task { await store.refresh() }
        while await transport.configReads == 0 { await Task.yield() }
        await store.select(group: "test", proxy: "DIRECT")
        await poll.value
        let reads = await transport.configReads
        XCTAssertEqual(reads, 2)
        XCTAssertNotNil(store.snapshot)
    }

    @MainActor func testLatencyDoesNotBlockActionsAndCancels() async throws {
        let transport = ProxyTransport()
        let store = Store(preview: false)
        try store.connect(endpoint: "http://127.0.0.1:12345", secret: "", managementEndpoint: "http://127.0.0.1:12345", transport: transport)
        let task = Task { await store.measureGroup(["node"], name: "group") }
        while !store.measuring.contains("node") { await Task.yield() }
        XCTAssertFalse(store.isBusy)
        XCTAssertTrue(store.measuringGroups.contains("group"))
        store.cancelMeasurement(group: "group")
        await task.value
        XCTAssertTrue(store.measuring.isEmpty)
        XCTAssertTrue(store.measuringGroups.isEmpty)
        XCTAssertNil(store.delays["node"])
    }
}
