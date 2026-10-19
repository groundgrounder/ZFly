import Observation
import ZFlyKit

/// Keep each core section observable independently so traffic cannot invalidate proxy/settings views.
@MainActor @Observable
final class CoreState {
    var version: VersionInfo
    var configs: CoreConfig
    var proxies: ProxyResponse
    var connections: ConnectionsSnapshot

    init(_ snapshot: Snapshot) {
        version = snapshot.version
        configs = snapshot.configs
        proxies = snapshot.proxies
        connections = snapshot.connections
    }

    func apply(_ snapshot: Snapshot) {
        if version != snapshot.version { version = snapshot.version }
        if configs != snapshot.configs { configs = snapshot.configs }
        if proxies != snapshot.proxies { proxies = snapshot.proxies }
        applyConnections(snapshot.connections)
    }

    func applyConnections(_ next: ConnectionsSnapshot) {
        if connections != next { connections = next }
    }
}
