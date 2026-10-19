"""Lifecycle regressions without starting a core or changing host networking."""
import copy
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from zfly_runtime import __main__ as runtime_main
from zfly_runtime.core import Core
from zfly_runtime.service import Service
from zfly_runtime.storage import RuntimeErrorMessage, StateStore, default_state, digest, encode_json


YAML = "proxies: []\nproxy-groups: []\nrules: [MATCH,DIRECT]\n"


class ServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = StateStore(self.directory.name)
        self.addCleanup(self.storage.close)
        data = default_state()
        data["profiles"] = [{"id": "profile", "label": "Test", "type": "url",
                             "url": "https://example.test/profile", "autoUpdate": True,
                             "updateHours": 24, "updated": "2000-01-01T00:00:00+00:00", "content": YAML}]
        data["currentProfileID"] = "profile"
        self.storage.save(data)
        self.core = MagicMock(kind="ipc", alive=True, endpoint="http://127.0.0.1:12345",
                              secret="test-only", home=Path(self.directory.name), last_config=None)
        self.proxy = MagicMock()
        self.fetcher = MagicMock(return_value=(YAML.encode(), {}, 200))
        self.service = Service(self.storage, self.core, self.proxy, self.fetcher)
        self.addCleanup(patch.stopall)
        patch.object(self.service, "_preflight_ports").start()
        patch("zfly_runtime.service.socket.create_connection", return_value=MagicMock()).start()

    def block_download(self):
        entered, release = threading.Event(), threading.Event()

        def fetch(*_args):
            entered.set()
            if not release.wait(5):
                raise RuntimeErrorMessage("test download was not released")
            return YAML.encode(), {}, 200

        self.fetcher.side_effect = fetch
        worker = threading.Thread(target=self.service.update_due, daemon=True)
        worker.start()
        self.addCleanup(lambda: worker.join(5))
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(1))
        return worker, release

    def test_auto_run_applies_the_saved_profile_only_once(self):
        self.service.data["workspace"]["app"]["autoRun"] = True
        self.service.start()
        self.core.start.assert_called_once()
        self.core.apply.assert_called_once()
        self.assertTrue(self.core.apply.call_args.args[2])
        self.assertTrue(self.service.running)

    def test_stopped_start_still_loads_the_saved_profile_once(self):
        self.service.start()
        self.core.apply.assert_called_once()
        self.assertFalse(self.core.apply.call_args.args[2])
        self.assertFalse(self.service.running)

    def test_status_reads_do_not_wait_for_subscription_download(self):
        worker, release = self.block_download()
        finished = threading.Event()
        reader = threading.Thread(target=lambda: (self.service.state(), finished.set()), daemon=True)
        reader.start()
        try:
            self.assertTrue(finished.wait(1), "state() waited for the subscription server")
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join(5)
            reader.join(5)
        self.assertFalse(worker.is_alive())

    def test_exit_during_download_does_not_wait_or_commit_after_close(self):
        before = self.storage.path.read_bytes()
        worker, release = self.block_download()
        finished = threading.Event()
        closer = threading.Thread(target=lambda: (self.service.close(), finished.set()), daemon=True)
        closer.start()
        try:
            self.assertTrue(finished.wait(1), "close() waited for the subscription server")
            self.assertTrue(worker.is_alive())
            self.proxy.disable.assert_called_once()
            self.core.close.assert_called_once()
        finally:
            release.set()
            worker.join(5)
            closer.join(5)
        self.assertFalse(worker.is_alive())
        self.core.validate.assert_not_called()
        self.core.apply.assert_not_called()
        self.assertEqual(self.storage.path.read_bytes(), before)
        self.assertTrue(self.storage.lock.closed)

    def test_subscription_edited_during_download_rejects_the_old_response(self):
        worker, release = self.block_download()
        finished = threading.Event()

        def edit():
            revision = digest(encode_json(self.service.data["profiles"][0]))
            self.service.perform({"action": "profile.edit", "id": "profile", "revision": revision,
                                  "url": "https://example.test/replacement"})
            finished.set()

        editor = threading.Thread(target=edit, daemon=True)
        editor.start()
        try:
            self.assertTrue(finished.wait(1), "editing waited for the subscription server")
        finally:
            release.set()
            worker.join(5)
            editor.join(5)
        self.assertFalse(worker.is_alive())
        self.core.validate.assert_not_called()
        self.assertEqual(self.storage.load()["profiles"][0]["url"], "https://example.test/replacement")

    def test_failed_background_apply_rolls_back_state_and_keeps_the_error(self):
        before = copy.deepcopy(self.service.data)
        self.core.apply.side_effect = [RuntimeErrorMessage("test apply failed"), None]
        self.service.update_due()
        self.assertEqual(self.service.data, before)
        self.assertEqual(self.storage.load(), before)
        self.assertEqual(self.core.apply.call_count, 2)
        self.core.validate.assert_called_once_with(YAML)
        self.assertEqual(self.service.update_errors, {"profile": "test apply failed"})

    def test_closed_service_never_starts_a_new_background_download(self):
        self.service.close()
        self.service.update_due()
        self.fetcher.assert_not_called()

    def test_exit_does_not_wait_for_an_inflight_status_response(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def request(*_args):
            entered.set()
            if not release.wait(5):
                raise RuntimeErrorMessage("test response was not released")
            return 200, b'{"version":"test-only"}'

        self.core.request.side_effect = request
        reader = threading.Thread(target=lambda: self.service.relay("GET", "/version"), daemon=True)
        reader.start()
        self.assertTrue(entered.wait(1))
        closer = threading.Thread(target=lambda: (self.service.close(), finished.set()), daemon=True)
        closer.start()
        try:
            self.assertTrue(finished.wait(1), "exit waited for a slow core status response")
            self.assertTrue(reader.is_alive())
            self.core.close.assert_called_once()
            with self.assertRaisesRegex(RuntimeErrorMessage, "正在退出"):
                self.service.relay("GET", "/version")
            self.assertEqual(self.core.request.call_count, 1)
        finally:
            release.set()
            reader.join(5)
            closer.join(5)


class CoreLifecycleTests(unittest.TestCase):
    def test_termination_cancels_an_inflight_ipc_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = threading.Event()
            core = Core("/bin/true", directory, "test-only", stop_event=stop)
            core.process = MagicMock()
            core.process.poll.return_value = None
            core.connection = MagicMock()
            sent, finished = threading.Event(), threading.Event()
            core.connection.sendall.side_effect = lambda *_args: sent.set()
            errors = []

            def call():
                try:
                    core.call("setupConfig")
                except RuntimeErrorMessage as error:
                    errors.append(str(error))
                finally:
                    finished.set()

            worker = threading.Thread(target=call, daemon=True)
            worker.start()
            try:
                self.assertTrue(sent.wait(1))
                stop.set()
                self.assertTrue(finished.wait(1), "exit waited for the 60-second IPC timeout")
                self.assertEqual(len(errors), 1)
                self.assertIn("正在退出", errors[0])
                self.assertEqual(core.pending, {})
            finally:
                stop.set()
                core.close()
                worker.join(1)

    def test_termination_cancels_the_startup_socket_wait_and_reaps_core(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = threading.Event()
            executable = Path(directory) / "core"
            executable.touch()
            core = Core(executable, directory, "test-only", stop_event=stop)
            listener = MagicMock()
            listener.getsockname.return_value = ("127.0.0.1", 12345)
            listener.__enter__.return_value = listener

            def accept():
                stop.set()
                raise TimeoutError()

            listener.accept.side_effect = accept
            process = MagicMock()
            process.poll.return_value = None
            with patch("zfly_runtime.core.socket.socket", return_value=listener), \
                    patch("zfly_runtime.core.subprocess.Popen", return_value=process):
                with self.assertRaisesRegex(RuntimeErrorMessage, "正在退出"):
                    core.start()
            process.terminate.assert_called_once()
            process.wait.assert_called_once()
            self.assertIsNone(core.process)

    def test_ipc_start_leaves_configuration_loading_to_the_service(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "core"
            executable.touch()
            core = Core(executable, directory, "test-only")
            listener = MagicMock()
            listener.getsockname.return_value = ("127.0.0.1", 12345)
            listener.accept.return_value = (MagicMock(), None)
            listener.__enter__.return_value = listener
            with patch("zfly_runtime.core.socket.socket", return_value=listener), \
                    patch("zfly_runtime.core.subprocess.Popen"), \
                    patch("zfly_runtime.core.threading.Thread"), \
                    patch.object(core, "call", return_value=True) as call, \
                    patch.object(core, "apply") as apply:
                core.start()
                self.assertEqual([item.args[0] for item in call.call_args_list], ["initClash", "startLog"])
                apply.assert_not_called()
            core.close()

    def test_close_signals_the_owned_process_before_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            core = Core("/bin/true", directory, "test-only", kind="mihomo")
            process = MagicMock()
            process.poll.return_value = None
            calls = []
            process.terminate.side_effect = lambda: calls.append("terminate")
            process.wait.side_effect = lambda **_kwargs: calls.append("wait")
            core.process = process
            pending = queue.Queue()
            core.pending["request"] = pending
            core.close()
            self.assertEqual(calls, ["terminate", "wait"])
            self.assertIn("正在停止", pending.get_nowait()["error"]["message"])
            self.assertIsNone(core.process)

    def test_unresponsive_owned_core_is_killed_and_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            core = Core("/bin/true", directory, "test-only")
            process = MagicMock()
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired("test-only", 3), 0]
            core.process = process
            core.close()
            process.terminate.assert_called_once()
            process.kill.assert_called_once()
            self.assertEqual(process.wait.call_count, 2)
            self.assertIsNone(core.process)


class MaintenanceSchedulingTests(unittest.TestCase):
    def test_initial_status_requests_are_not_raced_by_automatic_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = MagicMock(directory=Path(directory))
            stop = MagicMock()
            stop.wait.side_effect = [False, True]
            argv = ["zfly-runtime", "--core", "/test/core", "--ready-file", str(Path(directory) / "ready.json")]
            with patch.object(runtime_main.sys, "argv", argv), \
                    patch.object(runtime_main.signal, "signal"), \
                    patch.object(runtime_main.threading, "Event", return_value=stop), \
                    patch.object(runtime_main.threading, "Thread") as thread, \
                    patch.object(runtime_main, "StateStore", return_value=storage), \
                    patch.object(runtime_main, "Core") as core_type, \
                    patch.object(runtime_main, "SystemProxy"), \
                    patch.object(runtime_main, "Service") as service_type, \
                    patch.object(runtime_main, "Server"), \
                    patch.object(runtime_main.time, "monotonic", return_value=1000):
                self.assertEqual(runtime_main.main(), 0)
            self.assertEqual(thread.call_count, 1)
            self.assertIs(core_type.call_args.kwargs["stop_event"], stop)
            self.assertEqual(thread.call_args.kwargs["name"], "zfly-http")
            service_type.return_value.start.assert_called_once()
            service_type.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
