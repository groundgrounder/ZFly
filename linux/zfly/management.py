import base64
import binascii
import copy
import hashlib
import json
from pathlib import Path
import urllib.parse
import uuid

from .controller import Controller, ControllerError
from .catalog_model import PreviewCatalog

WORKSPACE_SECTIONS = {
    "core": "持久化核心设置", "dns": "DNS 与 hosts", "tun": "TUN 与路由覆盖",
    "geo": "Geo 资源", "app": "启动与测试", "dav": "WebDAV 备份连接",
    "profileOverrides": "配置专属覆盖", "globalRules": "全局追加规则", "profileRules": "配置追加规则",
    "logs": "日志", "dnsQueries": "DNS 记录", "requests": "请求历史", "providers": "运行中的提供者", "resources": "资源清单",
}
PROFILE_SECTIONS = { "profileOverrides", "profileRules"}
READONLY_SECTIONS = {"logs", "dnsQueries", "requests", "providers", "resources"}


def preview_document(section):
    value = {
        "core": {"mixed-port": 7890, "mode": "rule", "allow-lan": False, "ipv6": False, "tcp-concurrent": True},
        "dns": {"dns": {"enable": True, "enhanced-mode": "fake-ip", "nameserver": ["https://dns.alidns.com/dns-query"]}, "dns-override-keys": [], "hosts": {}},
        "tun": {"tun": {"auto-route": True, "strict-route": False}},
        "geo": {"geo-auto-update": False, "geo-update-interval": 24, "geodata-loader": "memconservative", "geox-url": {}},
        "app": {"autoRun": False, "testUrl": "https://www.gstatic.com/generate_204", "checkCertificate": True},
        "dav": {"uri": "https://example.com/dav", "user": "", "fileName": "backup.zip", "password": None},
        "profileOverrides": {"dns": {}, "dns-override-keys": [], "ntp": {}, "sniffer": {}, "tun": {}, "proxy-providers": {}},
        "logs": [{"LogLevel": "info", "Payload": "演示日志：配置载入完成"}],
        "dnsQueries": [{"domain": "example.com", "type": "A", "answers": ["192.0.2.1"], "cached": True}],
    }.get(section, [])
    rules = section in ("globalRules", "profileRules")
    return {"section": section, "title": WORKSPACE_SECTIONS[section], "text": "DOMAIN-SUFFIX,example.com,DIRECT\nMATCH,DIRECT" if rules else json.dumps(value, ensure_ascii=False, indent=2), "revision": "preview", "writable": section not in READONLY_SECTIONS, "format": "rules" if rules else "json"}


def validate_network(network):
    if not isinstance(network, dict):
        raise ControllerError("网络设置需要 JSON 对象。")
    for key in ("tunEnabled", "systemProxy", "bypassPrivateRoute", "autoSetSystemDns", "appendSystemDns", "ipv6", "allowLan", "tcpConcurrent"):
        if type(network.get(key)) is not bool:
            raise ControllerError("网络开关值无效。")
    if type(network.get("mixedPort")) is not int or not 1 <= network["mixedPort"] <= 65535:
        raise ControllerError("混合端口应为 1–65535 的整数。")
    for key, choices in {"mode": ["rule", "global", "direct"], "logLevel": ["silent", "error", "warning", "info", "debug"], "tunStack": ["gvisor", "system", "mixed"]}.items():
        if network.get(key) not in choices:
            raise ControllerError("不支持的网络设置：" + key)
    device = network.get("tunDevice")
    if not isinstance(device, str) or not device or len(device) > 32 or any(not (c.isalnum() or c in "_.-") for c in device):
        raise ControllerError("TUN 设备名无效。")
    domains = network.get("bypassDomains")
    if not isinstance(domains, list) or any(not isinstance(item, str) or any(ord(c) < 32 for c in item) for item in domains):
        raise ControllerError("绕过地址应为字符串列表。")


def _valid_http_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
            and not parsed.username and not parsed.password)


