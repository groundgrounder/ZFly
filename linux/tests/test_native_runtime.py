import subprocess
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from zfly.native_runtime import NativeRuntime
from zfly.controller import ControllerError

class NativeRuntimeTests(unittest.TestCase):
    def test_stop_reaps_a_wedged_owned_child(self):
        runtime = NativeRuntime()
        child = runtime.process = MagicMock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('owned', 3), 0]
        self.assertTrue(runtime.stop())
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertIsNone(runtime.process)

    def test_close_during_start_cancels_without_a_process_race(self):
        runtime = NativeRuntime()
        child = MagicMock()
        child.poll.return_value = None
        started = threading.Event()
        errors = []
        def launch(*args, **kwargs):
            started.set()
            return child
        def start():
            try:
                runtime.start()
            except ControllerError as error:
                errors.append(str(error))
        with patch('zfly.native_runtime.subprocess.Popen', side_effect=launch):
            worker = threading.Thread(target=start)
            worker.start()
            self.assertTrue(started.wait(1))
            self.assertTrue(runtime.stop())
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ['ZFly 启动已取消。'])
        self.assertIsNone(runtime.process)
        self.assertIsNone(runtime.directory)
