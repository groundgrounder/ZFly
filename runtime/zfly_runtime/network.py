"""System proxy changes use a recoverable lease; unrelated settings are preserved."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .storage import RuntimeErrorMessage, atomic_write, encode_json


class SystemProxy:
    def __init__(self, directory, runner=subprocess.run, platform=sys.platform):
        self.path = Path(directory) / "proxy-lease.json"
        self.runner, self.platform = runner, platform

    def _run(self, *arguments):
        try:
            result = self.runner(list(arguments), capture_output=True, text=True, timeout=15,
                                 env={**os.environ, "LC_ALL": "C"})
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeErrorMessage("无法访问系统代理设置，请检查系统网络设置权限。") from None
        if result.returncode or "** Error" in result.stdout:
            raise RuntimeErrorMessage("系统拒绝修改代理设置；请允许 ZFly 修改网络设置后重试。")
        return result.stdout.strip()

    def _mac(self, *arguments):
        return self._run("/usr/sbin/networksetup", *arguments)

    def _get_proxy(self, service, kind):
        output = self._mac("-get" + kind + "proxy", service)
        value = dict(line.split(": ", 1) for line in output.splitlines() if ": " in line)
        # The authentication flag reports a matching Keychain item, including
        # empty records. This lease changes only the endpoint and enabled state;
        # credentials stay in the Keychain and do not need to be backed up here.
        return {"enabled": value.get("Enabled") == "Yes", "server": value.get("Server", ""), "port": value.get("Port", "0")}

    def _set_proxy(self, service, kind, value):
        # Disabled entries can have empty server/port. Restore their inactive state.
        if value["server"] and value["port"] != "0":
            # Omit authentication arguments on both apply and restore. Passing
            # even "off" can delete the endpoint's existing Keychain item.
            self._mac("-set" + kind + "proxy", service, value["server"], str(value["port"]))
        self._mac("-set" + kind + "proxystate", service, "on" if value["enabled"] else "off")

    def _bypass(self, service):
        lines = self._mac("-getproxybypassdomains", service).splitlines()
        return [] if lines and lines[0].startswith("There aren't any") else lines

    def enable(self, port, bypass):
        self.disable()
        if self.platform == "darwin":
            services = [line for line in self._mac("-listallnetworkservices").splitlines()[1:] if line and not line.startswith("*")]
            if not services:
                raise RuntimeErrorMessage("系统中没有可设置代理的网络服务。")
            lease = {"platform": "darwin", "services": {}}
            target = {"enabled": True, "server": "127.0.0.1", "port": str(port)}
            for service in services:
                lease["services"][service] = {
                    "before": {kind: self._get_proxy(service, kind) for kind in ("web", "secureweb", "socksfirewall")},
                    "bypass": self._bypass(service), "target": target, "targetBypass": bypass,
                }
            atomic_write(self.path, encode_json(lease))
            try:
                for service in services:
                    for kind in ("web", "secureweb", "socksfirewall"):
                        self._set_proxy(service, kind, target)
                    self._mac("-setproxybypassdomains", service, *(bypass or ["Empty"]))
            except Exception:
                self.disable()
                raise
        elif self.platform.startswith("linux") and shutil.which("gsettings"):
            schema = "org.gnome.system.proxy"
            targets = {
                (schema, "mode"): "'manual'", (schema, "ignore-hosts"): repr(bypass),
                **{(schema + "." + kind, key): value
                   for kind in ("http", "https", "socks")
                   for key, value in (("host", "'127.0.0.1'"), ("port", str(port)))},
            }
            changes = [{"schema": s, "key": k, "before": self._run("gsettings", "get", s, k), "target": v}
                       for (s, k), v in targets.items()]
            atomic_write(self.path, encode_json({"platform": "linux", "changes": changes}))
            try:
                # Mode is switched last so an incomplete update does not become active.
                for item in changes[1:] + changes[:1]:
                    self._run("gsettings", "set", item["schema"], item["key"], item["target"])
            except Exception:
                self.disable()
                raise
        else:
            raise RuntimeErrorMessage("当前桌面没有受支持的系统代理接口。请在系统网络设置中使用 127.0.0.1:" + str(port) + "，或配置 TUN。")

    def _load_lease(self):
        try:
            if self.path.is_symlink():
                raise ValueError()
            os.chmod(self.path, 0o600)
            lease = json.loads(self.path.read_text())
        except (OSError, UnicodeError, ValueError):
            raise RuntimeErrorMessage("系统代理恢复记录损坏，请在系统网络设置中手动检查代理。") from None
        if not isinstance(lease, dict) or lease.get("platform") not in ("darwin", "linux"):
            raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
        if lease["platform"] == "darwin":
            services = lease.get("services")
            if not isinstance(services, dict) or not services:
                raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
            expected_kinds = {"web", "secureweb", "socksfirewall"}
            for service, item in services.items():
                if not isinstance(service, str) or not service or not isinstance(item, dict):
                    raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
                before = item.get("before")
                if not isinstance(before, dict) or not before or not set(before).issubset(expected_kinds):
                    raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
                for value in before.values():
                    if not self._valid_proxy_value(value):
                        raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
                target = item.get("target")
                if not self._valid_proxy_value(target) or target.get("server") != "127.0.0.1" or not target.get("enabled"):
                    raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
                if not self._valid_domains(item.get("bypass")) or not self._valid_domains(item.get("targetBypass")):
                    raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
        else:
            changes = lease.get("changes")
            allowed = {
                ("org.gnome.system.proxy", "mode"),
                ("org.gnome.system.proxy", "ignore-hosts"),
                *(("org.gnome.system.proxy." + kind, key) for kind in ("http", "https", "socks") for key in ("host", "port")),
            }
            if not isinstance(changes, list) or not changes or len(changes) != len(allowed):
                raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
            seen = set()
            for item in changes:
                if not isinstance(item, dict) or set(item) != {"schema", "key", "before", "target"}:
                    raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
                pair = (item["schema"], item["key"])
                if pair not in allowed or pair in seen or not all(isinstance(item[key], str) for key in ("schema", "key", "before", "target")):
                    raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
                if any(ord(char) < 32 or ord(char) == 127 for char in item["before"] + item["target"]):
                    raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
                seen.add(pair)
            if seen != allowed:
                raise RuntimeErrorMessage("系统代理恢复记录版本无效，请在系统网络设置中手动检查代理。")
        return lease

    @staticmethod
    def _valid_proxy_value(value):
        return (isinstance(value, dict) and set(value) == {"enabled", "server", "port"}
                and type(value["enabled"]) is bool and isinstance(value["server"], str)
                and isinstance(value["port"], str) and value["port"].isdigit()
                and 0 <= int(value["port"]) <= 65535
                and not any(ord(char) < 32 or ord(char) == 127 for char in value["server"]))

    @staticmethod
    def _valid_domains(value):
        return (isinstance(value, list) and len(value) <= 256
                and all(isinstance(item, str) and not any(ord(char) < 32 or ord(char) == 127 for char in item)
                        for item in value))

    def disable(self):
        if not self.path.exists():
            return
        lease = self._load_lease()
        errors = []
        if lease["platform"] == "darwin":
            for service, item in lease["services"].items():
                for kind, before in item["before"].items():
                    try:
                        if self._get_proxy(service, kind) == item["target"]:
                            self._set_proxy(service, kind, before)
                    except RuntimeErrorMessage as error:
                        errors.append(error)
                try:
                    if self._bypass(service) == item["targetBypass"]:
                        self._mac("-setproxybypassdomains", service, *(item["bypass"] or ["Empty"]))
                except RuntimeErrorMessage as error:
                    errors.append(error)
        else:
            for item in lease["changes"]:
                try:
                    if self._run("gsettings", "get", item["schema"], item["key"]) == item["target"]:
                        self._run("gsettings", "set", item["schema"], item["key"], item["before"])
                except RuntimeErrorMessage as error:
                    errors.append(error)
        if errors:
            raise RuntimeErrorMessage("系统代理未能完整还原；恢复记录已保留。请在系统网络设置中关闭指向 ZFly 的代理。")
        self.path.unlink(missing_ok=True)


def validate_network(network, require_tun_permission=True):
    if not isinstance(network, dict):
        raise RuntimeErrorMessage("网络设置需要 JSON 对象。")
    for key in ("tunEnabled", "systemProxy", "bypassPrivateRoute", "autoSetSystemDns", "appendSystemDns", "ipv6", "allowLan", "tcpConcurrent"):
        if type(network.get(key)) is not bool:
            raise RuntimeErrorMessage("网络开关值无效：" + key)
    if type(network.get("mixedPort")) is not int or not 1 <= network["mixedPort"] <= 65535:
        raise RuntimeErrorMessage("混合代理端口应为 1–65535 的整数。")
    for key, options in {"mode": ("rule", "global", "direct"), "logLevel": ("silent", "error", "warning", "info", "debug"), "tunStack": ("gvisor", "system", "mixed")}.items():
        if network.get(key) not in options:
            raise RuntimeErrorMessage("不支持的设置：" + key)
    device = network.get("tunDevice")
    if not isinstance(device, str) or not device or len(device) > 32 or any(not (c.isalnum() or c in "_-.") for c in device):
        raise RuntimeErrorMessage("TUN 设备名称无效。")
    domains = network.get("bypassDomains")
    if not isinstance(domains, list) or any(not isinstance(x, str) or any(ord(c) < 32 for c in x) for x in domains):
        raise RuntimeErrorMessage("代理绕过地址无效。")
    if network["autoSetSystemDns"] or network["appendSystemDns"]:
        raise RuntimeErrorMessage("此版本尚未提供系统 DNS 接管；可在 DNS 与 hosts 中配置核心 DNS。")
    if require_tun_permission and network["tunEnabled"] and os.geteuid() != 0 and os.environ.get("ZFLY_TUN_CAPABLE") != "1":
        raise RuntimeErrorMessage("TUN 需要 ZFly 自己的系统权限助手，此版本尚未内置 macOS 权限助手。可先使用系统代理；Linux 可为独立核心配置 CAP_NET_ADMIN。")
