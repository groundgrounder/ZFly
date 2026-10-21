"""Preview exercises the production Service without accessing the host environment."""
import base64
import copy
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from zfly_runtime import __main__ as entry
from zfly_runtime.preview import DELAY_SECONDS, PreviewBackend
from zfly_runtime.storage import RuntimeErrorMessage


class PreviewServiceTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "subprocess.run", "socket.socket",
                       "socket.if_nameindex", "urllib.request.OpenerDirector.open",
                       "zfly_runtime.network.SystemProxy._run"):
            guard = patch(target, side_effect=AssertionError("preview accessed host: " + target))
            guard.start()
            self.addCleanup(guard.stop)
        self.backend = PreviewBackend()
        self.addCleanup(self.backend.close)
        self.service = self.backend.service
        self.service.start()

    def action(self, action, **values):
        return self.service.perform({"action": action, **values})

    def test_global_group_contains_proxies_and_groups_in_config_order(self):
        config = copy.deepcopy(self.service.core.last_config)
        for groups in (config["proxy-groups"], list(reversed(config["proxy-groups"]))):
            with self.subTest(groups=[item["name"] for item in groups]):
                config["proxy-groups"] = groups
                self.service.core.apply(config)
                proxies = json.loads(self.service.relay("GET", "/proxies")[1])["proxies"]
                self.assertIn("GLOBAL", proxies)
                global_group = proxies["GLOBAL"]
                names = [item["name"] for item in config.get("proxies", [])]
                group_names = [item["name"] for item in groups]
                self.assertEqual(global_group["type"], "Selector")
                self.assertEqual(global_group["now"], "DIRECT")
                self.assertEqual(global_group["all"], ["DIRECT", "REJECT"] + names + group_names)
                self.assertEqual([name for name in global_group["all"] if name in group_names], group_names)

    def test_global_selection_via_put_is_persisted_and_restored(self):
        selected = self.service.core.last_config["proxy-groups"][0]["name"]
        status, _ = self.service.relay("PUT", "/proxies/GLOBAL", json.dumps({"name": selected}).encode())
        self.assertEqual(status, 204)
        self.assertEqual(self.service.core.proxies["GLOBAL"]["now"], selected)
        identity = self.service.state()["currentProfileID"]
        self.assertEqual(self.service.storage.load()["selections"][identity]["GLOBAL"], selected)
        for tool in ("apply", "restart"):
            with self.subTest(tool=tool):
                self.action("tool.run", tool=tool)
                proxies = json.loads(self.service.relay("GET", "/proxies")[1])["proxies"]
                self.assertEqual(proxies["GLOBAL"]["now"], selected)

    def test_profile_crud_download_read_write_and_stale_rejection(self):
        for action in ("profile.import", "profile.create", "profile.add"):
            kwargs = {"label": action, "url": "https://example.invalid/subscription"}
            if action == "profile.import":
                kwargs["data"] = base64.b64encode(b"rules: [MATCH,DIRECT]\n").decode()
            result = self.action(action, **kwargs)
            profile = result["state"]["profiles"][-1]
            identity = profile["id"]
            self.action("profile.select", id=identity)
            file = self.action("profile.read", id=identity)["file"]
            self.assertTrue(base64.b64decode(file["data"]))
            if action == "profile.import":
                changed = base64.b64encode(b"rules: ['MATCH,REJECT']\n").decode()
                self.action("profile.write", id=identity, revision=file["revision"], data=changed)
                with self.assertRaises(RuntimeErrorMessage):
                    self.action("profile.write", id=identity, revision=file["revision"], data=changed)
                self.assertEqual(self.action("profile.read", id=identity)["file"]["data"], changed)
            else:
                with self.assertRaises(RuntimeErrorMessage):
                    self.action("profile.write", id=identity, revision=file["revision"], data=file["data"])
            revision = self.service.state()["profiles"][-1]["revision"]
            result = self.action("profile.edit", id=identity, revision=revision, label="renamed", updateHours=2)
            with self.assertRaises(RuntimeErrorMessage):
                self.action("profile.delete", id=identity, revision=revision)
            revision = result["state"]["profiles"][-1]["revision"]
            if action == "profile.add":
                self.assertEqual(profile["total"], 107374182400)
                self.action("profile.update", id=identity)
                revision = self.service.state()["profiles"][-1]["revision"]
            self.action("profile.delete", id=identity, revision=revision)
            with self.assertRaises(RuntimeErrorMessage):
                self.action("profile.read", id=identity)
        self.assertEqual(len(self.service.storage.load()["profiles"]), 2)
        with self.assertRaises(RuntimeErrorMessage):
            self.action("profile.add", url="file:///etc/passwd")
        with self.assertRaises(RuntimeErrorMessage):
            self.action("profile.import", data=base64.b64encode(b"rules: wrong").decode())

    def test_proxy_selections_automatic_fix_unfix_and_reapply(self):
        self.action("proxy.select", group="节点选择", proxy="新加坡")
        self.action("proxy.select", group="自动选择", proxy="新加坡")
        self.assertEqual(self.service.core.proxies["自动选择"]["fixed"], "新加坡")
        self.action("tool.run", tool="apply")
        self.assertEqual(self.service.core.proxies["节点选择"]["now"], "新加坡")
        self.assertEqual(self.service.core.proxies["自动选择"]["fixed"], "新加坡")
        self.action("proxy.unfix", group="自动选择")
        self.assertNotIn("fixed", self.service.core.proxies["自动选择"])
        self.assertNotIn("自动选择", self.service.storage.load()["selections"]["sample-local"])
        with self.assertRaises(RuntimeErrorMessage):
            self.action("proxy.select", group="自动选择", proxy="missing")
        # Fallback groups use the same fixed-node contract.
        config = copy.deepcopy(self.service.core.last_config)
        config["proxy-groups"][1]["type"] = "fallback"
        self.service.core.apply(config, {}, True)
        self.action("proxy.select", group="自动选择", proxy="新加坡")
        self.assertEqual(self.service.core.proxies["自动选择"]["fixed"], "新加坡")
        self.action("proxy.unfix", group="自动选择")
        self.assertNotIn("fixed", self.service.core.proxies["自动选择"])

    def test_workspace_validation_revision_and_core_configuration(self):
        for section, invalid in (("core", {"mixed-port": -1}), ("app", {"autoRun": "yes"}), ("dns", {"dns": []})):
            doc = self.action("workspace.read", section=section)["document"]
            with self.assertRaises(RuntimeErrorMessage):
                self.action("workspace.write", section=section, revision=doc["revision"], text=json.dumps(invalid))
            self.assertEqual(self.action("workspace.read", section=section)["document"], doc)
        doc = self.action("workspace.read", section="dns")["document"]
        self.action("workspace.write", section="dns", revision=doc["revision"], text='{"dns":{"enable":true}}')
        with self.assertRaises(RuntimeErrorMessage):
            self.action("workspace.write", section="dns", revision=doc["revision"], text="{}")
        self.action("core.configure", patch={"mode": "direct"}, expected={"mode": "rule"}, requestID="a" * 32)
        self.action("core.configure", patch={"mode": "direct"}, expected={"mode": "rule"}, requestID="a" * 32)
        self.assertEqual(self.service.storage.load()["network"]["mode"], "direct")
        with self.assertRaises(RuntimeErrorMessage):
            self.action("core.configure", patch={"mode": "global"}, expected={"mode": "rule"})
        for section in ("core", "dns", "tun", "geo", "app", "dav", "profileOverrides", "globalRules", "profileRules", "logs", "dnsQueries", "requests", "providers", "resources"):
            self.assertIn("revision", self.action("workspace.read", section=section, id="sample-local")["document"])

    def test_catalog_crud_references_and_validation(self):
        custom = self.action("profile.create", label="custom")["state"]["profiles"][-1]["id"]
        self.action("profile.select", id=custom)
        doc = self.action("catalog.read", section="proxies")["document"]
        result = self.action("catalog.save", section="proxies", revision=doc["revision"], text=doc["text"])
        proxy = result["items"][0]["id"]
        doc = self.action("catalog.read", section="groups", name=custom)["document"]
        group = json.loads(doc["text"])
        group["proxies"] = ["新节点"]
        result = self.action("catalog.save", section="groups", name=custom, revision=doc["revision"], text=json.dumps(group))
        group_id = result["items"][0]["id"]
        doc = self.action("catalog.read", section="proxies", id=proxy)["document"]
        with self.assertRaises(RuntimeErrorMessage):
            self.action("catalog.delete", section="proxies", id=proxy, revision=doc["revision"])
        value = json.loads(doc["text"])
        value["name"] = "renamed"
        self.action("catalog.save", section="proxies", id=proxy, revision=doc["revision"], text=json.dumps(value))
        doc = self.action("catalog.read", section="groups", id=group_id, name=custom)["document"]
        self.assertEqual(json.loads(doc["text"])["proxies"], ["renamed"])
        self.action("catalog.delete", section="groups", id=group_id, name=custom, revision=doc["revision"])
        doc = self.action("catalog.read", section="proxies", id=proxy)["document"]
        self.action("catalog.delete", section="proxies", id=proxy, revision=doc["revision"])
        self.assertEqual(self.action("catalog.list", section="proxies")["items"], [])

    def test_network_running_backup_restore_and_tools(self):
        previous = self.service.state()["network"]
        network = {**previous, "mixedPort": 12345, "tunEnabled": True}
        self.action("network.save", expected=previous, network=network)
        with self.assertRaises(RuntimeErrorMessage):
            self.action("network.save", expected=previous, network=previous)
        self.action("running.set", running=False)
        self.action("running.set", running=True)
        file = self.action("tool.run", tool="backupLocal")["file"]
        for tool in ("apply", "restart", "gc", "updateProfiles", "updateResources", "updateProvider", "closeConnections"):
            self.action("tool.run", tool=tool, name="sample")
        self.assertIn("updateGeoData", self.service.core.calls)
        self.assertIn("updateExternalProvider", self.service.core.calls)
        self.assertTrue(self.action("workspace.read", section="resources")["document"]["text"])
        doc = self.action("workspace.read", section="dav")["document"]
        self.action("workspace.write", section="dav", revision=doc["revision"], text=json.dumps({"uri": "https://example.invalid/dav", "fileName": "backup.json", "user": "sample", "password": "test-only"}))
        for tool in ("testDAV", "backupDAV", "restoreDAVProfiles", "restoreDAVAll", "restoreLocalProfiles", "restoreLocalAll"):
            self.action("tool.run", tool=tool, data=file["data"])
        self.assertFalse(self.service.state()["running"])
        self.assertEqual(self.service.storage.load()["network"], network)

    def test_failed_persistence_rolls_back_config_and_selection(self):
        before = self.service.storage.load()
        with patch.object(self.service.storage, "save", side_effect=OSError("test disk full")):
            with self.assertRaises(OSError):
                self.action("proxy.select", group="自动选择", proxy="新加坡")
            self.assertNotIn("fixed", self.service.core.proxies["自动选择"])
            network = {**before["network"], "mode": "global"}
            with self.assertRaises(OSError):
                self.action("network.save", expected=before["network"], network=network)
        self.assertEqual(self.service.data, before)
        self.assertEqual(self.service.storage.load(), before)
        self.assertEqual(self.service.core.last_config["mode"], before["network"]["mode"])

    def test_http_surface_traffic_delay_and_shutdown_cancellation(self):
        for route in ("/version", "/configs", "/proxies", "/connections", "/rules", "/dns/query?name=example.com&type=A", "/providers/proxies", "/providers/rules", "/memory"):
            self.assertEqual(self.service.relay("GET", route)[0], 200)
        first = json.loads(self.service.relay("GET", "/connections")[1])
        time.sleep(0.02)
        second = json.loads(self.service.relay("GET", "/connections")[1])
        self.assertGreater(second["downloadTotal"], first["downloadTotal"])
        started = time.monotonic()
        path = "/proxies/" + urllib.parse.quote("东京 / Tokyo", safe="") + "/delay"
        self.assertEqual(json.loads(self.service.relay("GET", path)[1])["delay"], 42)
        self.assertGreaterEqual(time.monotonic() - started, DELAY_SECONDS * 0.9)
        self.service.core.stop_event.set()
        with self.assertRaises(RuntimeErrorMessage):
            self.service.relay("GET", path)
        for route in ("/cache/dns/flush", "/cache/fakeip/flush"):
            self.assertEqual(self.service.relay("POST", route)[0], 204)
        self.service.relay("DELETE", "/connections/preview-1")
        self.assertEqual(len(json.loads(self.service.relay("GET", "/connections")[1])["connections"]), 1)
        self.service.relay("DELETE", "/connections")
        self.assertEqual(json.loads(self.service.relay("GET", "/connections")[1])["connections"], [])

    def test_validation_is_schema_only(self):
        self.service.core.validate("proxies: [{name: sample, type: unknown-protocol}]\nrules: []")
        for content in ("proxies: [wrong]", "mixed-port: invalid", "rules: ["):
            with self.assertRaises(RuntimeErrorMessage):
                self.service.core.validate(content)


