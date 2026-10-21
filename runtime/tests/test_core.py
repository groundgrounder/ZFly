import json
from pathlib import Path
import queue
import struct
import tempfile
import unittest
from unittest.mock import patch

from zfly_runtime.core import Core
from zfly_runtime.storage import RuntimeErrorMessage


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class FakeReservation:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def bind(self, _address):
        pass

    def getsockname(self):
        return ("127.0.0.1", 32123)


class FakeConnection:
    def __init__(self, payload):
        self.payload = payload

    def recv(self, length):
        if not self.payload:
            return b""
        chunk, self.payload = self.payload[:length], self.payload[length:]
        return chunk


class CoreTests(unittest.TestCase):
    def test_ipc_reader_rejects_non_object_responses_and_notifies_waiters(self):
        with tempfile.TemporaryDirectory() as directory:
            core = Core("/bin/true", Path(directory) / "home", "secret")
            payload = json.dumps(["unexpected"]).encode()
            connection = FakeConnection(struct.pack("<I", len(payload)) + payload)
            target = queue.Queue()
            core.connection = connection
            core.pending["request"] = target

            core._read(connection)

            self.assertIn("意外退出", core.error)
            self.assertEqual(target.get_nowait()["error"]["message"], core.error)

    def test_mihomo_start_timeout_reaps_process(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "mihomo"
            executable.touch()
            process = FakeProcess()
            core = Core(executable, Path(directory) / "home", "secret", kind="mihomo")
            with patch("zfly_runtime.core.subprocess.Popen", return_value=process), \
                    patch("zfly_runtime.core.socket.socket", return_value=FakeReservation()), \
                    patch.object(core, "request", side_effect=RuntimeErrorMessage("not ready")), \
                    patch("zfly_runtime.core.time.sleep"):
                with self.assertRaisesRegex(RuntimeErrorMessage, "启动超时"):
                    core.start()
            self.assertIsNone(core.process)
            self.assertFalse(core.alive)

    def test_selection_pruning_uses_live_nodes_and_keeps_backend_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            core = Core("/bin/true", Path(directory) / "home", "secret", kind="mihomo")
            calls = []
            def request(method, path, data=None):
                calls.append((method, path))
                if path == "/proxies":
                    return 200, json.dumps({"proxies": {"group": {"type": "Selector", "all": ["DIRECT"]}}}).encode()
                return 204, b""
            core.request = request
            selections = {"group": "removed", "missing": "DIRECT"}
            core.apply({"mode": "rule"}, selections)
            self.assertEqual(selections, {})
            self.assertEqual(calls, [("PUT", "/configs?force=true"), ("GET", "/proxies")])
            def failing(method, path, data=None):
                if path == "/proxies/group":
                    raise RuntimeErrorMessage("HTTP 500")
                return request(method, path, data)
            core.request = failing
            with self.assertRaisesRegex(RuntimeErrorMessage, "HTTP 500"):
                core.apply({"mode": "rule"}, {"group": "DIRECT"})

    def test_automatic_pins_apply_on_both_drivers_and_prune_stale_fixed(self):
        for kind in ("ipc", "mihomo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                core = Core("/bin/true", Path(directory) / "home", "secret", kind=kind)
                calls = []
                core.call = lambda *args: calls.append(args)
                def request(method, path, data=None):
                    calls.append((method, path))
                    if path == "/proxies":
                        return 200, json.dumps({"proxies": {
                            "Auto": {"type": "URLTest", "all": ["provider-node"], "fixed": "provider-node"},
                            "Failover": {"type": "Fallback", "all": ["provider-node"], "fixed": "removed"}
                        }}).encode()
                    return 204, b""
                core.request = request
                selections = {"Auto": "provider-node", "Failover": "removed"}
                core.apply({"mode": "rule"}, selections)
                self.assertEqual(selections, {"Auto": "provider-node"})
                if kind == "ipc":
                    self.assertIn(("DELETE", "/proxies/Failover"), calls)
                    self.assertFalse(any(call[:2] == ("PUT", "/proxies/Auto") for call in calls))
                else:
                    self.assertIn(("PUT", "/proxies/Auto"), calls)


if __name__ == "__main__":
    unittest.main()
