import base64
import binascii
import copy
from datetime import datetime, timezone
import gzip
import io
import json
import sys
from pathlib import Path
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from ._vendor import yaml
from .network import validate_network
from .storage import RuntimeErrorMessage, digest, encode_json

MAX_PROFILE = 16 * 1024 * 1024
NETWORK_KEYS = {"mode": "mode", "mixedPort": "mixed-port", "ipv6": "ipv6", "allowLan": "allow-lan",
                "tcpConcurrent": "tcp-concurrent", "logLevel": "log-level"}
TITLES = {"core": "核心设置", "dns": "DNS 与 hosts", "tun": "TUN 与路由覆盖", "geo": "Geo 资源",
          "app": "启动与测试", "dav": "WebDAV 备份连接", "profileOverrides": "配置专属覆盖",
          "globalRules": "全局追加规则", "profileRules": "配置追加规则", "logs": "日志",
          "dnsQueries": "DNS 记录", "requests": "请求历史", "providers": "运行中的提供者", "resources": "资源清单"}
READONLY = {"logs", "dnsQueries", "requests", "providers", "resources"}
PROFILE_SECTIONS = {"profileOverrides", "profileRules"}
DEFAULTS = {"core": {}, "dns": {"dns": {}, "hosts": {}}, "tun": {}, "geo": {},
            "profileOverrides": {}, "globalRules": "", "profileRules": "",
            "app": {"autoRun": False, "testUrl": "https://www.gstatic.com/generate_204", "checkCertificate": True},
            "dav": {"uri": "", "user": "", "password": "", "fileName": "zfly-backup.json"}}
FORBIDDEN = {"external-controller", "external-controller-tls", "external-controller-unix", "external-controller-pipe",
             "secret", "external-ui", "external-ui-url", "external-ui-name", "listeners", "redir-port", "tproxy-port",
             "routing-mark", "interface-name"}


class DownloadRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if (new.scheme not in ("http", "https") or not new.hostname or new.username or new.password
                or (old.scheme == "https" and new.scheme != "https")
                or (req.has_header("Authorization") and (old.scheme, old.netloc) != (new.scheme, new.netloc))):
            raise RuntimeErrorMessage("下载重定向不安全，请使用服务商提供的最终 HTTPS 地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_http_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
    except (TypeError, ValueError):
        raise RuntimeErrorMessage("请输入有效的 HTTP / HTTPS 地址；认证订阅请使用服务商提供的链接。") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeErrorMessage("请输入有效的 HTTP / HTTPS 地址；认证订阅请使用服务商提供的链接。")
    return parsed


def validate_profile_command(command, action, profile=None):
    """Validate native management input before it can reach downloads or state."""
    if action in ("profile.add", "profile.import", "profile.create"):
        label = command.get("label", "未命名配置")
        if not isinstance(label, str):
            raise RuntimeErrorMessage("配置名称必须是文本。")
        if label and not label.strip():
            raise RuntimeErrorMessage("配置名称不能为空。")
        url = command.get("url", "")
        if not isinstance(url, str):
            raise RuntimeErrorMessage("配置地址必须是文本。")
        if action == "profile.add":
            validate_http_url(url)
        if type(command.get("autoUpdate", action == "profile.add")) is not bool:
            raise RuntimeErrorMessage("自动更新开关无效。")
        hours = command.get("updateHours", 24)
        if type(hours) is not int or not 1 <= hours <= 8760:
            raise RuntimeErrorMessage("更新间隔应为 1–8760 小时。")
        if not isinstance(command.get("ageSecretKey", ""), str):
            raise RuntimeErrorMessage("Age 解密密钥必须是文本。")
        return
    if action != "profile.edit" or profile is None:
        return
    if "label" in command and (not isinstance(command["label"], str) or not command["label"].strip()):
        raise RuntimeErrorMessage("配置名称不能为空。")
    if "url" in command and not isinstance(command["url"], str):
        raise RuntimeErrorMessage("配置地址必须是文本。")
    if "autoUpdate" in command and type(command["autoUpdate"]) is not bool:
        raise RuntimeErrorMessage("自动更新开关无效。")
    if "updateHours" in command and (type(command["updateHours"]) is not int or not 1 <= command["updateHours"] <= 8760):
        raise RuntimeErrorMessage("更新间隔应为 1–8760 小时。")
    if profile["type"] == "url":
        validate_http_url(command.get("url", profile["url"]))


def decode_data(value):
    try:
        if not isinstance(value, str) or len(value) > MAX_PROFILE * 4 // 3 + 8:
            raise ValueError()
        data = base64.b64decode(value, validate=True)
        if len(data) > MAX_PROFILE:
            raise ValueError()
        return data
    except (ValueError, binascii.Error):
        raise RuntimeErrorMessage("文件编码无效或超过 16 MiB。") from None


def parse_profile(content):
    if not isinstance(content, str) or len(content.encode()) > MAX_PROFILE:
        raise RuntimeErrorMessage("配置超过 16 MiB。")
    try:
        # A modest alias limit avoids expanding hostile subscription YAML indefinitely.
        aliases = sum(isinstance(token, yaml.AliasToken) for token in yaml.scan(content))
        if aliases > 100:
            raise ValueError()
        value = yaml.safe_load(content)
        if not isinstance(value, dict) or not value or any(not isinstance(k, str) for k in value):
            raise ValueError()
        for key in ("proxies", "proxy-groups", "rules"):
            if key in value and not isinstance(value[key], list):
                raise ValueError()
        if any(not isinstance(line, str) for line in value.get("rules", [])):
            raise ValueError()
        for key in ("proxies", "proxy-groups"):
            if any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                   or not isinstance(item.get("type"), str) for item in value.get(key, [])):
                raise ValueError()
        for key in ("dns", "tun", "ntp", "hosts", "profile", "sniffer", "proxy-providers", "rule-providers"):
            if key in value and not isinstance(value[key], dict):
                raise ValueError()
        for key in ("proxy-providers", "rule-providers"):
            if any(not isinstance(name, str) or not isinstance(provider, dict)
                   or not isinstance(provider.get("path", ""), str)
                   for name, provider in value.get(key, {}).items()):
                raise ValueError()
        # JSON serialization also rejects cycles, unsupported YAML types and excessive depth.
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, TypeError, yaml.YAMLError, RecursionError):
        raise RuntimeErrorMessage("这不是有效的 Clash / mihomo YAML 配置。请使用 Clash 订阅链接或 YAML 文件。") from None


def merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Service:
    def __init__(self, storage, core, system_proxy, fetcher=None):
        self.storage, self.core, self.system_proxy = storage, core, system_proxy
        self.data = storage.load()
        self.running = False
        self.last_error = None
        self.lock = threading.RLock()
        self.fetcher = fetcher or self._fetch
        self.proxy_signature = None
        self.update_errors = {}
        self.closing = False
        self.proxy_restore_pending = False

    def start(self):
        self.system_proxy.disable()  # Recover a lease left by a previous crash.
        self.core.start()
        try:
            self._apply(False)
            if self.data["workspace"].get("app", {}).get("autoRun") and self.data["currentProfileID"]:
                self._apply(True)
        except RuntimeErrorMessage as error:
            # Keep management available so a bad saved profile can be repaired.
            self.core.apply(self.core.base_config())
            self.system_proxy.disable()
            self.proxy_signature = None
            self.running = False
            self.last_error = str(error)

    def close(self):
        self.closing = True
        with self.lock:
            try:
                self.system_proxy.disable()
            finally:
                self.proxy_signature = None
                self.running = False
                self.core.close()
                self.storage.close()

    def check_health(self):
        # Downloads may hold the transaction lock. Never block parent-exit detection.
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self.closing or self.core.alive or not (self.running or self.proxy_signature or self.proxy_restore_pending):
                return
            self.running = False
            self.last_error = "核心意外退出，代理已停止。请在网络工具中重启核心。"
            try:
                self.system_proxy.disable()
            except Exception:
                self.proxy_restore_pending = True
                self.last_error = "核心意外退出；系统代理还原失败，正在重试。"
            else:
                self.proxy_signature = None
                self.proxy_restore_pending = False
        finally:
            self.lock.release()

    def state(self):
        with self.lock:
            alive = self.core.alive
            return {"apiVersion": 1, "currentProfileID": self.data["currentProfileID"],
                    "running": self.running and alive,
                    "coreStatus": self.last_error or ("运行中" if self.running and alive else ("已就绪 · 已停止代理" if alive else "核心已退出")),
                    "tunAuthorization": "已启用 TUN" if self.running and self.data["network"]["tunEnabled"] else ("macOS 权限助手尚未提供；可使用系统代理" if sys.platform == "darwin" else "TUN 需要核心具备 CAP_NET_ADMIN 权限；GNOME 可使用系统代理"),
                    "profiles": [{**{k: v for k, v in profile.items() if k not in ("content", "ageSecretKey", "etag", "lastModified")}, "revision": digest(encode_json(profile))}
                                 for profile in self.data["profiles"]],
                    "network": copy.deepcopy(self.data["network"]), "dataDirectory": str(self.storage.directory),
                    "updateErrors": copy.deepcopy(self.update_errors)}

    def profile(self, identity):
        item = next((p for p in self.data["profiles"] if p["id"] == identity), None)
        if not item:
            raise RuntimeErrorMessage("配置已不存在，请刷新列表。")
        return item

    def _fetch(self, url, headers=None, method="GET", data=None):
        validate_http_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "ZFly/0.2 (Clash compatible)", **(headers or {})}, data=data, method=method)
        # Follow the user's environment for initial downloads; once active, use ZFly's own proxy.
        proxies = {"http": "http://127.0.0.1:" + str(self.data["network"]["mixedPort"]),
                   "https": "http://127.0.0.1:" + str(self.data["network"]["mixedPort"])} if self.running else None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), DownloadRedirects())
        try:
            with opener.open(request, timeout=40) as response:
                body = response.read(MAX_PROFILE + 1)
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                        body = compressed.read(MAX_PROFILE + 1)
                if len(body) > MAX_PROFILE:
                    raise RuntimeErrorMessage("下载文件超过 16 MiB。")
                return body, dict(response.headers), response.status
        except urllib.error.HTTPError as error:
            if error.code == 304:
                return b"", dict(error.headers), 304
            raise RuntimeErrorMessage("下载失败（HTTP " + str(error.code) + "），请检查订阅有效期和服务商状态。") from None
        except (OSError, urllib.error.URLError, ValueError):
            # Exception strings can contain credential-bearing subscription URLs.
            raise RuntimeErrorMessage("无法下载配置，请检查网络、订阅地址和 HTTPS 证书。") from None

    def _download(self, profile):
        headers = {}
        if profile.get("etag"):
            headers["If-None-Match"] = profile["etag"]
        if profile.get("lastModified"):
            headers["If-Modified-Since"] = profile["lastModified"]
        body, response, status = self.fetcher(profile["url"], headers)
        profile["updated"] = now()
        if status == 304:
            if not profile.get("content"):
                raise RuntimeErrorMessage("订阅没有返回配置内容，请重试。")
            return
        if profile.get("ageSecretKey"):
            content = self.core.decrypt(body, profile["ageSecretKey"])
        else:
            try:
                content = body.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise RuntimeErrorMessage("订阅不是 UTF-8 YAML；如果使用 Age 加密，请填写解密密钥。") from None
        parse_profile(content)
        self.core.validate(content)
        profile["content"] = content
        response = {k.lower(): v for k, v in response.items()}
        profile["etag"] = response.get("etag")
        profile["lastModified"] = response.get("last-modified")
        info = response.get("subscription-userinfo", "")
        for key in ("upload", "download", "total", "expire"):
            match = re.search(r"(?:^|;)\s*" + key + r"=(\d+)", info)
            profile[key] = min(int(match.group(1)), 2**62 - 1) if match else None

    def _config(self, running):
        identity = self.data["currentProfileID"]
        config = parse_profile(self.profile(identity)["content"]) if identity else {"rules": ["MATCH,DIRECT"]}
        workspace = self.data["workspace"]
        for section in ("core", "dns", "geo", "tun"):
            config = merge(config, workspace.get(section, {}))
        if identity:
            config = merge(config, workspace.get("profileOverrides:" + identity, {}))
        if identity and self.profile(identity)["type"] == "custom":
            config["proxies"] = [copy.deepcopy(item["value"]) for item in self.data["catalog"].get("proxies:", {}).values()]
            config["proxy-groups"] = [copy.deepcopy(item["value"]) for item in self.data["catalog"].get("groups:" + identity, {}).values()]
        parse_profile(encode_json(config))
        # Older native settings used the controller API spelling rather than YAML.
        if "sniffing" in config:
            if type(config["sniffing"]) is not bool:
                raise RuntimeErrorMessage("域名嗅探开关需要布尔值。")
            config["sniffer"] = merge(config.get("sniffer", {}), {"enable": config.pop("sniffing")})
        rules = []
        for section in ("globalRules", "profileRules:" + str(identity)):
            rules.extend(line.strip() for line in workspace.get(section, "").splitlines() if line.strip() and not line.strip().startswith("#"))
        config["rules"] = rules + config.get("rules", ["MATCH,DIRECT"])
        for key in FORBIDDEN:
            config.pop(key, None)
        network = self.data["network"]
        for key, target in NETWORK_KEYS.items():
            config[target] = network[key]
        config["external-controller"] = self.core.endpoint.removeprefix("http://")
        config["secret"] = self.core.secret
        config["external-controller-cors"] = {"allow-origins": [], "allow-private-network": False}
        config["ntp"] = {**config.get("ntp", {}), "write-to-system": False}
        config["tun"] = {**config.get("tun", {}), "enable": running and network["tunEnabled"],
                         "device": network["tunDevice"], "stack": network["tunStack"], "auto-route": True,
                         "route-exclude-address": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"] if network["bypassPrivateRoute"] else []}
        # A downloaded profile cannot open additional inbound servers or a system DNS port.
        for key in ("port", "socks-port", "redir-port", "tproxy-port"):
            config[key] = workspace.get("core", {}).get(key, 0) if running and key in ("port", "socks-port") else 0
        if not running:
            config["mixed-port"] = 0
        config["dns"] = {**config.get("dns", {}), "listen": ""}
        config.pop("ss-config", None)
        config.pop("vmess-config", None)
        config.pop("tuic-server", None)
        config["profile"] = {"store-selected": False, "store-fake-ip": True}
        # Provider caches stay in this app's home and separate profile scope.
        for kind in ("proxy-providers", "rule-providers"):
            for name, provider in config.get(kind, {}).items():
                if isinstance(provider, dict) and provider.get("type") == "http":
                    provider["path"] = "providers/" + digest(str(identity) + kind + name) + ".cache"
                elif isinstance(provider, dict) and provider.get("type") == "file":
                    path = (self.core.home / provider.get("path", "")).resolve()
                    if not path.is_relative_to(self.core.home.resolve()):
                        raise RuntimeErrorMessage("本地提供者路径必须位于 ZFly 的核心数据目录内。")
        return config

    def _preflight_ports(self, config):
        previous = self.core.last_config or {}
        ports = [config.get(key, 0) for key in ("mixed-port", "port", "socks-port") if config.get(key, 0)]
        if len(set(ports)) != len(ports):
            raise RuntimeErrorMessage("混合、HTTP 和 SOCKS 端口不能重复。")
        for key in ("mixed-port", "port", "socks-port"):
            port = config.get(key, 0)
            if not port or (self.running and previous.get(key) == port):
                continue
            try:
                with socket.socket() as check:
                    # Match the core's TCP listener: recently closed connections
                    # must not make a stopped proxy look like another program.
                    check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    check.bind(("0.0.0.0" if config.get("allow-lan") else "127.0.0.1", port))
                    # A reusable bind alone can succeed beside a BSD listener.
                    # Never enable SO_REUSEPORT; a live listener must still fail.
                    check.listen(1)
            except OSError:
                raise RuntimeErrorMessage("端口 " + str(port) + " 已被其他程序占用。请关闭原代理或在网络接管设置中更换端口。") from None

    def _apply(self, running):
        if running and not self.data["currentProfileID"]:
            raise RuntimeErrorMessage("请先添加订阅或导入 YAML 配置。")
        config = self._config(running)
        preferences = self.data["workspace"].get("app", DEFAULTS["app"])
        self.core.test_url = preferences.get("testUrl", DEFAULTS["app"]["testUrl"])
        self.core.check_certificate = preferences.get("checkCertificate", True)
        if running:
            validate_network(self.data["network"])
            self._preflight_ports(config)
        # Core.apply is fallible; the caller restores the previous config on failure.
        self.core.apply(config, self.data["selections"].get(self.data["currentProfileID"], {}), running)
        if running:
            for key in ("mixed-port", "port", "socks-port"):
                if config.get(key):
                    try:
                        with socket.create_connection(("127.0.0.1", config[key]), timeout=2):
                            pass
                    except OSError:
                        raise RuntimeErrorMessage("核心未能监听端口 " + str(config[key]) + "，启动已回滚。") from None
            if config["tun"]["enable"] and not any(name.startswith(config["tun"]["device"]) for _, name in socket.if_nameindex()):
                raise RuntimeErrorMessage("TUN 设备未建立，请检查核心权限。")
        signature = (config["mixed-port"], tuple(self.data["network"]["bypassDomains"])) if running and self.data["network"]["systemProxy"] else None
        if signature != self.proxy_signature:
            if signature:
                self.system_proxy.enable(signature[0], list(signature[1]))
            else:
                self.system_proxy.disable()
            self.proxy_signature = signature
        self.running = running
        self.proxy_restore_pending = False
        self.last_error = None

    def perform(self, command):
        if not isinstance(command, dict) or not isinstance(command.get("action"), str):
            raise RuntimeErrorMessage("操作格式无效。")
        with self.lock:
            if self.closing:
                raise RuntimeErrorMessage("ZFly 正在退出，请稍候。")
            old, was_running = copy.deepcopy(self.data), self.running
            applied = False
            selection = None
            try:
                result, reapply, persist = self._command(command)
                selection = result.pop("selection", None)
                if selection:
                    self.core.request("PUT", "/proxies/" + urllib.parse.quote(selection["group"], safe=""), json.dumps({"name": selection["proxy"]}).encode())
                if reapply:
                    applied = True
                    self._apply(result.pop("running", self.running))
                if persist:
                    self.storage.save(self.data)
                return {"state": self.state(), **result}
            except Exception:
                self.data = old
                if selection and selection["previous"]:
                    try:
                        self.core.request("PUT", "/proxies/" + urllib.parse.quote(selection["group"], safe=""), json.dumps({"name": selection["previous"]}).encode())
                    except RuntimeErrorMessage:
                        self.last_error = "节点选择保存失败，核心选择还原失败。请重新应用配置。"
                if applied:
                    try:
                        self._apply(was_running)
                    except Exception:
                        self.running = False
                        self.last_error = "配置应用及回滚失败，请重启核心；已保留原配置。"
                        self.system_proxy.disable()
                        self.proxy_signature = None
                raise

    def _command(self, c):
        action = c["action"]
        if action.startswith("profile."):
            return self._profile_command(c)
        if action == "network.save":
            if c.get("expected") != self.data["network"]:
                raise RuntimeErrorMessage("网络设置已被修改，请重新载入。")
            validate_network(c.get("network", {}))
            self.data["network"] = copy.deepcopy(c["network"])
            return {}, True, True
        if action == "running.set":
            if type(c.get("running")) is not bool:
                raise RuntimeErrorMessage("运行状态无效。")
            return {"running": c["running"]}, True, False
        if action == "proxy.select":
            group, proxy = c.get("group"), c.get("proxy")
            status, body = self.core.request("GET", "/proxies")
            item = json.loads(body).get("proxies", {}).get(group, {})
            if item.get("type") != "Selector" or proxy not in item.get("all", []):
                raise RuntimeErrorMessage("代理组或节点已变化，请刷新。")
            if not self.data["currentProfileID"]:
                raise RuntimeErrorMessage("请先选择配置。")
            self.data["selections"].setdefault(self.data["currentProfileID"], {})[group] = proxy
            return {"selection": {"group": group, "proxy": proxy, "previous": item.get("now")}}, False, True
        if action.startswith("workspace."):
            return self._workspace(c)
        if action.startswith("catalog."):
            return self._catalog(c)
        if action == "tool.run":
            return self._tool(c)
        raise RuntimeErrorMessage("不支持的操作。")

    def _profile_command(self, c):
        action = c["action"]
        if action in ("profile.add", "profile.import", "profile.create"):
            validate_profile_command(c, action)
            hours = c.get("updateHours", 24)
            profile = {"id": uuid.uuid4().hex, "label": (c.get("label") or "未命名配置").strip(),
                       "type": {"profile.add": "url", "profile.import": "file", "profile.create": "custom"}[action],
                       "url": c.get("url", ""), "autoUpdate": c.get("autoUpdate", action == "profile.add"), "updateHours": hours,
                       "updated": now(), "ageSecretKey": c.get("ageSecretKey", "")}
            if action == "profile.add":
                self._download(profile)
            else:
                try:
                    content = decode_data(c.get("data")).decode("utf-8-sig") if action == "profile.import" else "proxies: []\nproxy-groups: []\nrules:\n  - MATCH,DIRECT\n"
                except UnicodeDecodeError:
                    raise RuntimeErrorMessage("配置文件需要 UTF-8 编码。") from None
                parse_profile(content)
                self.core.validate(content)
                profile["content"] = content
            self.data["profiles"].append(profile)
            first = not self.data["currentProfileID"]
            if first:
                self.data["currentProfileID"] = profile["id"]
            return {"message": "配置已保存并选中。点击启动代理即可使用。" if first else "配置已保存。"}, first, True
        p = self.profile(c.get("id"))
        if action in ("profile.edit", "profile.delete") and c.get("revision") != digest(encode_json(p)):
            raise RuntimeErrorMessage("配置已被更新，请刷新列表后重试。")
        current = p["id"] == self.data["currentProfileID"]
        if action == "profile.select":
            self.data["currentProfileID"] = p["id"]
            return {}, True, True
        if action == "profile.update":
            if p["type"] != "url":
                raise RuntimeErrorMessage("只有订阅配置支持在线更新。")
            self._download(p)
            self.update_errors.pop(p["id"], None)
            return {"message": "订阅已更新。"}, current, True
        if action == "profile.edit":
            validate_profile_command(c, action, p)
            old_url = p["url"]
            for key in ("label", "url", "autoUpdate", "updateHours"):
                if key in c:
                    p[key] = c[key]
            if p["type"] == "url":
                validate_http_url(p["url"])
                if p["url"] != old_url:
                    p.pop("etag", None)
                    p.pop("lastModified", None)
            return {}, False, True
        if action == "profile.delete":
            self.data["profiles"].remove(p)
            self.data["selections"].pop(p["id"], None)
            for section in PROFILE_SECTIONS:
                self.data["workspace"].pop(section + ":" + p["id"], None)
            self.data["catalog"].pop("groups:" + p["id"], None)
            if current:
                self.data["currentProfileID"] = self.data["profiles"][0]["id"] if self.data["profiles"] else None
            return {"running": self.running and bool(self.data["profiles"])}, current, True
        if action == "profile.read":
            content = p["content"]
            if p["type"] == "custom":
                value = parse_profile(content)
                value["proxies"] = [copy.deepcopy(item["value"]) for item in self.data["catalog"].get("proxies:", {}).values()]
                value["proxy-groups"] = [copy.deepcopy(item["value"]) for item in self.data["catalog"].get("groups:" + p["id"], {}).values()]
                content = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
            return {"file": {"name": "zfly-profile.yaml", "data": base64.b64encode(content.encode()).decode(), "revision": digest(content)}}, False, False
        if action == "profile.write":
            if p["type"] != "file" or c.get("revision") != digest(p["content"]):
                raise RuntimeErrorMessage("配置只读或已被修改，请重新打开。")
            try:
                content = decode_data(c.get("data")).decode("utf-8-sig")
            except UnicodeDecodeError:
                raise RuntimeErrorMessage("配置文件需要 UTF-8 编码。") from None
            parse_profile(content)
            self.core.validate(content)
            p["content"], p["updated"] = content, now()
            return {}, current, True
        raise RuntimeErrorMessage("不支持的配置操作。")

    def _document(self, section, identity):
        if section not in TITLES:
            raise RuntimeErrorMessage("此版本尚未实现该设置分类。")
        if section in PROFILE_SECTIONS:
            self.profile(identity)
        key = section + ":" + identity if section in PROFILE_SECTIONS else section
        value = copy.deepcopy(self.data["workspace"].get(key, DEFAULTS.get(section, [])))
        if section == "core":
            value.update({target: self.data["network"][k] for k, target in NETWORK_KEYS.items()})
        if section in ("logs", "dnsQueries", "requests") and self.core.kind != "ipc":
            value = {"supported": False, "message": "当前标准 mihomo 驱动尚未接入实时历史；连接和 DNS 查询仍可在网络工具中查看。"}
        elif section == "logs":
            value = self.core.history("log")
        elif section == "dnsQueries":
            value = self.core.history("dns")
        elif section == "requests":
            value = self.core.history("request")
        elif section == "providers":
            value = json.loads(self.core.request("GET", "/providers/proxies")[1])
        elif section == "resources":
            value = [{"name": p.name, "size": p.stat().st_size} for p in self.core.home.iterdir() if p.suffix.lower() in (".dat", ".mmdb", ".metadb")]
        if section == "dav":
            value["password"] = None
        text = value if isinstance(value, str) else encode_json(value)
        # The core document includes network-owned fields such as mode and port.
        # Hash the rendered value so a network edit invalidates an older core draft.
        revision_source = (value if section == "core"
                           else self.data["workspace"].get(key, DEFAULTS.get(section, [])) if section == "dav"
                           else text)
        return key, {"section": section, "title": TITLES[section], "text": text,
                     "revision": digest(encode_json(revision_source)), "writable": section not in READONLY,
                     "format": "rules" if section.endswith("Rules") else "json"}

    def _workspace(self, c):
        section = c.get("section")
        key, doc = self._document(section, c.get("id"))
        if c["action"] == "workspace.read":
            return {"document": doc}, False, False
        if c["action"] != "workspace.write" or not doc["writable"] or doc["revision"] != c.get("revision"):
            raise RuntimeErrorMessage("内容只读或已被修改，请重新载入。")
        text = c.get("text")
        if not isinstance(text, str) or len(text.encode()) > MAX_PROFILE:
            raise RuntimeErrorMessage("设置文本无效或过大。")
        try:
            value = text if doc["format"] == "rules" else json.loads(text)
            if doc["format"] != "rules" and not isinstance(value, dict):
                raise ValueError()
        except (ValueError, TypeError):
            raise RuntimeErrorMessage("请输入有效的 JSON 对象。") from None
        if section == "core" and "sniffing" in value:
            if type(value["sniffing"]) is not bool or not isinstance(value.get("sniffer", {}), dict):
                raise RuntimeErrorMessage("域名嗅探开关需要布尔值。")
            value["sniffer"] = merge(value.get("sniffer", {}), {"enable": value.pop("sniffing")})
        if isinstance(value, dict) and FORBIDDEN.intersection(value):
            raise RuntimeErrorMessage("核心控制接口由 ZFly 自动管理，不能通过配置覆盖。")
        self._validate_workspace_value(section, value)
        if section == "core":
            for field, target in NETWORK_KEYS.items():
                if target in value:
                    self.data["network"][field] = value.pop(target)
            for port in ("port", "socks-port"):
                if port in value and (type(value[port]) is not int or not 0 <= value[port] <= 65535):
                    raise RuntimeErrorMessage("端口应为 0–65535 的整数。")
            validate_network(self.data["network"])
        if section == "tun" and value.get("tun", {}).get("enable") is not None:
            raise RuntimeErrorMessage("请在网络接管页启用 TUN。此处仅用于路由覆盖。")
        if section == "app":
            if set(value) - {"autoRun", "testUrl", "checkCertificate"} or type(value.get("autoRun")) is not bool or type(value.get("checkCertificate")) is not bool:
                raise RuntimeErrorMessage("启动偏好支持 autoRun、testUrl、checkCertificate。")
            if urllib.parse.urlsplit(value.get("testUrl", "")).scheme not in ("http", "https"):
                raise RuntimeErrorMessage("测试地址必须为 HTTP / HTTPS URL。")
        if section == "dav":
            if value.get("password") is None:
                value["password"] = self.data["workspace"].get("dav", {}).get("password", "")
        self.data["workspace"][key] = value
        return {"document": self._document(section, c.get("id"))[1]}, section != "dav", True

    @staticmethod
    def _validate_workspace_value(section, value):
        if section in ("globalRules", "profileRules"):
            if not isinstance(value, str):
                raise RuntimeErrorMessage("追加规则需要每行一条的文本。")
        elif not isinstance(value, dict):
            raise RuntimeErrorMessage("设置需要 JSON 对象。")
        elif section == "app":
            if (set(value) - {"autoRun", "testUrl", "checkCertificate"}
                    or type(value.get("autoRun")) is not bool or type(value.get("checkCertificate")) is not bool
                    or not isinstance(value.get("testUrl"), str)):
                raise RuntimeErrorMessage("启动偏好支持 autoRun、testUrl、checkCertificate。")
            parsed = urllib.parse.urlsplit(value["testUrl"])
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise RuntimeErrorMessage("测试地址必须为有效的 HTTP / HTTPS URL。")
        elif section == "dav":
            if set(value) - {"uri", "user", "password", "fileName"} or any(not isinstance(v, str) and not (k == "password" and v is None) for k, v in value.items()):
                raise RuntimeErrorMessage("WebDAV 连接字段需要文本。")
        else:
            parse_profile(encode_json(value or {"rules": []}))
            if FORBIDDEN.intersection(value):
                raise RuntimeErrorMessage("核心控制接口由 ZFly 自动管理，不能通过配置覆盖。")
            for port in ("port", "socks-port"):
                if port in value and (type(value[port]) is not int or not 0 <= value[port] <= 65535):
                    raise RuntimeErrorMessage("端口应为 0–65535 的整数。")

    def _catalog(self, c):
        kind, identity = c.get("section"), c.get("id")
        if kind not in ("proxies", "groups"):
            raise RuntimeErrorMessage("此版本尚未实现 JavaScript 配置脚本。可使用配置覆盖与追加规则。")
        if kind == "groups" and self.profile(c.get("name"))["type"] != "custom":
            raise RuntimeErrorMessage("请先选择自定义配置。")
        scope = kind + ":" + (c.get("name", "") if kind == "groups" else "")
        records = self.data["catalog"].setdefault(scope, {})
        if identity is not None and identity not in records:
            raise RuntimeErrorMessage("资源已被删除，请刷新。")
        template = {"name": "新节点", "type": "socks5", "server": "127.0.0.1", "port": 1080} if kind == "proxies" else {"name": "新代理组", "type": "select", "proxies": ["DIRECT"]}
        record = records.get(identity, {"value": template})
        revision = digest(encode_json(record if identity else records))
        if c["action"] == "catalog.read":
            return {"document": {"section": kind, "title": "自定义资源", "text": encode_json(record["value"]), "revision": revision, "writable": True, "format": "json"}}, False, False
        changed = c["action"] in ("catalog.save", "catalog.delete")
        if changed:
            if revision != c.get("revision"):
                raise RuntimeErrorMessage("资源已被修改，请重新载入。")
            if c["action"] == "catalog.delete":
                if identity is None:
                    raise RuntimeErrorMessage("请先选择资源。")
                old_name = records[identity]["value"]["name"]
                references = self._catalog_references(old_name, scope, identity)
                if references:
                    raise RuntimeErrorMessage("资源仍被引用，请先修改：" + "、".join(references[:5]))
                del records[identity]
                for profile_id, selections in self.data["selections"].items():
                    if kind == "groups" and profile_id == c.get("name"):
                        selections.pop(old_name, None)
                    for group, selected in list(selections.items()):
                        if selected == old_name:
                            selections.pop(group)
            else:
                try:
                    value = json.loads(c.get("text", ""))
                    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"].strip() or not isinstance(value.get("type"), str):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise RuntimeErrorMessage("资源需要有效的 JSON 对象、name 和 type。") from None
                if any(v["value"]["name"] == value["name"] for k, v in records.items() if k != identity):
                    raise RuntimeErrorMessage("资源名称已存在。")
                if identity and value["name"] != record["value"]["name"]:
                    self._rename_resource(record["value"]["name"], value["name"], scope)
                records[identity or uuid.uuid4().hex] = {"value": value}
                self._validate_catalog()
        elif c["action"] != "catalog.list":
            raise RuntimeErrorMessage("不支持的资源操作。")
        items = [{"id": k, "label": v["value"]["name"], "detail": v["value"]["type"]} for k, v in records.items()]
        active_custom = self.data["currentProfileID"] and self.profile(self.data["currentProfileID"])["type"] == "custom"
        return {"items": sorted(items, key=lambda x: x["label"])}, bool(changed and active_custom), changed

    @staticmethod
    def _references(value):
        members = value.get("proxies", [])
        if not isinstance(members, list) or any(not isinstance(x, str) for x in members):
            raise RuntimeErrorMessage("代理组的 proxies 必须是名称列表。")
        dialer = value.get("dialer-proxy")
        if dialer is not None and not isinstance(dialer, str):
            raise RuntimeErrorMessage("dialer-proxy 必须是节点名称。")
        return members + ([dialer] if dialer else [])

    def _resource_profiles(self, scope):
        return [p for p in self.data["profiles"] if p["type"] == "custom" and (scope == "proxies:" or scope == "groups:" + p["id"])]

    @staticmethod
    def _rule_target(line, old, new=None):
        if not isinstance(line, str):
            raise RuntimeErrorMessage("规则必须是文本。")
        fields = line.split(",")
        index = -2 if fields[-1].strip().lower() == "no-resolve" else -1
        if len(fields) > 1 and fields[index].strip() == old:
            if new is None:
                return True
            fields[index] = new
            return ",".join(fields)
        return False if new is None else line

    def _catalog_references(self, name, scope, identity):
        profiles = self._resource_profiles(scope)
        scopes = {"groups:" + p["id"] for p in profiles} | ({"proxies:"} if scope == "proxies:" else set())
        result = []
        for key in scopes:
            for item_id, item in self.data["catalog"].get(key, {}).items():
                if (key, item_id) != (scope, identity) and name in self._references(item["value"]):
                    result.append(item["value"]["name"])
        for profile in profiles:
            if any(self._rule_target(line, name) for line in parse_profile(profile["content"]).get("rules", [])):
                result.append(profile["label"] + "的规则")
            for key in ("globalRules", "profileRules:" + profile["id"]):
                if any(self._rule_target(line, name) for line in self.data["workspace"].get(key, "").splitlines()):
                    result.append(TITLES.get(key.split(":")[0], key))
            override = self.data["workspace"].get("profileOverrides:" + profile["id"], {})
            if self._override_references(override, name):
                result.append(profile["label"] + "的覆盖")
        return result

    def _override_references(self, value, name):
        return (any(self._rule_target(line, name) for line in value.get("rules", []))
                or any(name in self._references(item) for key in ("proxies", "proxy-groups") for item in value.get(key, [])))

    def _rename_resource(self, old, new, scope):
        profiles = self._resource_profiles(scope)
        scopes = {"groups:" + p["id"] for p in profiles} | ({"proxies:"} if scope == "proxies:" else set())
        for key in scopes:
            for item in self.data["catalog"].get(key, {}).values():
                value = item["value"]
                if "proxies" in value:
                    value["proxies"] = [new if x == old else x for x in value["proxies"]]
                if value.get("dialer-proxy") == old:
                    value["dialer-proxy"] = new
        for profile in profiles:
            value = parse_profile(profile["content"])
            value["rules"] = [self._rule_target(line, old, new) for line in value.get("rules", [])]
            profile["content"] = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
            selections = self.data["selections"].get(profile["id"], {})
            if scope.startswith("groups:") and old in selections:
                selections[new] = selections.pop(old)
            for group, selected in list(selections.items()):
                if selected == old:
                    selections[group] = new
            for key in ("globalRules", "profileRules:" + profile["id"]):
                if key in self.data["workspace"]:
                    self.data["workspace"][key] = "\n".join(self._rule_target(line, old, new) for line in self.data["workspace"][key].splitlines())
            override = self.data["workspace"].get("profileOverrides:" + profile["id"], {})
            if "rules" in override:
                override["rules"] = [self._rule_target(line, old, new) for line in override["rules"]]
            for key in ("proxies", "proxy-groups"):
                for item in override.get(key, []):
                    if "proxies" in item:
                        item["proxies"] = [new if x == old else x for x in item["proxies"]]
                    if item.get("dialer-proxy") == old:
                        item["dialer-proxy"] = new

    def _validate_catalog(self):
        builtins = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}
        proxies = [x["value"] for x in self.data["catalog"].get("proxies:", {}).values()]
        scopes = [proxies] + [proxies + [x["value"] for x in self.data["catalog"].get("groups:" + p["id"], {}).values()]
                              for p in self.data["profiles"] if p["type"] == "custom"]
        for items in scopes:
            names = [x["name"] for x in items]
            if len(set(names)) != len(names) or builtins.intersection(names) or any(x != x.strip() or any(ord(c) < 32 for c in x) for x in names):
                raise RuntimeErrorMessage("节点与代理组名称需要唯一，且不能使用 DIRECT、GLOBAL 等保留名称或首尾空格。")
            graph = {item["name"]: self._references(item) for item in items}
            visited, visiting = set(), set()
            def visit(name):
                if name in visiting:
                    raise RuntimeErrorMessage("代理引用形成循环：" + name)
                if name in visited or name in builtins:
                    return
                if name not in graph:
                    raise RuntimeErrorMessage("引用了不存在的节点或代理组：" + name)
                visiting.add(name)
                for dependency in graph[name]:
                    visit(dependency)
                visiting.remove(name)
                visited.add(name)
            for name in graph:
                visit(name)

    def _backup(self):
        return encode_json({"application": "ZFly", "version": 1, "created": now(), "state": self.data}).encode()

    def _dav(self, method, data=None):
        config = self.data["workspace"].get("dav", {})
        uri = config.get("uri", "")
        if urllib.parse.urlsplit(uri).scheme != "https":
            raise RuntimeErrorMessage("请先配置 HTTPS WebDAV 地址。")
        headers = {"Authorization": "Basic " + base64.b64encode((config.get("user", "") + ":" + (config.get("password") or "")).encode()).decode()}
        name = config.get("fileName") or "zfly-backup.json"
        url = uri.rstrip("/") + "/" + urllib.parse.quote(name, safe="")
        return self.fetcher(url, headers, method=method, data=data)

    def _restore(self, payload, all_settings):
        try:
            archive = json.loads(payload)
            if archive["application"] != "ZFly" or archive["version"] != 1:
                raise ValueError()
            state = archive["state"]
            if not isinstance(state, dict) or state["schema"] != 1 or not isinstance(state["profiles"], list):
                raise ValueError()
            identities = set()
            for p in state["profiles"]:
                if not isinstance(p["id"], str) or not p["id"] or p["id"] in identities:
                    raise ValueError()
                identities.add(p["id"])
                parse_profile(p["content"])
                if p["type"] not in ("file", "custom", "url") or not isinstance(p["label"], str):
                    raise ValueError()
                if (not isinstance(p["url"], str) or type(p["autoUpdate"]) is not bool
                        or type(p["updateHours"]) is not int or not 1 <= p["updateHours"] <= 8760
                        or not isinstance(p.get("ageSecretKey", ""), str)
                        or not isinstance(p.get("updated", ""), str)):
                    raise ValueError()
                if p["type"] == "url":
                    validate_http_url(p["url"])
                if any(type(p[k]) is not int or not 0 <= p[k] <= 2**62 - 1 for k in ("upload", "download", "total", "expire") if p.get(k) is not None):
                    raise ValueError()
            if state["currentProfileID"] is not None and state["currentProfileID"] not in identities:
                raise ValueError()
            for field in ("catalog", "workspace", "selections"):
                if not isinstance(state[field], dict):
                    raise ValueError()
            for key, value in state["workspace"].items():
                section, _, profile_id = key.partition(":")
                if section not in DEFAULTS or (section in PROFILE_SECTIONS and profile_id not in identities):
                    raise ValueError()
                self._validate_workspace_value(section, value)
            for key, records in state["catalog"].items():
                if key != "proxies:" and (not key.startswith("groups:") or key[7:] not in identities):
                    raise ValueError()
                if not isinstance(records, dict):
                    raise ValueError()
                for item_id, record in records.items():
                    if (not isinstance(item_id, str) or not isinstance(record, dict) or not isinstance(record.get("value"), dict)
                            or not isinstance(record["value"].get("name"), str) or not isinstance(record["value"].get("type"), str)):
                        raise ValueError()
            if any(key not in identities or not isinstance(value, dict)
                   or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items())
                   for key, value in state["selections"].items()):
                raise ValueError()
            validate_network(state["network"], require_tun_permission=False)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeErrorMessage):
            raise RuntimeErrorMessage("这不是受支持的 ZFly 备份文件。") from None
        if all_settings:
            self.data = state
        else:
            for key in ("profiles", "currentProfileID", "catalog", "selections"):
                self.data[key] = state[key]
            self.data["workspace"] = {k: v for k, v in self.data["workspace"].items() if not k.startswith(("profileOverrides:", "profileRules:"))}
            self.data["workspace"].update({k: v for k, v in state["workspace"].items() if k.startswith(("profileOverrides:", "profileRules:"))})
        self._validate_catalog()

    def _tool(self, c):
        tool = c.get("tool")
        if tool in ("apply", "restart"):
            if tool == "restart":
                was_running = self.running
                self.system_proxy.disable()
                self.proxy_signature = None
                self.running = False
                self.core.close()
                try:
                    self.core.start()
                except RuntimeErrorMessage as error:
                    self.last_error = str(error)
                    raise
                return {"message": "核心配置已应用。", "running": was_running}, True, False
            return {"message": "核心配置已应用。"}, True, False
        if tool == "gc":
            if self.core.kind != "ipc":
                raise RuntimeErrorMessage("此核心不提供手动内存回收。")
            self.core.call("forceGc")
        elif tool == "closeConnections":
            self.core.request("DELETE", "/connections")
        elif tool == "updateProfiles":
            for p in self.data["profiles"]:
                if p["type"] == "url":
                    self._download(p)
            return {"message": "全部订阅已更新。"}, True, True
        elif tool == "updateProvider":
            name = urllib.parse.quote(c.get("name", ""), safe="")
            self.core.request("PUT", "/providers/proxies/" + name)
        elif tool == "updateResources":
            if self.core.kind != "ipc":
                self.core.request("POST", "/configs/geo")
            else:
                for name in ("GEOIP", "GEOSITE", "MMDB", "ASN"):
                    self.core.call("updateGeoData", name, timeout=180)
        elif tool == "backupLocal":
            body = self._backup()
            return {"file": {"name": "zfly-backup.json", "data": base64.b64encode(body).decode(), "revision": digest(body)}, "message": "备份已生成，包含订阅链接和本地凭据，请妥善保存。"}, False, False
        elif tool in ("testDAV", "backupDAV"):
            self._dav("PROPFIND" if tool == "testDAV" else "PUT", data=None if tool == "testDAV" else self._backup())
        elif tool in ("restoreLocalProfiles", "restoreLocalAll", "restoreDAVProfiles", "restoreDAVAll"):
            body = decode_data(c.get("data")) if tool.startswith("restoreLocal") else self._dav("GET")[0]
            self._restore(body, tool.endswith("All"))
            return {"running": False, "message": "备份已恢复，代理已停止。请检查配置后启动。"}, True, True
        else:
            raise RuntimeErrorMessage("此工具尚未实现。")
        return {"message": "操作已完成。"}, False, False

    def update_due(self):
        with self.lock:
            due = []
            for p in self.data["profiles"]:
                if p["type"] == "url" and p["autoUpdate"]:
                    try:
                        last = datetime.fromisoformat(p["updated"]).timestamp()
                    except (KeyError, ValueError):
                        last = 0
                    if time.time() - last >= p["updateHours"] * 3600:
                        due.append(p["id"])
        for identity in due:
            try:
                self.perform({"action": "profile.update", "id": identity})
            except RuntimeErrorMessage as error:
                self.update_errors[identity] = str(error)

    def relay(self, method, path, body=None):
        with self.lock:
            route = urllib.parse.urlsplit(path).path
            allowed = (method == "GET" and (route in ("/version", "/configs", "/proxies", "/connections", "/rules", "/dns/query", "/memory", "/providers/proxies", "/providers/rules")
                       or route.startswith(("/proxies/", "/providers/proxies/", "/providers/rules/"))))
            allowed = allowed or (method == "DELETE" and (route == "/connections" or route.startswith("/connections/")))
            allowed = allowed or (method == "POST" and route in ("/cache/dns/flush", "/cache/fakeip/flush"))
            if method == "PUT" and route.startswith("/proxies/") and "/" not in route[len("/proxies/"):]:
                try:
                    selection = json.loads(body)
                    self.perform({"action": "proxy.select", "group": urllib.parse.unquote(route[len("/proxies/"):]), "proxy": selection["name"]})
                except (ValueError, TypeError, KeyError):
                    raise RuntimeErrorMessage("节点选择格式无效。") from None
                return 204, b""
            if method == "GET" and path == "/configs":
                status, data = self.core.request(method, path)
                value = json.loads(data)
                value.update({target: self.data["network"][field] for field, target in NETWORK_KEYS.items()})
                for key in ("port", "socks-port"):
                    value[key] = self.data["workspace"].get("core", {}).get(key, 0)
                return status, encode_json(value).encode()
            # All persistent core settings go through the same transaction as native settings.
            if path == "/configs" and method == "PATCH":
                try:
                    patch = json.loads(body)
                except (ValueError, TypeError):
                    raise RuntimeErrorMessage("核心设置格式无效。") from None
                _, doc = self._document("core", None)
                if not isinstance(patch, dict):
                    raise RuntimeErrorMessage("核心设置需要 JSON 对象。")
                value = merge(json.loads(doc["text"]), patch)
                self.perform({"action": "workspace.write", "section": "core", "revision": doc["revision"], "text": encode_json(value)})
                return 204, b""
            if not allowed:
                raise RuntimeErrorMessage("此核心接口不允许直接修改。请使用 ZFly 配置与网络设置。")
            if method == "GET" and route.startswith("/proxies/") and route.endswith("/delay"):
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                preferences = self.data["workspace"].get("app", DEFAULTS["app"])
                query["url"] = [preferences.get("testUrl", DEFAULTS["app"]["testUrl"])]
                path = route + "?" + urllib.parse.urlencode(query, doseq=True)
            return self.core.request(method, path, body)
