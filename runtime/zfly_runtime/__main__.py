"""Private application service. Endpoints and credentials are generated per launch."""
import argparse
import contextlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sys
import threading
import time
import urllib.parse

from .core import Core, MAX_FRAME
from .network import SystemProxy
from .service import Service
from .storage import StateStore, RuntimeErrorMessage, atomic_write, encode_json


def default_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/ZFly"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "zfly"


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, service, secret):
        self.service, self.secret = service, secret
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ZFly"

    def log_message(self, *args):
        pass  # Do not log URLs, profile contents, tokens or credentials.

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        # Refuse browser origins and DNS rebinding before accepting any local operation.
        expected_host = "127.0.0.1:" + str(self.server.server_port)
        if self.headers.get("Origin") or self.headers.get("Host") != expected_host:
            self.close_connection = True
            self._send(403, b'{"message":"Local application access only"}')
            return
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.secret):
            self.close_connection = True
            self._send(401, b'{"message":"Unauthorized"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_FRAME or self.headers.get("Transfer-Encoding"):
                raise RuntimeErrorMessage("请求过大或编码无效。")
            payload = self.rfile.read(length) if length else None
            if payload is not None and len(payload) != length:
                raise RuntimeErrorMessage("请求未完整接收。")
            if self.path == "/state" and self.command == "GET":
                status, body = 200, encode_json(self.server.service.state()).encode()
            elif self.path == "/action" and self.command == "POST":
                result = self.server.service.perform(json.loads(payload or b"{}"))
                status, body = 200, encode_json(result).encode()
            else:
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.scheme or parsed.netloc or not self.path.startswith("/"):
                    raise RuntimeErrorMessage("请求路径无效。")
                allowed = ("/version", "/configs", "/proxies", "/connections", "/rules", "/dns", "/cache", "/providers", "/memory")
                if not any(parsed.path == item or parsed.path.startswith(item + "/") for item in allowed):
                    raise RuntimeErrorMessage("不支持的核心接口。")
                status, body = self.server.service.relay(self.command, self.path, payload)
            self._send(status, body)
        except (RuntimeErrorMessage, ValueError, TypeError, KeyError) as error:
            message = str(error) if isinstance(error, RuntimeErrorMessage) else "操作数据格式无效。"
            self._send(400, encode_json({"message": message}).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._send(500, encode_json({"message": "ZFly 内部操作失败，原有配置已保留。请重试。"}).encode())

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle


def main():
    parser = argparse.ArgumentParser(description="ZFly 独立应用运行时")
    parser.add_argument("--data-dir", type=Path, default=default_directory())
    parser.add_argument("--core", default=os.environ.get("ZFLY_CORE"))
    parser.add_argument("--core-kind", choices=("ipc", "mihomo"), default=os.environ.get("ZFLY_CORE_KIND", "ipc"))
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    os.umask(0o077)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    secret = secrets.token_urlsafe(32)
    storage = service = server = None
    try:
        storage = StateStore(args.data_dir)
        core_path = args.core or shutil.which("mihomo")
        if not core_path:
            raise RuntimeErrorMessage("应用缺少内置代理核心，请重新安装完整的 ZFly。")
        core_home = storage.directory / "core"
        core_home.mkdir(mode=0o700, exist_ok=True)
        if args.resources and args.resources.is_dir():
            for name in ("GEOIP.dat", "GEOSITE.dat", "GEOIP.metadb", "ASN.mmdb", "geoip.dat", "geosite.dat", "country.mmdb"):
                source = args.resources / name
                target = core_home / name
                if source.is_file() and not target.exists():
                    shutil.copyfile(source, target)
        core = Core(core_path, core_home, secret, args.core_kind if args.core else "mihomo", stop_event=stop)
        service = Service(storage, core, SystemProxy(storage.directory))
        service.start()
        server = Server(service, secret)
        atomic_write(args.ready_file, encode_json({"endpoint": "http://127.0.0.1:" + str(server.server_port),
                                                  "secret": secret, "pid": os.getpid(), "dataDirectory": str(storage.directory)}))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True, name="zfly-http").start()
        # Let the native app finish its first status requests before maintenance.
        last_update = last_health_check = time.monotonic()
        while not stop.wait(0.25):
            if args.parent_pid and os.getppid() != args.parent_pid:
                break
            if time.monotonic() - last_health_check >= 1:
                last_health_check = time.monotonic()
                service.check_health()
            if time.monotonic() - last_update >= 60:
                last_update = time.monotonic()
                # Keep termination responsive while subscriptions download.
                if not any(t.name == "zfly-auto-update" for t in threading.enumerate()):
                    threading.Thread(target=service.update_due, name="zfly-auto-update", daemon=True).start()
        return 0
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeErrorMessage) else "ZFly 运行时启动失败，请检查应用资源和数据目录权限。"
        atomic_write(args.ready_file, encode_json({"error": message}))
        return 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if service:
            with contextlib.suppress(Exception):
                service.close()
        elif storage:
            storage.close()


if __name__ == "__main__":
    sys.exit(main())
