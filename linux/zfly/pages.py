from enum import StrEnum


class Page(StrEnum):
    OVERVIEW = "overview"
    PROFILES = "profiles"
    PROXIES = "proxies"
    CONNECTIONS = "connections"
    CATALOG = "catalog"
    WORKSPACE = "workspace"
    TOOLS = "tools"
    SETTINGS = "settings"


PAGE_LABELS = {
    Page.OVERVIEW: "概览",
    Page.PROFILES: "配置与订阅",
    Page.PROXIES: "代理组",
    Page.CONNECTIONS: "连接",
    Page.CATALOG: "自定义资源",
    Page.WORKSPACE: "规则与高级",
    Page.TOOLS: "网络工具",
    Page.SETTINGS: "设置",
}

PAGE_ICONS = {
    Page.OVERVIEW: "view-grid-symbolic",
    Page.PROFILES: "document-new-symbolic",
    Page.PROXIES: "network-workgroup-symbolic",
    Page.CONNECTIONS: "network-transmit-receive-symbolic",
    Page.CATALOG: "folder-symbolic",
    Page.WORKSPACE: "text-x-generic-symbolic",
    Page.TOOLS: "applications-engineering-symbolic",
    Page.SETTINGS: "preferences-system-symbolic",
}
