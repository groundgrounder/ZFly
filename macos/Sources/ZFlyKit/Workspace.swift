import Foundation

public enum PreviewWorkspace {
    public static func document(_ section: WorkspaceSection) -> ManagedDocument {
        let value: String
        switch section {
        case .core: value = "{\n  \"mixed-port\": 7890,\n  \"mode\": \"rule\",\n  \"allow-lan\": false,\n  \"ipv6\": false,\n  \"tcp-concurrent\": true\n}"
        case .dns: value = "{\n  \"dns\": {\"enable\": true, \"enhanced-mode\": \"fake-ip\", \"nameserver\": [\"https://dns.alidns.com/dns-query\"]},\n  \"dns-override-keys\": [],\n  \"hosts\": {}\n}"
        case .tun: value = "{\n  \"tun\": {\"auto-route\": true, \"strict-route\": false}\n}"
        case .geo: value = "{\n  \"geo-auto-update\": false,\n  \"geo-update-interval\": 24,\n  \"geodata-loader\": \"memconservative\",\n  \"geox-url\": {}\n}"
        case .app: value = "{\n  \"autoRun\": false,\n  \"testUrl\": \"https://www.gstatic.com/generate_204\",\n  \"checkCertificate\": true,\n  \"userAgent\": \"\"\n}"
        case .dav: value = "{\"uri\":\"https://example.com/dav\",\"user\":\"\",\"fileName\":\"backup.zip\",\"password\":null}"
        case .profileOverrides: value = "{\n  \"dns\": {},\n  \"dns-override-keys\": [],\n  \"ntp\": {},\n  \"sniffer\": {},\n  \"tun\": {},\n  \"proxy-providers\": {}\n}"
        case .globalRules, .profileRules: value = "DOMAIN-SUFFIX,example.com,DIRECT\nMATCH,DIRECT"
        case .logs: value = "[{\"LogLevel\":\"info\", \"Payload\":\"演示日志：配置载入完成\"}]"
        case .dnsQueries: value = "[{\"domain\":\"example.com\", \"type\":\"A\", \"answers\":[\"192.0.2.1\"], \"cached\":true}]"
        default: value = "[]"
        }
        return ManagedDocument(section: section.rawValue, title: section.title, text: value,
                               revision: "preview", writable: section.writable,
                               format: [.globalRules, .profileRules].contains(section) ? "rules" : "json")
    }
}
