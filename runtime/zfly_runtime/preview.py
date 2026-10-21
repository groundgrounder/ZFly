"""Isolated preview dependencies. Service still owns every management transaction.

No sockets, subprocesses, host proxy commands or external downloads belong here.
Validation covers YAML/schema only, not the real core's protocol semantics.
"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import urllib.parse

from .core import Core
from .network import validate_network
from .schema import validate_core_config
from .service import Service, parse_profile, validate_http_url
from .storage import RuntimeErrorMessage, StateStore, default_state, encode_json

FIXTURE = Path(__file__).with_name("preview.json")
DELAY_SECONDS = 0.45


def sample_config(fixture):
    """Derive an editable YAML-compatible profile from the shared wire fixture."""
    config = copy.deepcopy(fixture["configs"])
    config["proxies"], config["proxy-groups"] = [], []
    for name, item in fixture["proxies"]["proxies"].items():
        if item["type"] in ("Selector", "URLTest", "Fallback"):
            config["proxy-groups"].append({"name": name, "type": {"Selector": "select", "URLTest": "url-test", "Fallback": "fallback"}[item["type"]], "proxies": item["all"]})
        elif name != "DIRECT":
            config["proxies"].append({"name": name, "type": item["type"].lower(), "server": "example.invalid", "port": 443})
    config["rules"] = ["DOMAIN-SUFFIX,github.com,节点选择", "MATCH,DIRECT"]
    return config


def initial_state(fixture, variant):
    state = default_state()
    managed = fixture["management"]
    state["network"] = copy.deepcopy(managed["network"])
    state["profiles"] = copy.deepcopy(managed["profiles"])
    content = encode_json(sample_config(fixture))
    for profile in state["profiles"]:
        profile["content"] = content
    state["currentProfileID"] = managed["currentProfileID"]
    state["workspace"]["app"]["autoRun"] = variant == "default" and managed["running"]
    state["selections"] = {profile["id"]: {name: item["now"] for name, item in fixture["proxies"]["proxies"].items()
                                            if item["type"] == "Selector"} for profile in state["profiles"]}
    if variant == "empty":
        state.update(profiles=[], currentProfileID=None, selections={})
    return state


class RecordingSystemProxy:
    def __init__(self):
        self.calls = []

    def enable(self, port, bypass):
        self.calls.append(("enable", port, list(bypass)))

    def disable(self):
        self.calls.append(("disable",))


class PreviewFetcher:
    def __init__(self, fixture, state):
        self.content = encode_json(sample_config(fixture)).encode()
        self.backup = encode_json({"application": "ZFly", "version": 1, "state": state}).encode()
        self.calls = []
        self.archives = {}
        self.info = "; ".join(f"{key}={fixture['management']['profiles'][1][key]}" for key in ("upload", "download", "total", "expire"))

    def __call__(self, url, headers=None, method="GET", data=None, *, max_bytes=None):
        validate_http_url(url)
        self.calls.append((method, url))
        if headers and "Authorization" in headers:
            if method == "PUT":
                self.archives[url] = data
                return b"", {}, 201
            if method == "PROPFIND":
                return b"", {}, 207
            return self.archives.get(url, self.backup), {}, 200
        return self.content, {"subscription-userinfo": self.info, "etag": '"zfly-preview"'}, 200


class FakeCore(Core):
    def __init__(self, home, secret, fixture, stop_event):
        # Do not initialize the real driver's transport or process lifecycle.
        self.home, self.secret, self.kind = Path(home), secret, "ipc"
        self.home.mkdir(mode=0o700)
        self.endpoint = "http://127.0.0.1:0"
        self.fixture = copy.deepcopy(fixture)
        self.stop_event = stop_event
        self.last_config = None
        self.process = None
        self._alive = self.running = False
        self.lock = threading.RLock()
        self.proxies = {}
        self.connections = copy.deepcopy(fixture["connections"])
        self.started_at = time.monotonic()
        self.elapsed_running = 0.0
        self.calls = []
        self.events = {"log": [{"level": "info", "payload": "演示核心已就绪"}], "dns": [],
                       "request": copy.deepcopy(fixture["connections"]["connections"])}

    @property
    def alive(self):
        return self._alive

    def start(self):
        self._alive = True

    def close(self):
        with self.lock:
            self._alive = self.running = False

    def history(self, kind):
        with self.lock:
            return copy.deepcopy(self.events[kind])

    def validate(self, content):
        config = parse_profile(content)
        # Disabled ports are valid in a stopped core configuration.
        validate_core_config({k: v for k, v in config.items() if k != "mixed-port" or v != 0})

    def decrypt(self, content, key):
        raise RuntimeErrorMessage("演示核心不提供 Age 解密，请导入未加密的 YAML。")

    def call(self, method, arguments=None, timeout=60):
        with self.lock:
            self.calls.append(method)
            if method == "validateConfig":
                self.validate(Path(arguments).read_text())
            elif method == "setupConfig":
                for name, proxy in (arguments or {}).get("selected-map", {}).items():
                    item = self.proxies.get(name, {})
                    if proxy in item.get("all", []):
                        item["now"] = proxy
                        if item.get("type") in ("URLTest", "Fallback"):
                            item["fixed"] = proxy
            elif method == "updateGeoData":
                # A labelled placeholder makes the resource list reviewable.
                (self.home / (str(arguments) + ".dat")).write_bytes(b"ZFly preview resource; no geo database")
            elif method not in ("updateExternalProvider", "forceGc", "stopListener", "startListener"):
                raise RuntimeErrorMessage("演示核心不支持此 IPC 操作。")
        return None

    def apply(self, config, selections=None, running=False):
        self.validate(encode_json(config))
        with self.lock:
            elapsed = time.monotonic() - self.started_at
            if self.running:
                self.elapsed_running += elapsed
            self.started_at = time.monotonic()
            self.running = running
            self.last_config = copy.deepcopy(config)
            proxies = {"DIRECT": {"type": "Direct", "history": []}, "REJECT": {"type": "Reject", "history": []}}
            for item in config.get("proxies", []):
                name = item["name"]
                fixture = self.fixture["proxies"]["proxies"].get(name, {})
                proxies[name] = {"type": fixture.get("type", item["type"]), "history": copy.deepcopy(fixture.get("history", [{"delay": 42}]))}
            for item in config.get("proxy-groups", []):
                names = item.get("proxies", [])
                kind = {"select": "Selector", "url-test": "URLTest", "fallback": "Fallback", "load-balance": "LoadBalance"}.get(item["type"], item["type"])
                proxies[item["name"]] = {"type": kind, "all": list(names), "now": names[0] if names else ""}
            self.proxies = proxies
            for name, proxy in list((selections or {}).items()):
                item = proxies.get(name, {})
                if item.get("type") not in ("Selector", "URLTest", "Fallback") or proxy not in item.get("all", []):
                    del selections[name]
            self.call("setupConfig", {"selected-map": selections or {}})

    def request(self, method, path, data=None):
        parsed = urllib.parse.urlsplit(path)
        route = parsed.path
        if method == "GET" and route.startswith("/proxies/") and route.endswith("/delay"):
            name = urllib.parse.unquote(route[len("/proxies/"):-len("/delay")])
            if self.stop_event.wait(DELAY_SECONDS):
                raise RuntimeErrorMessage("演示核心正在退出，测速已取消。")
            with self.lock:
                item = self.proxies.get(name)
                if item is None:
                    raise RuntimeErrorMessage("节点已不存在。")
                delay = (item.get("history") or [{"delay": 42}])[-1]["delay"]
            return 200, encode_json({"delay": delay}).encode()
        with self.lock:
            value, status = {}, 200
            if route == "/version":
                value = self.fixture["version"]
            elif route == "/configs":
                value = {**self.fixture["configs"], **(self.last_config or {})}
                value["sniffing"] = value.get("sniffer", {}).get("enable", value.get("sniffing", True))
                value.pop("secret", None)
            elif route == "/proxies":
                value = {"proxies": self.proxies}
            elif route.startswith("/proxies/"):
                item = self.proxies.get(urllib.parse.unquote(route[len("/proxies/"):]))
                if item is None:
                    raise RuntimeErrorMessage("代理组已不存在。")
                if method == "GET":
                    value = item
                elif method == "PUT":
                    name = json.loads(data)["name"]
                    if item["type"] not in ("Selector", "URLTest", "Fallback") or name not in item.get("all", []):
                        raise RuntimeErrorMessage("代理组或节点已变化。")
                    item["now"] = name
                    if item["type"] != "Selector":
                        item["fixed"] = name
                    status = 204
                elif method == "DELETE" and item["type"] in ("URLTest", "Fallback"):
                    item.pop("fixed", None)
                    item["now"] = next(iter(item["all"]), "")
                    status = 204
                else:
                    raise RuntimeErrorMessage("不支持的代理操作。")
            elif route == "/connections" or route.startswith("/connections/"):
                if method == "DELETE":
                    identity = urllib.parse.unquote(route[len("/connections/"):]) if route != "/connections" else None
                    self.connections["connections"] = [c for c in self.connections["connections"] if identity and c["id"] != identity]
                    status = 204
                else:
                    value = copy.deepcopy(self.connections)
                    seconds = self.elapsed_running + (time.monotonic() - self.started_at if self.running else 0)
                    value["downloadTotal"] += int(seconds * 1_048_576)
                    value["uploadTotal"] += int(seconds * 131_072)
                    if not self.running:
                        value["connections"] = []
            elif route == "/rules":
                value = {"rules": [{"type": parts[0], "payload": parts[1] if len(parts) > 2 else "", "proxy": parts[-1]}
                                   for parts in (line.split(",") for line in (self.last_config or {}).get("rules", []))]}
            elif route == "/dns/query":
                query = urllib.parse.parse_qs(parsed.query)
                value = {"Answer": [{"name": query.get("name", [""])[0], "type": query.get("type", ["A"])[0], "data": "192.0.2.1"}]}
                self.events["dns"].append(copy.deepcopy(value))
            elif route.startswith("/cache/") and route.endswith("/flush"):
                status = 204
            elif route.startswith("/providers/"):
                if method == "PUT":
                    self.call("updateExternalProvider", urllib.parse.unquote(route.rsplit("/", 1)[-1]))
                    status = 204
                else:
                    value = {"providers": copy.deepcopy((self.last_config or {}).get("proxy-providers" if route.startswith("/providers/proxies") else "rule-providers", {}))}
            elif route == "/memory":
                value = {"inuse": self.connections["memory"]}
            else:
                raise RuntimeErrorMessage("演示核心不支持此接口。")
            return status, b"" if status == 204 else encode_json(value).encode()


class PreviewBackend:
    """Own a fresh private directory and a Service with entirely local adapters."""
    def __init__(self, secret="preview-test", variant="default", stop_event=None):
        if variant not in ("default", "empty", "stopped"):
            raise ValueError("invalid preview state")
        self.temporary = tempfile.TemporaryDirectory(prefix="zfly-preview-")
        self.service = None
        try:
            fixture = json.loads(FIXTURE.read_text())
            storage = StateStore(self.temporary.name)
            state = initial_state(fixture, variant)
            storage.save(state)
            core = FakeCore(storage.directory / "core", secret, fixture, stop_event or threading.Event())
            self.service = Service(storage, core, RecordingSystemProxy(), PreviewFetcher(fixture, state),
                                   network_validator=lambda value: validate_network(value, require_tun_permission=False),
                                   port_preflight=lambda config: None, listener_check=lambda config: None)
        except BaseException:
            if 'storage' in locals():
                storage.close()
            self.temporary.cleanup()
            raise

    def close(self):
        try:
            if self.service:
                self.service.close()
        finally:
            self.temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
