"""Owned core drivers: FlClash's standalone Go core or standard mihomo CLI."""
from collections import deque
import contextlib
import json
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

from .storage import RuntimeErrorMessage, atomic_write, encode_json

MAX_FRAME = 64 * 1024 * 1024


class Core:
    def __init__(self, executable, home, secret, kind="ipc"):
        self.executable = str(Path(executable).resolve())
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.secret, self.kind = secret, kind
        self.process = None
        self.connection = None
        self.pending = {}
        self.send_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.events = {name: deque(maxlen=500) for name in ("log", "dns", "request")}
        self.error = None
        self.endpoint = None
        self.last_config = None
        self.reader = None
        self.event_lock = threading.Lock()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        self.error = None
        if not Path(self.executable).is_file():
            raise RuntimeErrorMessage("应用缺少内置代理核心，请重新构建或安装完整的 ZFly。")
        # Select a private controller port. Never attach to an existing core.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.endpoint = "http://127.0.0.1:" + str(reservation.getsockname()[1])
        if self.kind == "mihomo":
            self.last_config = self.base_config()
            atomic_write(self.home / "config.yaml", encode_json(self.last_config))
            self.process = subprocess.Popen([self.executable, "-d", str(self.home), "-f", str(self.home / "config.yaml")],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if not self.alive:
                    self.close()
                    raise RuntimeErrorMessage("ZFly 内置核心启动失败。")
                try:
                    self.request("GET", "/version")
                    return
                except RuntimeErrorMessage:
                    time.sleep(0.05)
            self.close()
            raise RuntimeErrorMessage("等待 ZFly 核心启动超时。")
        # macOS limits Unix socket path length. Keep the socket in a private short directory.
        self.socket_directory = tempfile.TemporaryDirectory(prefix="zf-", dir="/tmp")
        socket_path = str(Path(self.socket_directory.name) / "core.sock")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(socket_path)
            listener.listen(1)
            listener.settimeout(10)
            self.process = subprocess.Popen([self.executable, socket_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.connection, _ = listener.accept()
            except (TimeoutError, OSError):
                self.close()
                raise RuntimeErrorMessage("ZFly 内置核心没有建立连接，核心版本可能不兼容。") from None
        self.reader = threading.Thread(target=self._read, args=(self.connection,), daemon=True, name="zfly-core-ipc")
        self.reader.start()
        try:
            if self.call("initClash", {"home-dir": str(self.home), "version": 0}) is not True:
                raise RuntimeErrorMessage("ZFly 核心初始化失败。")
            self.call("startLog")
            self.apply(self.base_config())
        except RuntimeErrorMessage:
            self.close()
            raise

    def base_config(self):
        return {"mixed-port": 0, "port": 0, "socks-port": 0, "allow-lan": False,
                "external-controller": self.endpoint.removeprefix("http://"), "secret": self.secret,
                "tun": {"enable": False}, "dns": {"enable": False}, "mode": "rule", "log-level": "info",
                "proxies": [], "proxy-groups": [], "rules": ["MATCH,DIRECT"]}

    def _exact(self, length, connection):
        result = bytearray()
        while len(result) < length:
            part = connection.recv(length - len(result))
            if not part:
                raise EOFError()
            result.extend(part)
        return result

    def _event(self, result):
        events = result.get("arguments", result.get("result", result))
        for event in events if isinstance(events, list) else [events]:
            if isinstance(event, dict) and event.get("type") in self.events:
                with self.event_lock:
                    self.events[event["type"]].append(event.get("data"))

    def history(self, kind):
        with self.event_lock:
            return list(self.events[kind])

    def _read(self, connection):
        try:
            while True:
                length = struct.unpack("<I", self._exact(4, connection))[0]
                if not 0 < length <= MAX_FRAME:
                    raise ValueError("invalid IPC frame")
                result = json.loads(self._exact(length, connection))
                if not isinstance(result, dict):
                    raise ValueError("invalid IPC response")
                spilled = result.get("file")
                if spilled is not None:
                    if not isinstance(spilled, str) or not spilled:
                        raise ValueError("invalid spilled response")
                    path = Path(spilled).resolve()
                    if not path.is_relative_to((self.home / "responses").resolve()) or path.stat().st_size > MAX_FRAME:
                        raise ValueError("invalid spilled response")
                    result = json.loads(path.read_bytes())
                    if not isinstance(result, dict):
                        raise ValueError("invalid spilled response")
                    path.unlink()
                identity = result.get("id")
                if identity is not None:
                    if not isinstance(identity, str) or not identity:
                        raise ValueError("invalid IPC response id")
                    with self.pending_lock:
                        target = self.pending.get(identity)
                    if target:
                        target.put(result)
                else:
                    self._event(result)
        except (OSError, ValueError, EOFError, KeyError):
            if self.connection is connection:
                self.error = "ZFly 核心意外退出，请在网络工具中重启核心。"
                with self.pending_lock:
                    for target in self.pending.values():
                        target.put({"error": {"message": self.error}})

    def call(self, method, arguments=None, timeout=60):
        if not self.alive or not self.connection:
            raise RuntimeErrorMessage(self.error or "ZFly 核心尚未启动。")
        identity = uuid.uuid4().hex
        target = queue.Queue()
        with self.pending_lock:
            self.pending[identity] = target
        try:
            payload = json.dumps({"id": identity, "method": method, "arguments": arguments}).encode()
            with self.send_lock:
                self.connection.sendall(struct.pack("<I", len(payload)) + payload)
            result = target.get(timeout=timeout)
            if result.get("error"):
                raise RuntimeErrorMessage(str(result["error"].get("message", "核心操作失败。")))
            value = result.get("result")
            if method in ("validateConfig", "setupConfig", "updateConfig", "updateExternalProvider", "updateGeoData") and value:
                raise RuntimeErrorMessage(str(value))
            return value
        except queue.Empty:
            raise RuntimeErrorMessage("核心操作超时，请重试或重启核心。") from None
        except OSError:
            raise RuntimeErrorMessage("ZFly 核心连接已中断。") from None
        finally:
            with self.pending_lock:
                self.pending.pop(identity, None)

    def request(self, method, path, data=None):
        request = urllib.request.Request(self.endpoint + path, data=data, method=method,
                                         headers={"Authorization": "Bearer " + self.secret, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=15) as response:
                return response.status, response.read(MAX_FRAME)
        except urllib.error.HTTPError as error:
            raise RuntimeErrorMessage("核心拒绝操作（HTTP " + str(error.code) + "）：" + error.read(4096).decode(errors="replace")) from None
        except (OSError, urllib.error.URLError):
            raise RuntimeErrorMessage("ZFly 核心没有响应，请重启核心。") from None

    def validate(self, content):
        path = self.home / "validation.yaml"
        atomic_write(path, content)
        try:
            if self.kind == "ipc":
                self.call("validateConfig", str(path))
            else:
                try:
                    result = subprocess.run([self.executable, "-t", "-d", str(self.home), "-f", str(path)],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
                except (OSError, subprocess.TimeoutExpired):
                    raise RuntimeErrorMessage("核心校验失败或超时，请检查配置中的远程资源。") from None
                if result.returncode:
                    raise RuntimeErrorMessage("配置校验失败：" + result.stdout.decode(errors="replace")[-2000:])
        finally:
            path.unlink(missing_ok=True)

    def decrypt(self, content, key):
        if self.kind != "ipc":
            raise RuntimeErrorMessage("此核心不提供 Age 解密，请导入已解密的 YAML。")
        path = self.home / "encrypted-profile"
        atomic_write(path, content)
        try:
            return self.call("decryptConfig", {"path": str(path), "age-secret-key": key})
        finally:
            path.unlink(missing_ok=True)

    def apply(self, config, selections=None, running=False):
        # Configuration staging never writes over a profile or imports a host app's data.
        path = self.home / "config.yaml"
        atomic_write(path, encode_json(config))
        if self.kind == "ipc":
            self.call("stopListener")
            self.call("setupConfig", {"selected-map": selections or {},
                                      "test-url": getattr(self, "test_url", "https://www.gstatic.com/generate_204"),
                                      "skip-cert-verify": not getattr(self, "check_certificate", True)})
            if running:
                self.call("startListener")
        else:
            self.request("PUT", "/configs?force=true", json.dumps({"path": str(path)}).encode())
            for group, proxy in (selections or {}).items():
                from urllib.parse import quote
                try:
                    self.request("PUT", "/proxies/" + quote(group, safe=""), json.dumps({"name": proxy}).encode())
                except RuntimeErrorMessage as error:
                    # A subscription may have removed a remembered group or node. Other
                    # failures (core disconnects, 4xx/5xx responses) must reach the UI.
                    if "HTTP 404" not in str(error):
                        raise
        self.last_config = config

    def close(self):
        connection, self.connection = self.connection, None
        if connection:
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        if self.reader:
            self.reader.join(timeout=2)
            self.reader = None
        if self.process:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None
        if hasattr(self, "socket_directory"):
            self.socket_directory.cleanup()