def validate_profile_command(command, profile=None):
    action = command.get("action")
    if action in ("profile.add", "profile.import", "profile.create"):
        label = command.get("label", "")
        if not isinstance(label, str) or (label and not label.strip()):
            raise ControllerError("配置名称不能为空。")
        url = command.get("url", "")
        if not isinstance(url, str) or (action == "profile.add" and not _valid_http_url(url)):
            raise ControllerError("请输入有效的 HTTP / HTTPS 订阅地址。")
        if type(command.get("autoUpdate", action == "profile.add")) is not bool:
            raise ControllerError("自动更新开关无效。")
        hours = command.get("updateHours", 24)
        if type(hours) is not int or not 1 <= hours <= 8760:
            raise ControllerError("更新间隔应为 1–8760 小时。")
        if not isinstance(command.get("ageSecretKey", ""), str):
            raise ControllerError("Age 解密密钥必须是文本。")
        if action == "profile.import":
            try:
                if not isinstance(command.get("data"), str) or not base64.b64decode(command["data"], validate=True):
                    raise ValueError
            except (ValueError, TypeError, binascii.Error):
                raise ControllerError("配置文件编码无效。") from None
        return
    if action == "profile.edit" and profile is not None:
        if "label" in command and (not isinstance(command["label"], str) or not command["label"].strip()):
            raise ControllerError("配置名称不能为空。")
        if "url" in command and not isinstance(command["url"], str):
            raise ControllerError("配置地址必须是文本。")
        if "autoUpdate" in command and type(command["autoUpdate"]) is not bool:
            raise ControllerError("自动更新开关无效。")
        if "updateHours" in command and (type(command["updateHours"]) is not int or not 1 <= command["updateHours"] <= 8760):
            raise ControllerError("更新间隔应为 1–8760 小时。")
        if profile.get("type") == "url" and not _valid_http_url(command.get("url", profile.get("url"))):
            raise ControllerError("请输入有效的 HTTP / HTTPS 订阅地址。")


def validate_state(state):
    try:
        if state["apiVersion"] != 1:
            raise ControllerError("本地运行时版本不兼容，请重新安装完整的 ZFly。")
        validate_network(state["network"])
        if not isinstance(state["profiles"], list) or type(state["running"]) is not bool:
            raise ValueError
        for profile in state["profiles"]:
            for key in ("id", "label", "url", "type"):
                if not isinstance(profile[key], str):
                    raise ValueError
            if (not profile["id"] or not profile["label"].strip() or profile["type"] not in ("file", "custom", "url")
                    or type(profile.get("autoUpdate")) is not bool
                    or type(profile.get("updateHours")) is not int or not 1 <= profile["updateHours"] <= 8760
                    or (profile["type"] == "url" and not _valid_http_url(profile["url"]))):
                raise ValueError
        current = state.get("currentProfileID")
        if current is not None and (not isinstance(current, str) or current not in {p["id"] for p in state["profiles"]}):
            raise ValueError
        return state
    except (KeyError, TypeError, ValueError):
        raise ControllerError("本地运行时返回了无法识别的数据。") from None


class ManagementClient(Controller):
    def snapshot(self):
        return validate_state(self._request("GET", "state", management_errors=True))

    def perform(self, command):
        if command.get("action") == "network.save":
            validate_network(command["network"])
        elif isinstance(command.get("action"), str) and command["action"].startswith("profile."):
            validate_profile_command(command)
        result = self._request("POST", "action", body=command, timeout=240, management_errors=True)
        if not isinstance(result, dict):
            raise ControllerError("本地运行时返回了无法识别的数据。")
        validate_state(result.get("state"))
        return result