class PreviewIsolationTests(unittest.TestCase):
    def test_main_binds_only_management_and_never_uses_host_adapters(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready.json"
            forbidden = Path(directory) / "user-data"
            event_type = threading.Event
            stop = event_type()
            events = iter([stop])
            bind = socket.socket.bind
            binds, state_directories = [], []
            write = entry.atomic_write

            def record_bind(sock, address):
                binds.append(address)
                self.assertEqual(address, ("127.0.0.1", 0))
                return bind(sock, address)

            def ready_write(path, data):
                write(path, data)
                if Path(path) == ready:
                    state_directories.append(Path(json.loads(data)["dataDirectory"]))
                    self.assertEqual(state_directories[-1].stat().st_mode & 0o777, 0o700)
                    self.assertEqual((state_directories[-1] / "state.json").stat().st_mode & 0o777, 0o600)
                    stop.set()

            argv = ["zfly-runtime", "--preview", "--core", "/forbidden", "--data-dir", str(forbidden), "--ready-file", str(ready)]
            with patch.object(sys, "argv", argv), patch.object(entry.signal, "signal"), \
                    patch.object(entry.threading, "Event", side_effect=lambda: next(events, None) or event_type()), \
                    patch.object(entry, "atomic_write", side_effect=ready_write), \
                    patch.object(socket.socket, "bind", record_bind), \
                    patch.object(socket.socket, "connect", side_effect=AssertionError("outbound connection")), \
                    patch("subprocess.Popen", side_effect=AssertionError("subprocess")) as popen, \
                    patch("subprocess.run", side_effect=AssertionError("runner")) as run, \
                    patch("zfly_runtime.network.SystemProxy._run", side_effect=AssertionError("system proxy")) as runner, \
                    patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("external fetch")) as fetch:
                self.assertEqual(entry.main(), 0)
                for mock in (popen, run, runner, fetch):
                    mock.assert_not_called()
            self.assertEqual(len(binds), 1)
            self.assertFalse(forbidden.exists())
            self.assertTrue(state_directories)
            self.assertFalse(state_directories[0].exists())

    def test_variants_independent_directories_and_no_host_calls_during_actions(self):
        with patch("subprocess.Popen", side_effect=AssertionError("subprocess")), \
                patch("subprocess.run", side_effect=AssertionError("runner")), \
                patch("socket.socket", side_effect=AssertionError("socket")), \
                patch("socket.if_nameindex", side_effect=AssertionError("interfaces")), \
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("external fetch")):
            directories = []
            for variant in ("default", "empty", "stopped"):
                with PreviewBackend(variant=variant) as backend:
                    service = backend.service
                    directories.append(service.storage.directory)
                    service.start()
                    self.assertEqual(service.state()["running"], variant == "default")
                    self.assertEqual(len(service.state()["profiles"]), 0 if variant == "empty" else 2)
                    service.perform({"action": "profile.add", "url": "https://example.invalid/sub"})
                    service.perform({"action": "running.set", "running": True})
                    service.perform({"action": "tool.run", "tool": "updateResources"})
                    self.assertTrue(service.system_proxy.calls)
                self.assertFalse(directories[-1].exists())
            self.assertEqual(len(set(directories)), 3)


