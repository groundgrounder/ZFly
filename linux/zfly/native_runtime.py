"""Own the private service for the lifetime of this GTK application."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading

from .controller import ControllerError


class NativeRuntime:
    def __init__(self):
        self.process = None
        self.directory = None
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()

    def start(self):
        with self._lock:
            self._stop_requested.clear()
            return self._start_locked()

    def _start_locked(self):
        if self.process is not None and self.process.poll() is not None:
            self.stop()
        if self.process is not None:
            raise ControllerError("ZFly 运行时已启动。")
        self.directory = tempfile.TemporaryDirectory(prefix="zfly-session-")
        ready = Path(self.directory.name) / "ready.json"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        source_runtime = Path(__file__).resolve().parents[2] / "runtime"
        if source_runtime.is_dir():
            env["PYTHONPATH"] = str(source_runtime) + os.pathsep + env.get("PYTHONPATH", "")
        arguments = [sys.executable, "-B", "-m", "zfly_runtime", "--ready-file", str(ready), "--parent-pid", str(os.getpid())]
        if core := env.get("ZFLY_CORE"):
            arguments += ["--core", core, "--core-kind", env.get("ZFLY_CORE_KIND", "mihomo")]
        if geodata := env.get("ZFLY_GEODATA"):
            arguments += ["--resources", geodata]
        try:
            self.process = subprocess.Popen(arguments, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(900):
                if self._stop_requested.is_set():
                    raise ControllerError("ZFly 启动已取消。")
                if ready.is_file():
                    result = json.loads(ready.read_text())
                    ready.unlink()
                    if result.get("error"):
                        raise ControllerError(result["error"])
                    if not all(isinstance(result.get(key), str) and result[key] for key in ("endpoint", "secret", "dataDirectory")):
                        raise ControllerError("ZFly 运行时返回无效的启动信息。")
                    return result
                if self.process.poll() is not None:
                    raise ControllerError("ZFly 运行时退出，请检查包内核心与 Python 运行时。")
                time.sleep(0.1)
            raise ControllerError("ZFly 核心启动超时。")
        except Exception:
            self.stop()
            raise

    def stop(self):
        self._stop_requested.set()
        with self._lock:
            return self._stop_locked()

    def _stop_locked(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            # The normal runtime restores its lease before exit. A wedged owned
            # child is killed after a grace period; its lease remains recoverable.
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None
        if self.directory:
            self.directory.cleanup()
            self.directory = None
        return True