class PreviewManagement:
    def __init__(self):
        self._state = json.loads(Path(__file__).with_name("preview.json").read_text(encoding="utf-8"))["management"]
        for profile in self._state.get("profiles", []):
            profile["revision"] = "preview:" + profile["id"]
        self._files = {}
        self._documents = {}
        self._catalog = PreviewCatalog()

    def snapshot(self):
        return copy.deepcopy(self._state)

    def _file(self, identity):
        return self._files.get(identity, {"name": "演示配置.yaml", "data": base64.b64encode(b"mixed-port: 7890\nmode: rule\n").decode(), "revision": "preview"})

    @staticmethod
    def _decode_file(value):
        try:
            if not isinstance(value, str):
                raise ValueError
            data = base64.b64decode(value, validate=True)
            if not data or b"\x00" in data:
                raise ValueError
            data.decode("utf-8-sig")
        except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
            raise ControllerError("配置文件编码无效。") from None
        return value

    def perform(self, command):
        if not isinstance(command, dict) or not isinstance(command.get("action"), str):
            raise ControllerError("操作格式无效。")
        action = command["action"]
        state = self._state
        if action.startswith("catalog."):
            return self._catalog.perform(command, state)
        if action.startswith("workspace."):
            if action not in ("workspace.read", "workspace.write"):
                raise ControllerError("不支持的管理操作。")
            section = command.get("section")
            if section not in WORKSPACE_SECTIONS:
                raise ControllerError("不支持的分类。")
            if section in PROFILE_SECTIONS and not any(item["id"] == command.get("id") for item in state["profiles"]):
                raise ControllerError("请先选择配置。")
            key = (section, command.get("id") if section in PROFILE_SECTIONS else None)
            document = self._documents.get(key, preview_document(section))
            if action == "workspace.write":
                if not document["writable"] or command.get("revision") != document["revision"]:
                    raise ControllerError("内容只读或已修改，请重新载入。")
                if document["format"] == "json":
                    try:
                        value = json.loads(command["text"])
                        if not isinstance(value, dict):
                            raise ValueError()
                    except (ValueError, KeyError, TypeError):
                        raise ControllerError("请输入有效的 JSON 对象。") from None
                document = {**document, "text": command["text"], "revision": str(uuid.uuid4())}
                self._documents[key] = document
            return {"state": self.snapshot(), "document": copy.deepcopy(document)}
        profile = next((item for item in state["profiles"] if item["id"] == command.get("id")), None)
        if action.startswith("profile.") and action not in ("profile.add", "profile.import", "profile.create") and profile is None:
            raise ControllerError("配置已不存在，请刷新列表。")
        if action.startswith("profile."):
            validate_profile_command(command, profile)
        if action in ("profile.edit", "profile.delete") and command.get("revision") != profile.get("revision"):
            raise ControllerError("配置已被更新，请刷新列表后重试。")
        if action == "network.save":
            network = command.get("network")
            if command.get("expected") != state["network"]:
                raise ControllerError("网络设置已修改，请重新载入。")
            validate_network(network)
            state["network"] = copy.deepcopy(network)
        elif action == "running.set":
            if type(command.get("running")) is not bool:
                raise ControllerError("运行状态无效。")
            state["running"] = command["running"]
        elif action in ("profile.add", "profile.import", "profile.create"):
            identity = str(uuid.uuid4())
            profile = {"id": identity, "label": (command.get("label") or "演示配置").strip(),
                       "url": command.get("url", ""),
                       "type": "custom" if action == "profile.create" else ("url" if action == "profile.add" else "file"),
                       "autoUpdate": command.get("autoUpdate", action == "profile.add"),
                       "updateHours": command.get("updateHours", 24), "revision": str(uuid.uuid4())}
            state["profiles"].append(profile)
            if action == "profile.import":
                data = self._decode_file(command.get("data"))
                self._files[identity] = {"name": "演示配置.yaml", "data": data, "revision": hashlib.md5(base64.b64decode(data, validate=True)).hexdigest()}
        elif action == "profile.select":
            state["currentProfileID"] = profile["id"]
        elif action == "profile.delete":
            state["profiles"].remove(profile)
            self._files.pop(profile["id"], None)
            if state["currentProfileID"] == profile["id"]:
                state["currentProfileID"] = state["profiles"][0]["id"] if state["profiles"] else None
        elif action == "profile.edit":
            for key in ("label", "url", "autoUpdate", "updateHours"):
                if key in command:
                    profile[key] = command[key].strip() if key in ("label", "url") else command[key]
            profile["revision"] = str(uuid.uuid4())
        elif action == "profile.read":
            return {"state": self.snapshot(), "file": copy.deepcopy(self._file(profile["id"]))}
        elif action == "profile.write":
            if profile["type"] != "file" or self._file(profile["id"])["revision"] != command["revision"]:
                raise ControllerError("文件只读或已被修改，请重新打开。")
            data = self._decode_file(command.get("data"))
            self._files[profile["id"]] = {"name": "演示配置.yaml", "data": data, "revision": hashlib.md5(base64.b64decode(data, validate=True)).hexdigest()}
        elif action in ("proxy.select", "tool.run"):
            if action == "tool.run" and command.get("tool") not in ("apply", "restart", "gc", "closeConnections", "updateProfiles", "updateResources", "updateProvider", "backupLocal", "testDAV", "backupDAV", "restoreLocalProfiles", "restoreLocalAll", "restoreDAVProfiles", "restoreDAVAll"):
                raise ControllerError("不支持的工具操作。")
            return {"state": self.snapshot(), "message": "演示操作已完成。"}
        elif action != "profile.update":
            raise ControllerError("不支持的管理操作。")
        return {"state": self.snapshot()}
