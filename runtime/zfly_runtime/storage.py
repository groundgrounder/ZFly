import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.parse


class RuntimeErrorMessage(Exception):
    """Safe, actionable error for presentation by the native interfaces."""


def digest(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".zfly-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data if isinstance(data, bytes) else data.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def default_state():
    return {
        "schema": 1, "profiles": [], "currentProfileID": None, "selections": {},
        "network": {
            "tunEnabled": False, "tunStack": "mixed", "tunDevice": "utun" if os.uname().sysname == "Darwin" else "zfly",
            "systemProxy": False, "bypassDomains": ["localhost", "127.0.0.1", "::1", "*.local"],
            "bypassPrivateRoute": True, "autoSetSystemDns": False, "appendSystemDns": False,
            "mode": "rule", "mixedPort": 7890, "ipv6": False, "allowLan": False,
            "tcpConcurrent": True, "logLevel": "info",
        },
        "workspace": {"app": {"autoRun": False, "testUrl": "https://www.gstatic.com/generate_204", "checkCertificate": True, "userAgent": ""}},
        "catalog": {},
    }


def _valid_http_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
            and not parsed.username and not parsed.password)


APP_KEYS = {"autoRun", "testUrl", "checkCertificate", "userAgent"}


def valid_user_agent(value):
    # An empty value selects the built-in default. Header text must stay
    # printable ASCII so it cannot inject lines or fail to encode.
    return isinstance(value, str) and len(value) <= 256 and all(" " <= c <= "~" for c in value)


WORKSPACE_SECTIONS = {"core", "dns", "tun", "geo", "app", "dav", "profileOverrides",
                      "globalRules", "profileRules"}
PROFILE_WORKSPACE_SECTIONS = {"profileOverrides", "profileRules"}


def _validate_network_shape(network):
    if not isinstance(network, dict):
        raise ValueError()
    boolean_keys = ("tunEnabled", "systemProxy", "bypassPrivateRoute", "autoSetSystemDns",
                    "appendSystemDns", "ipv6", "allowLan", "tcpConcurrent")
    if any(type(network.get(key)) is not bool for key in boolean_keys):
        raise ValueError()
    if type(network.get("mixedPort")) is not int or not 1 <= network["mixedPort"] <= 65535:
        raise ValueError()
    if network.get("mode") not in ("rule", "global", "direct"):
        raise ValueError()
    if network.get("logLevel") not in ("silent", "error", "warning", "info", "debug"):
        raise ValueError()
    if network.get("tunStack") not in ("gvisor", "system", "mixed"):
        raise ValueError()
    device = network.get("tunDevice")
    if not isinstance(device, str) or not device or len(device) > 32 or any(not (c.isalnum() or c in "_.-") for c in device):
        raise ValueError()
    domains = network.get("bypassDomains")
    if not isinstance(domains, list) or any(not isinstance(item, str) or any(ord(c) < 32 for c in item) for item in domains):
        raise ValueError()


def validate_state(value):
    """Validate persisted state before service code consumes it."""
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError()
    profiles = value.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError()
    identities = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError()
        required = ("id", "label", "type", "url", "autoUpdate", "updateHours", "updated", "content")
        if any(key not in profile for key in required):
            raise ValueError()
        identity = profile["id"]
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError()
        identities.add(identity)
        if (not isinstance(profile["label"], str) or not profile["label"].strip()
                or profile["type"] not in ("file", "custom", "url")
                or not isinstance(profile["url"], str)
                or type(profile["autoUpdate"]) is not bool
                or type(profile["updateHours"]) is not int or not 1 <= profile["updateHours"] <= 8760
                or not isinstance(profile["updated"], str)
                or not isinstance(profile["content"], str)):
            raise ValueError()
        if profile["type"] == "url" and not _valid_http_url(profile["url"]):
            raise ValueError()
        if "ageSecretKey" in profile and not isinstance(profile["ageSecretKey"], str):
            raise ValueError()
        for key in ("upload", "download", "total", "expire"):
            if key in profile and profile[key] is not None and (type(profile[key]) is not int or profile[key] < 0):
                raise ValueError()
    current = value.get("currentProfileID")
    if current is not None and (not isinstance(current, str) or current not in identities):
        raise ValueError()
    _validate_network_shape(value.get("network"))
    workspace = value.get("workspace")
    if not isinstance(workspace, dict) or any(not isinstance(key, str) for key in workspace):
        raise ValueError()
    for key, item in workspace.items():
        section, separator, profile_id = key.partition(":")
        if section not in WORKSPACE_SECTIONS:
            raise ValueError()
        if section in PROFILE_WORKSPACE_SECTIONS:
            if not separator or profile_id not in identities:
                raise ValueError()
        elif separator:
            raise ValueError()
        if section in ("globalRules", "profileRules"):
            if not isinstance(item, str):
                raise ValueError()
        elif not isinstance(item, dict):
            raise ValueError()
        elif section == "app":
            if (set(item) - APP_KEYS
                    or type(item.get("autoRun")) is not bool
                    or type(item.get("checkCertificate")) is not bool
                    or not _valid_http_url(item.get("testUrl"))
                    or not valid_user_agent(item.get("userAgent", ""))):
                raise ValueError()
        elif section == "dav":
            if (set(item) - {"uri", "user", "password", "fileName"}
                    or any(not isinstance(value, str) for value in item.values())
                    or not isinstance(item.get("uri"), str)
                    or (item["uri"] and (not _valid_http_url(item["uri"]) or not item["uri"].lower().startswith("https://")))
                    or not isinstance(item.get("fileName"), str)
                    or not item["fileName"].strip()):
                raise ValueError()
    catalog = value.get("catalog")
    if not isinstance(catalog, dict):
        raise ValueError()
    for scope, records in catalog.items():
        if not isinstance(scope, str) or not isinstance(records, dict):
            raise ValueError()
        if scope != "proxies:" and (not scope.startswith("groups:") or scope[7:] not in identities):
            raise ValueError()
        for item_id, record in records.items():
            if (not isinstance(item_id, str) or not isinstance(record, dict)
                    or not isinstance(record.get("value"), dict)
                    or not isinstance(record["value"].get("name"), str)
                    or not isinstance(record["value"].get("type"), str)):
                raise ValueError()
    selections = value.get("selections")
    if not isinstance(selections, dict):
        raise ValueError()
    for identity, selected in selections.items():
        if identity not in identities or not isinstance(selected, dict):
            raise ValueError()
        if any(not isinstance(group, str) or not isinstance(proxy, str) for group, proxy in selected.items()):
            raise ValueError()
    return value


class StateStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.lock = (self.directory / "runtime.lock").open("a+")
        os.chmod(self.directory / "runtime.lock", 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeErrorMessage("ZFly 已在运行，请切换到已打开的 ZFly 窗口。") from None
        self.path = self.directory / "state.json"

    def load(self):
        if not self.path.exists():
            return default_state()
        try:
            if self.path.is_symlink():
                raise ValueError()
            os.chmod(self.path, 0o600)
            value = json.loads(self.path.read_text())
            return validate_state(value)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            raise RuntimeErrorMessage("ZFly 数据文件损坏或版本不兼容。原文件已保留，请从备份恢复。") from None

    def save(self, value):
        atomic_write(self.path, encode_json(value))

    def close(self):
        if not self.lock.closed:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()
