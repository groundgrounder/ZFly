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