class PreviewProcessTests(unittest.TestCase):
    def test_real_http_auth_actions_and_cleanup(self):
        self.run_preview()

    def test_parent_exit_detection_cleans_preview_directory(self):
        self.run_preview(parent_mismatch=True)

    def run_preview(self, parent_mismatch=False):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready.json"
            args = [sys.executable, "-B", "-m", "zfly_runtime", "--preview", "--data-dir", str(Path(directory) / "untouched"), "--ready-file", str(ready)]
            if parent_mismatch:
                args += ["--parent-pid", str(os.getpid() + 1000000)]
            child = subprocess.Popen(args, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            state_directory = None
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), "preview did not publish readiness")
                info = json.loads(ready.read_text())
                self.assertNotIn("error", info)
                state_directory = Path(info["dataDirectory"])
                if not parent_mismatch:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    def request(path, value=None, auth=True):
                        headers = {"Authorization": "Bearer " + info["secret"]} if auth else {}
                        req = urllib.request.Request(info["endpoint"] + path, headers=headers, data=json.dumps(value).encode() if value else None)
                        with opener.open(req, timeout=3) as response:
                            return json.load(response)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request("/state", auth=False)
                    self.assertEqual(error.exception.code, 401)
                    self.assertTrue(request("/state")["running"])
                    self.assertIn("节点选择", request("/proxies")["proxies"])
                    state = request("/action", {"action": "profile.add", "url": "https://unreachable.invalid/sub"})["state"]
                    self.assertEqual(len(state["profiles"]), 3)
                    self.assertEqual(request("/action", {"action": "running.set", "running": False})["state"]["running"], False)
                    child.send_signal(signal.SIGTERM)
                self.assertEqual(child.wait(timeout=10), 0)
                self.assertFalse(state_directory.exists())
                self.assertFalse((Path(directory) / "untouched").exists())
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
                child.stderr.close()
                if state_directory:
                    self.assertFalse(state_directory.exists())


if __name__ == "__main__":
    unittest.main()
