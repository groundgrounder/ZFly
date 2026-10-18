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

    def test_mihomo_selection_only_ignores_missing_group(self):
        with tempfile.TemporaryDirectory() as directory:
            core = Core("/bin/true", Path(directory) / "home", "secret", kind="mihomo")
            calls = []

            def request(method, path, data=None):
                calls.append((method, path, data))
                if path.startswith("/configs"):
                    return 204, b""
                raise RuntimeErrorMessage("核心拒绝操作（HTTP 500）：backend unavailable")

            core.request = request
            with self.assertRaisesRegex(RuntimeErrorMessage, "HTTP 500"):
                core.apply({"mode": "rule"}, {"group": "missing", "proxy": "DIRECT"})

            self.assertEqual(len(calls), 2)
            core.request = lambda method, path, data=None: (204, b"") if path.startswith("/configs") else (_ for _ in ()).throw(
                RuntimeErrorMessage("核心拒绝操作（HTTP 404）：not found"))
            core.apply({"mode": "rule"}, {"missing": "DIRECT"})
            self.assertEqual(core.last_config, {"mode": "rule"})


if __name__ == "__main__":
    unittest.main()
