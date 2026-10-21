"""Offline regressions: no real sockets, cores or host proxy mutations."""
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import errno
import io
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import urllib.request
import urllib.parse

from zfly_runtime.__main__ import Handler
from zfly_runtime.network import SystemProxy
from zfly_runtime.service import DownloadRedirects, Service, parse_profile
from zfly_runtime.storage import StateStore, RuntimeErrorMessage, default_state, digest, encode_json


YAML = "proxies: []\nproxy-groups: []\nrules: [MATCH,DIRECT]\n"


class MemoryStorage:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.saved = default_state()
        self.fail = False

    def load(self):
        return copy.deepcopy(self.saved)

    def save(self, value):
        if self.fail:
            raise OSError("disk full")
        self.saved = copy.deepcopy(value)


class FakeCore:
    kind, alive, endpoint, secret = "ipc", True, "http://127.0.0.1:32123", "test-only"

    def __init__(self, directory):
        self.home = Path(directory)
        self.last_config = None
        self.applies = []
        self.selected = "DIRECT"
        self.fail_next = False

    def validate(self, _content):
        pass

    def apply(self, config, _selections=None, _running=False):
        self.last_config = copy.deepcopy(config)
        self.applies.append(copy.deepcopy(config))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeErrorMessage("invalid core configuration")

    def request(self, method, path, data=None):
        if method == "GET" and path == "/proxies":
            return 200, json.dumps({"proxies": {"选择": {"type": "Selector", "all": ["DIRECT", "REJECT"], "now": self.selected}}}).encode()
        if method == "PUT" and path.startswith("/proxies/"):
            self.selected = json.loads(data)["name"]
            return 204, b""
        return 200, b"{}"


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = MemoryStorage(self.directory.name)
        self.core = FakeCore(self.directory.name)
        self.proxy = MagicMock()
        self.service = Service(self.storage, self.core, self.proxy)
        self.addCleanup(patch.stopall)
        patch.object(self.service, "_preflight_ports").start()
        patch("zfly_runtime.service.socket.create_connection", return_value=MagicMock()).start()

    def create(self, custom=False, content=YAML):
        command = {"action": "profile.create" if custom else "profile.import", "label": "本地配置"}
        command["data"] = base64.b64encode(content.encode()).decode()
        self.service.perform(command)
        return self.service.data["currentProfileID"]

    def workspace(self, section, value, identity=None):
        doc = self.service.perform({"action": "workspace.read", "section": section, "id": identity})["document"]
        return self.service.perform({"action": "workspace.write", "section": section, "id": identity,
                                     "revision": doc["revision"], "text": value if isinstance(value, str) else json.dumps(value)})

    def resource(self, kind, value, identity=None, profile=None):
        scope = {"section": kind, "name": profile, "id": identity}
        doc = self.service.perform({"action": "catalog.read", **scope})["document"]
        result = self.service.perform({"action": "catalog.save", **scope, "text": json.dumps(value), "revision": doc["revision"]})
        return next(x["id"] for x in result["items"] if x["label"] == value["name"])

    def test_core_configure_rejects_an_intervening_write(self):
        self.create()
        expected = copy.deepcopy(self.service.data["network"])
        self.service.perform({"action": "network.save", "expected": expected,
                              "network": {**expected, "mode": "direct"}})
        applies = len(self.core.applies)
        with self.assertRaisesRegex(RuntimeErrorMessage, "其他界面修改"):
            self.service.perform({"action": "core.configure", "patch": {"mode": "global"},
                                  "expected": {"mode": "rule"}})
        self.assertEqual(self.storage.saved["network"]["mode"], "direct")
        self.assertEqual(len(self.core.applies), applies)

    def test_core_configure_requires_an_expected_value_for_every_change(self):
        with self.assertRaisesRegex(RuntimeErrorMessage, "原始值"):
            self.service.perform({"action": "core.configure", "patch": {"mode": "global", "ipv6": True},
                                  "expected": {"mode": "rule"}})

    def test_save_receipt_survives_retries_without_overwriting_later_edits(self):
        command = {"action": "core.configure", "patch": {"mode": "global"},
                   "expected": {"mode": "rule"}, "requestID": "a" * 32}
        receipt = self.service.perform(command)
        self.assertEqual(self.storage.saved["network"]["mode"], "global")
        expected = copy.deepcopy(self.service.data["network"])
        self.service.perform({"action": "network.save", "expected": expected,
                              "network": {**expected, "mode": "direct"}})
        applies = len(self.core.applies)
        self.assertEqual(self.service.perform(command), receipt)
        self.assertEqual(self.storage.saved["network"]["mode"], "direct")
        self.assertEqual(len(self.core.applies), applies)
        with self.assertRaisesRegex(RuntimeErrorMessage, "其他请求"):
            self.service.perform({**command, "patch": {"mode": "rule"}})

    def test_failed_core_configure_rolls_back_and_can_be_retried(self):
        self.create()
        before = copy.deepcopy(self.storage.saved)
        command = {"action": "core.configure", "patch": {"mode": "global"},
                   "expected": {"mode": "rule"}, "requestID": "b" * 32}
        self.core.fail_next = True
        with self.assertRaises(RuntimeErrorMessage):
            self.service.perform(command)
        self.assertEqual(self.storage.saved, before)
        self.assertEqual(self.service.data, before)
        self.service.perform(command)
        self.assertEqual(self.storage.saved["network"]["mode"], "global")

    def test_delays_are_concurrent_and_do_not_block_state_or_mutations(self):
        release, started = threading.Event(), threading.Event()
        guard = threading.Lock()
        active = 0
        def request(_method, _path, _data=None):
            nonlocal active
            with guard:
                active += 1
                if active == 4:
                    started.set()
            if not release.wait(3):
                raise AssertionError("delay test was not released")
            return 200, b'{"delay":42}'
        with patch.object(self.core, "request", side_effect=request), ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.service.relay, "GET", f"/proxies/{name}/delay") for name in "abcd"]
            try:
                self.assertTrue(started.wait(2), "delay requests were serialized")
                self.assertFalse(self.service.state()["running"])
                expected = copy.deepcopy(self.service.data["network"])
                self.service.perform({"action": "network.save", "expected": expected,
                                      "network": {**expected, "mode": "direct"}})
                self.assertEqual(self.storage.saved["network"]["mode"], "direct")
            finally:
                release.set()
            for future in futures:
                self.assertEqual(json.loads(future.result(timeout=2)[1])["delay"], 42)

    def test_first_import_is_selected_but_never_started(self):
        identity = self.create()
        self.assertEqual(self.storage.saved["currentProfileID"], identity)
        self.assertFalse(self.service.running)
        self.assertEqual(self.core.last_config["mixed-port"], 0)
        self.proxy.enable.assert_not_called()

    def test_start_requires_a_profile(self):
        with self.assertRaisesRegex(RuntimeErrorMessage, "先添加"):
            self.service.perform({"action": "running.set", "running": True})

    def test_apply_failure_restores_running_configuration_and_persistent_state(self):
        self.create()
        self.service.perform({"action": "running.set", "running": True})
        original = copy.deepcopy(self.service.data)
        updated = {**original["network"], "mode": "global"}
        self.core.fail_next = True
        with self.assertRaises(RuntimeErrorMessage):
            self.service.perform({"action": "network.save", "expected": original["network"], "network": updated})
        self.assertTrue(self.service.running)
        self.assertEqual(self.service.data, original)
        self.assertEqual(self.storage.saved, original)
        self.assertEqual(self.core.last_config["mode"], "rule")

    def test_disk_failure_rolls_back_applied_configuration(self):
        self.create()
        original = copy.deepcopy(self.service.data)
        self.storage.fail = True
        with self.assertRaises(OSError):
            self.workspace("core", {"mode": "global"})
        self.assertEqual(self.service.data, original)
        self.assertEqual(self.core.last_config["mode"], "rule")

    def test_selection_persists_and_rolls_back_on_disk_failure(self):
        identity = self.create()
        self.service.perform({"action": "proxy.select", "group": "选择", "proxy": "REJECT"})
        self.assertEqual(self.storage.saved["selections"][identity]["选择"], "REJECT")
        self.storage.fail = True
        with self.assertRaises(OSError):
            self.service.perform({"action": "proxy.select", "group": "选择", "proxy": "DIRECT"})
        self.assertEqual(self.core.selected, "REJECT")
        self.assertEqual(self.service.data["selections"][identity]["选择"], "REJECT")

    def test_stale_profile_and_workspace_writes_are_rejected(self):
        identity = self.create()
        revision = self.service.state()["profiles"][0]["revision"]
        self.service.perform({"action": "profile.edit", "id": identity, "revision": revision, "label": "已修改"})
        with self.assertRaisesRegex(RuntimeErrorMessage, "更新"):
            self.service.perform({"action": "profile.delete", "id": identity, "revision": revision})
        doc = self.service.perform({"action": "workspace.read", "section": "core"})["document"]
        self.workspace("core", {"mode": "global"})
        with self.assertRaisesRegex(RuntimeErrorMessage, "修改"):
            self.service.perform({"action": "workspace.write", "section": "core", "revision": doc["revision"], "text": "{}"})

    def test_stale_network_change_is_rejected(self):
        old = copy.deepcopy(self.service.data["network"])
        self.service.perform({"action": "network.save", "expected": old, "network": {**old, "mode": "global"}})
        with self.assertRaisesRegex(RuntimeErrorMessage, "修改"):
            self.service.perform({"action": "network.save", "expected": old, "network": old})

    def test_core_workspace_revision_includes_network_settings(self):
        self.create()
        document = self.service.perform({"action": "workspace.read", "section": "core"})["document"]
        old = copy.deepcopy(self.service.data["network"])
        self.service.perform({"action": "network.save", "expected": old,
                              "network": {**old, "mode": "global"}})
        with self.assertRaisesRegex(RuntimeErrorMessage, "修改"):
            self.service.perform({"action": "workspace.write", "section": "core",
                                  "revision": document["revision"], "text": document["text"]})

    def test_download_metadata_etag_and_url_changes(self):
        calls = []
        def fetch(url, headers):
            calls.append((url, headers))
            return (YAML.encode(), {"ETag": "abc", "Subscription-Userinfo": "upload=12; download=34; total=100; expire=1800000000"}, 200) if len(calls) == 1 else (b"", {}, 304)
        self.service.fetcher = fetch
        self.service.perform({"action": "profile.add", "url": "https://example.test/token"})
        identity = self.service.data["currentProfileID"]
        self.service.perform({"action": "profile.update", "id": identity})
        self.assertEqual(calls[1][1]["If-None-Match"], "abc")
        self.assertEqual(self.service.profile(identity)["download"], 34)
        revision = self.service.state()["profiles"][0]["revision"]
        self.service.perform({"action": "profile.edit", "id": identity, "revision": revision, "url": "https://example.test/other"})
        self.assertNotIn("etag", self.service.profile(identity))
        archive = self.service.perform({"action": "tool.run", "tool": "backupLocal"})["file"]
        self.service.perform({"action": "tool.run", "tool": "restoreLocalAll", "data": archive["data"]})
        self.assertEqual(self.service.profile(identity)["download"], 34)

    def test_subscription_without_usage_headers_can_restore_its_own_backup(self):
        self.service.fetcher = lambda *_: (YAML.encode(), {}, 200)
        self.service.perform({"action": "profile.add", "url": "https://example.test/no-headers"})
        archive = self.service.perform({"action": "tool.run", "tool": "backupLocal"})["file"]
        self.service.perform({"action": "tool.run", "tool": "restoreLocalAll", "data": archive["data"]})

    def test_subscription_edit_and_restore_reject_authenticated_urls(self):
        self.service.fetcher = lambda *_: (YAML.encode(), {}, 200)
        identity = self.service.perform({"action": "profile.add", "url": "https://example.test/token"})["state"]["currentProfileID"]
        profile = self.service.profile(identity)
        with self.assertRaisesRegex(RuntimeErrorMessage, "认证订阅"):
            self.service.perform({"action": "profile.edit", "id": identity,
                                  "revision": digest(encode_json(profile)),
                                  "url": "https://user:pass@example.test/token"})
        archive = json.loads(base64.b64decode(self.service.perform({"action": "tool.run", "tool": "backupLocal"})["file"]["data"]))
        archive["state"]["profiles"][0]["url"] = "https://user:pass@example.test/token"
        payload = base64.b64encode(json.dumps(archive).encode()).decode()
        with self.assertRaisesRegex(RuntimeErrorMessage, "受支持的 ZFly 备份"):
            self.service.perform({"action": "tool.run", "tool": "restoreLocalAll", "data": payload})

    def test_subscription_user_agent_requests_mihomo_protocols(self):
        calls = []
        def fetch(url, headers=None, method="GET", data=None):
            calls.append(headers or {})
            return YAML.encode(), {"ETag": "v1"}, 200
        self.service.fetcher = fetch
        identity = self.service.perform({"action": "profile.add", "url": "https://example.test/token"})["state"]["currentProfileID"]
        agent = calls[-1]["User-Agent"]
        # Panels such as Xboard parse the first "name/version" pair. It must be a
        # Meta client name, otherwise AnyTLS, Mieru and VLESS nodes are dropped.
        name, version = re.search(r"([a-zA-Z0-9\-_]+)[\/\s]+(v?[0-9]+(?:\.[0-9]+){0,2})", agent.lower()).groups()
        self.assertEqual(name, "meta")
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (1, 19, 9))
        # Some panels answer HTTP 403 to any User-Agent containing "zfly".
        self.assertNotIn("zfly", agent.lower())

        # Preferences saved before userAgent existed still list it and keep the default.
        self.service.data["workspace"]["app"].pop("userAgent")
        document = self.service.perform({"action": "workspace.read", "section": "app"})["document"]
        self.assertEqual(json.loads(document["text"])["userAgent"], "")
        self.service.perform({"action": "profile.update", "id": identity})
        self.assertEqual(calls[-1]["User-Agent"], agent)
        self.assertEqual(calls[-1]["If-None-Match"], "v1")
        self.assertNotIn("agent", self.service.state()["profiles"][0])

        # Content cached for another client identity must be downloaded again, not
        # revalidated: a 304 would keep nodes the panel filtered for that client.
        self.workspace("app", {**json.loads(document["text"]), "userAgent": "  clash-verge/v2.4.2  "})
        self.service.perform({"action": "profile.update", "id": identity})
        self.assertEqual(calls[-1]["User-Agent"], "clash-verge/v2.4.2")
        self.assertNotIn("If-None-Match", calls[-1])
        self.service.perform({"action": "profile.update", "id": identity})
        self.assertEqual(calls[-1]["If-None-Match"], "v1")
        self.service.profile(identity).pop("agent")  # Downloaded by an earlier ZFly version.
        self.service.perform({"action": "profile.update", "id": identity})
        self.assertNotIn("If-None-Match", calls[-1])
        self.service.data["workspace"]["dav"] = {"uri": "https://example.test/dav", "user": "", "password": "", "fileName": "zfly.json"}
        self.service.perform({"action": "tool.run", "tool": "testDAV"})
        self.assertNotIn("User-Agent", calls[-1])

        saved = copy.deepcopy(self.service.data["workspace"]["app"])
        for invalid in ("a\r\nX-Injected: 1", "节点", "x" * 257, 42):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(RuntimeErrorMessage, "User-Agent"):
                self.workspace("app", {**saved, "userAgent": invalid})
            self.assertEqual(self.service.data["workspace"]["app"], saved)

    def test_profile_commands_reject_wrong_types_without_internal_errors(self):
        for command in (
            {"action": "profile.add", "url": 42},
            {"action": "profile.add", "url": "https://example.test/a", "label": []},
            {"action": "profile.create", "label": "   "},
            {"action": "profile.import", "data": base64.b64encode(YAML.encode()).decode(), "ageSecretKey": 1},
        ):
            with self.subTest(command=command), self.assertRaises(RuntimeErrorMessage):
                self.service.perform(command)
        self.assertEqual(self.service.data["profiles"], [])

        identity = self.create()
        profile = self.service.profile(identity)
        revision = digest(encode_json(profile))
        with self.assertRaises(RuntimeErrorMessage):
            self.service.perform({"action": "profile.edit", "id": identity, "revision": revision, "label": {}})
        self.assertEqual(self.service.profile(identity)["label"], "本地配置")

    def test_custom_catalog_export_rename_and_dependency_protection(self):
        profile = self.create(custom=True)
        node = self.resource("proxies", {"name": "节点 A", "type": "socks5", "server": "127.0.0.1", "port": 1080})
        self.resource("groups", {"name": "选择", "type": "select", "proxies": ["节点 A", "DIRECT"]}, profile=profile)
        self.workspace("profileRules", "DOMAIN,example.test,节点 A", profile)
        self.workspace("profileOverrides", {"rules": ["MATCH,节点 A"]}, profile)
        doc = self.service.perform({"action": "catalog.read", "section": "proxies", "id": node})["document"]
        with self.assertRaisesRegex(RuntimeErrorMessage, "引用"):
            self.service.perform({"action": "catalog.delete", "section": "proxies", "id": node, "revision": doc["revision"]})
        self.resource("proxies", {"name": "节点 B", "type": "socks5", "server": "127.0.0.1", "port": 1080}, node)
        exported = self.service.perform({"action": "profile.read", "id": profile})["file"]
        config = parse_profile(base64.b64decode(exported["data"]).decode())
        self.assertEqual(config["proxy-groups"][0]["proxies"][0], "节点 B")
        self.assertEqual(config["proxies"][0]["name"], "节点 B")
        self.assertEqual(self.service.data["workspace"]["profileRules:" + profile], "DOMAIN,example.test,节点 B")
        self.assertEqual(self.service.data["workspace"]["profileOverrides:" + profile]["rules"], ["MATCH,节点 B"])

    def test_catalog_rejects_cycles_missing_and_duplicate_names(self):
        profile = self.create(custom=True)
        group = self.resource("groups", {"name": "A", "type": "select", "proxies": ["DIRECT"]}, profile=profile)
        self.resource("groups", {"name": "B", "type": "select", "proxies": ["A"]}, profile=profile)
        original = copy.deepcopy(self.service.data)
        for value in ({"name": "A", "type": "select", "proxies": ["B"]},
                      {"name": "A", "type": "select", "proxies": ["missing"]},
                      {"name": "DIRECT", "type": "select", "proxies": []}):
            with self.assertRaises(RuntimeErrorMessage):
                self.resource("groups", value, group, profile)
            self.assertEqual(self.service.data, original)

    def test_config_confines_control_listeners_and_provider_caches(self):
        self.create(content="""external-controller: 0.0.0.0:9999
secret: attacker
mixed-port: 8888
port: 80
listeners: [{name: rogue, type: socks, port: 9000}]
dns: {enable: true, listen: '0.0.0.0:53'}
ntp: {enable: true, write-to-system: true}
tun: {enable: true}
proxy-providers:
  one: {type: http, path: /tmp/escape, url: 'https://example.test/a'}
rules: [MATCH,DIRECT]
""")
        config = self.core.last_config
        self.assertEqual(config["external-controller"], "127.0.0.1:32123")
        self.assertEqual(config["secret"], "test-only")
        self.assertNotIn("listeners", config)
        self.assertEqual(config["port"], 0)
        self.assertEqual(config["dns"]["listen"], "")
        self.assertFalse(config["tun"]["enable"])
        self.assertFalse(config["ntp"]["write-to-system"])
        self.assertTrue(config["proxy-providers"]["one"]["path"].startswith("providers/"))

    def test_file_provider_cannot_escape_owned_core_directory(self):
        with self.assertRaisesRegex(RuntimeErrorMessage, "路径"):
            self.create(content="proxy-providers: {one: {type: file, path: /etc/passwd}}\nrules: [MATCH,DIRECT]")
        self.assertEqual(self.service.data["profiles"], [])

    def test_backup_restore_is_stopped_and_rejects_malformed_shapes(self):
        self.create(custom=True)
        self.service.perform({"action": "running.set", "running": True})
        archive = self.service.perform({"action": "tool.run", "tool": "backupLocal"})["file"]
        body = json.loads(base64.b64decode(archive["data"]))
        original = copy.deepcopy(self.service.data)
        for field, value in (("workspace", {"app": {"autoRun": "yes"}}), ("catalog", {"proxies:": []}), ("selections", {"missing": {}})):
            invalid = copy.deepcopy(body)
            invalid["state"][field] = value
            with self.assertRaises(RuntimeErrorMessage):
                self.service.perform({"action": "tool.run", "tool": "restoreLocalAll", "data": base64.b64encode(json.dumps(invalid).encode()).decode()})
            self.assertEqual(self.service.data, original)
            self.assertTrue(self.service.running)
        self.service.perform({"action": "tool.run", "tool": "restoreLocalAll", "data": archive["data"]})
        self.assertFalse(self.service.running)
        self.assertEqual(self.service.data, original)

    def test_relay_persists_settings_and_blocks_configuration_reload(self):
        self.create()
        self.service.relay("PATCH", "/configs", b'{"mode":"global"}')
        self.assertEqual(self.storage.saved["network"]["mode"], "global")
        status, body = self.service.relay("GET", "/configs")
        self.assertEqual(json.loads(body)["mixed-port"], 7890)
        for method, path in (("PUT", "/configs?force=true"), ("PUT", "/providers/proxies/a"), ("POST", "/restart")):
            with self.assertRaises(RuntimeErrorMessage):
                self.service.relay(method, path, b"{}")

    def test_stop_restores_proxy_and_preserves_user_preference(self):
        self.create()
        old = copy.deepcopy(self.service.data["network"])
        self.service.perform({"action": "network.save", "expected": old, "network": {**old, "systemProxy": True}})
        self.service.perform({"action": "running.set", "running": True})
        self.proxy.enable.assert_called_once()
        self.service.perform({"action": "running.set", "running": False})
        self.proxy.disable.assert_called()
        self.assertTrue(self.service.data["network"]["systemProxy"])
        self.assertIsNone(self.service.proxy_signature)

    def test_sniffing_setting_persists_yaml_and_preserves_sniffer_options(self):
        self.create()
        self.workspace("core", {"sniffer": {"enable": False, "override-destination": True}})
        self.service.relay("PATCH", "/configs", b'{"sniffing":true}')
        expected = {"enable": True, "override-destination": True}
        self.assertEqual(self.core.last_config["sniffer"], expected)
        self.assertEqual(self.storage.saved["workspace"]["core"]["sniffer"], expected)
        self.assertNotIn("sniffing", self.core.last_config)
        previous = copy.deepcopy(self.service.data)
        with self.assertRaises(RuntimeErrorMessage):
            self.service.relay("PATCH", "/configs", b'{"sniffing":"yes"}')
        self.assertEqual(self.service.data, previous)

    def test_node_latency_uses_saved_test_url(self):
        self.create()
        target = "https://example.test/check?a=one&b=two"
        self.workspace("app", {"autoRun": False, "testUrl": target, "checkCertificate": True})
        with patch.object(self.core, "request", return_value=(200, b'{"delay":42}')) as request:
            self.service.relay("GET", "/proxies/node%2Fone/delay?timeout=5000&url=https%3A%2F%2Fignored.test")
        method, path, _ = request.call_args.args
        self.assertEqual(method, "GET")
        self.assertTrue(path.startswith("/proxies/node%2Fone/delay?"))
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query), {"timeout": ["5000"], "url": [target]})

    def test_core_exit_restores_proxy_without_erasing_preferences(self):
        self.create()
        old = copy.deepcopy(self.service.data["network"])
        self.service.perform({"action": "network.save", "expected": old, "network": {**old, "systemProxy": True}})
        self.service.perform({"action": "running.set", "running": True})
        self.core.alive = False
        self.proxy.disable.side_effect = [RuntimeErrorMessage("temporary failure"), None]
        self.service.check_health()
        self.assertFalse(self.service.running)
        self.assertTrue(self.service.proxy_restore_pending)
        self.service.check_health()
        self.assertFalse(self.service.proxy_restore_pending)
        self.assertIsNone(self.service.proxy_signature)
        self.assertTrue(self.storage.saved["network"]["systemProxy"])
        self.assertIn("核心意外退出", self.service.state()["coreStatus"])
        self.service.check_health()
        self.assertEqual(self.proxy.disable.call_count, 2)

    def test_restart_failure_clears_running_state_and_proxy_lease(self):
        self.create()
        old = copy.deepcopy(self.service.data["network"])
        self.service.perform({"action": "network.save", "expected": old, "network": {**old, "systemProxy": True}})
        self.service.perform({"action": "running.set", "running": True})
        self.core.close = MagicMock()
        self.core.start = MagicMock(side_effect=RuntimeErrorMessage("核心启动失败"))

        with self.assertRaisesRegex(RuntimeErrorMessage, "核心启动失败"):
            self.service.perform({"action": "tool.run", "tool": "restart"})

        self.assertFalse(self.service.running)
        self.assertIsNone(self.service.proxy_signature)
        self.assertTrue(self.service.data["network"]["systemProxy"])
        self.core.close.assert_called_once()
        self.core.start.assert_called_once()
        self.proxy.disable.assert_called()


class PortPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.core = FakeCore(self.directory.name)
        self.service = Service(MemoryStorage(self.directory.name), self.core, MagicMock())
        self.service.perform({"action": "profile.import", "label": "Local test",
                              "data": base64.b64encode(YAML.encode()).decode()})

    def probe(self, recently_closed=False, occupied=False):
        probe = MagicMock()
        probe.__enter__.return_value = probe
        reusable = False

        def setsockopt(level, option, value):
            nonlocal reusable
            # Port sharing must never be enabled to bypass a real listener.
            self.assertEqual((level, option, value), (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1))
            reusable = True

        def bind(_address):
            if recently_closed and not reusable:
                raise OSError(errno.EADDRINUSE, "Address already in use")

        probe.setsockopt.side_effect = setsockopt
        probe.bind.side_effect = bind
        if occupied:
            # BSD can allow a reusable bind and reject the conflicting listen.
            probe.listen.side_effect = OSError(errno.EADDRINUSE, "Address already in use")
        return probe

    def test_repeated_start_stop_accepts_recently_closed_ports(self):
        probes = []

        def new_probe():
            probe = self.probe(recently_closed=bool(probes))
            probes.append(probe)
            return probe

        with patch("zfly_runtime.service.socket.socket", side_effect=new_probe), \
                patch("zfly_runtime.service.socket.create_connection", return_value=MagicMock()):
            for _ in range(3):
                self.service.perform({"action": "running.set", "running": True})
                self.assertTrue(self.service.running)
                self.service.perform({"action": "running.set", "running": False})
                self.assertFalse(self.service.running)
                self.assertEqual(self.core.last_config["mixed-port"], 0)

        self.assertEqual(len(probes), 3)
        for probe in probes:
            probe.listen.assert_called_once()
            probe.__exit__.assert_called_once()

    def test_occupied_listener_is_rejected_before_applying_config(self):
        probe = self.probe(occupied=True)
        before = copy.deepcopy(self.core.last_config)
        with patch("zfly_runtime.service.socket.socket", return_value=probe):
            with self.assertRaisesRegex(RuntimeErrorMessage, "端口 7890"):
                self.service.perform({"action": "running.set", "running": True})
        self.assertFalse(self.service.running)
        self.assertEqual(self.core.last_config, before)
        probe.__exit__.assert_called_once()

    def test_bind_conflict_is_still_rejected(self):
        probe = self.probe()
        probe.bind.side_effect = OSError(errno.EADDRINUSE, "Address already in use")
        with patch("zfly_runtime.service.socket.socket", return_value=probe):
            with self.assertRaisesRegex(RuntimeErrorMessage, "端口 7890"):
                self.service._preflight_ports(self.service._config(True))

    def test_lan_listener_checks_wildcard_address(self):
        probe = self.probe(recently_closed=True)
        config = {**self.service._config(True), "allow-lan": True}
        with patch("zfly_runtime.service.socket.socket", return_value=probe):
            self.service._preflight_ports(config)
        probe.bind.assert_called_once_with(("0.0.0.0", 7890))

    def test_running_core_keeps_its_existing_port(self):
        config = self.service._config(True)
        self.core.last_config = copy.deepcopy(config)
        self.service.running = True
        with patch("zfly_runtime.service.socket.socket") as factory:
            self.service._preflight_ports(config)
        factory.assert_not_called()


class BoundaryTests(unittest.TestCase):
    def test_yaml_rejects_bad_shapes_and_recursive_aliases(self):
        for content in ("rules: [42]", "dns: null", "proxy-providers: [one]", "proxies: [node]", "a: &a [*a]"):
            with self.subTest(content=content), self.assertRaises(RuntimeErrorMessage):
                parse_profile(content)

    def test_redirects_reject_downgrade_and_auth_leaks(self):
        handler = DownloadRedirects()
        request = urllib.request.Request("https://example.test/private", headers={"Authorization": "Basic secret"})
        for target in ("http://example.test/a", "https://elsewhere.test/a", "file:///tmp/test", "https://user:pass@example.test/a"):
            with self.assertRaises(RuntimeErrorMessage):
                handler.redirect_request(request, None, 302, "", {}, target)
        result = handler.redirect_request(request, None, 302, "", {}, "https://example.test/final")
        self.assertEqual(result.full_url, "https://example.test/final")

    def test_http_rejects_missing_auth_and_browser_origins(self):
        for origin, host, auth, expected in ((None, "127.0.0.1:4321", None, 401),
                                             ("https://evil.test", "127.0.0.1:4321", "Bearer token", 403),
                                             (None, "evil.test:4321", "Bearer token", 403),
                                             (None, "127.0.0.1:4321", "Bearer token", 200)):
            handler = object.__new__(Handler)
            handler.headers = Message()
            handler.headers["Host"] = host
            if origin:
                handler.headers["Origin"] = origin
            if auth:
                handler.headers["Authorization"] = auth
            handler.path, handler.command = "/state", "GET"
            handler.server = MagicMock(server_port=4321, secret="token")
            handler.server.service.state.return_value = {}
            handler._send = MagicMock()
            handler._handle()
            self.assertEqual(handler._send.call_args.args[0], expected)
            if expected != 200:
                handler.server.service.state.assert_not_called()

    def test_storage_is_private_atomic_and_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "data")
            try:
                store.save(default_state())
                self.assertEqual(store.directory.stat().st_mode & 0o777, 0o700)
                self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(store.load(), default_state())
                with self.assertRaises(RuntimeErrorMessage):
                    StateStore(store.directory)
                with patch("zfly_runtime.storage.os.replace", side_effect=OSError("disk")):
                    with self.assertRaises(OSError):
                        store.save({})
                self.assertEqual(store.load(), default_state())
                self.assertFalse(list(store.directory.glob(".zfly-*")))
            finally:
                store.close()

    def test_storage_rejects_deeply_invalid_state_shapes(self):
        invalid_values = [
            [],
            {"schema": 1, "profiles": [], "currentProfileID": None, "selections": {},
             "network": {}, "workspace": [], "catalog": {}},
            {**default_state(), "workspace": {"app": []}},
            {**default_state(), "workspace": {"app": {**default_state()["workspace"]["app"], "userAgent": "a\nb"}}},
            {**default_state(), "selections": {"missing": {}}},
        ]
        for invalid in invalid_values:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                store = StateStore(Path(directory) / "data")
                try:
                    store.path.write_text(json.dumps(invalid))
                    with self.assertRaisesRegex(RuntimeErrorMessage, "数据文件损坏"):
                        store.load()
                finally:
                    store.close()

    def test_proxy_lease_restores_only_fields_still_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            proxy = SystemProxy(directory, platform="darwin")
            before = {"enabled": False, "server": "old", "port": "8000"}
            target = {"enabled": True, "server": "127.0.0.1", "port": "7890"}
            proxy.path.write_text(json.dumps({"platform": "darwin", "services": {"Wi-Fi": {
                "before": {"web": before, "secureweb": before}, "target": target, "bypass": ["old"], "targetBypass": ["localhost"]}}}))
            proxy._get_proxy = MagicMock(side_effect=lambda _service, kind: target if kind == "web" else {**target, "port": "9999"})
            proxy._set_proxy = MagicMock()
            proxy._bypass = MagicMock(return_value=["user-change"])
            proxy._mac = MagicMock()
            proxy.disable()
            proxy._set_proxy.assert_called_once_with("Wi-Fi", "web", before)
            proxy._mac.assert_not_called()
            self.assertFalse(proxy.path.exists())

    def test_proxy_lease_rejects_malformed_nested_records(self):
        with tempfile.TemporaryDirectory() as directory:
            proxy = SystemProxy(directory, platform="darwin")
            proxy.path.write_text(json.dumps({"platform": "darwin", "services": {"Wi-Fi": {"before": {}}}}))
            with self.assertRaisesRegex(RuntimeErrorMessage, "恢复记录"):
                proxy.disable()
            self.assertTrue(proxy.path.exists())


if __name__ == "__main__":
    unittest.main()
