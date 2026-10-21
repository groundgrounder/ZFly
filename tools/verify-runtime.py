#!/usr/bin/env python3
"""Isolated, loopback-only end-to-end check of a packaged ZFly runtime.

Owns its data directory and child PID. Never enables TUN/system proxy or makes
external requests. Exit 77 means the host sandbox prevents local socket binding.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import errno
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def verify_automatic_groups(action, request):
    """Healthy direct nodes exercise pin persistence without external traffic."""
    class Healthy(BaseHTTPRequestHandler):
        def do_HEAD(self):
            time.sleep(0.025)
            self.send_response(204)
            self.end_headers()
        do_GET = do_HEAD
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Healthy)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = "http://127.0.0.1:" + str(server.server_port)
        doc = action("workspace.read", section="app")["document"]
        preferences = json.loads(doc["text"])
        preferences["testUrl"] = url
        action("workspace.write", section="app", revision=doc["revision"], text=json.dumps(preferences))
        groups = ("Auto/% 🧪", "Failover/% 🧪")
        config = {"proxies": [{"name": node, "type": "direct"} for node in ("A", "B")],
                  "proxy-groups": [{"name": name, "type": kind, "proxies": ["A", "B"], "url": url,
                                    "interval": 86400, "lazy": True}
                                   for name, kind in zip(groups, ("url-test", "fallback"))],
                  "rules": ["MATCH,DIRECT"]}
        imported = action("profile.import", label="Automatic groups", data=base64.b64encode(json.dumps(config, ensure_ascii=False).encode()).decode())["state"]
        identity = next(p["id"] for p in imported["profiles"] if p["label"] == "Automatic groups")
        action("profile.select", id=identity)
        for node in ("A", "B"):
            request("GET", "/proxies/" + node + "/delay?timeout=1000")
        def check(fixed):
            proxies = request("GET", "/proxies")["proxies"]
            for group in groups:
                value = proxies[group]
                if (value.get("fixed") or "") != fixed or (fixed and value.get("now") != fixed):
                    raise RuntimeError("Automatic group verification failed: " + repr({group: value}))
        for group in groups:
            action("proxy.select", group=group, proxy="B")
        check("B")
        action("tool.run", tool="restart")
        check("B")
        created = action("profile.create", label="Switch target")["state"]
        other = next(p["id"] for p in created["profiles"] if p["label"] == "Switch target")
        if other == identity:
            raise RuntimeError("Profile switch did not create another profile")
        action("profile.select", id=other)
        action("profile.select", id=identity)
        check("B")
        for group in groups:
            request("DELETE", "/proxies/" + urllib.parse.quote(group, safe=""))
        check("")
        action("tool.run", tool="restart")
        check("")
        print("PASS: URLTest/Fallback pin, unfix, core restart, profile switch and encoded group names.")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)

ROOT = Path(__file__).resolve().parents[1]


def verify(app):
    resources = app / "Contents/Resources"
    manifest = json.loads((resources / "runtime-manifest.json").read_text())
    python = resources / "python/bin/python3"
    core = app / "Contents/Helpers/ZFlyCore"
    try:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
    except OSError as error:
        if error.errno not in (errno.EACCES, errno.EPERM):
            raise
        print("SKIP: this sandbox prohibits loopback socket binding; no processes or host proxy settings were changed.")
        return 77
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="zfly-integration-") as directory:
        scratch = Path(directory)
        ready_file = scratch / "ready.json"
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(resources / "runtime"),
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
               "SSL_CERT_FILE": str(resources / "python/cert.pem")}
        process = None
        def start():
            nonlocal process
            ready_file.unlink(missing_ok=True)
            process = subprocess.Popen([str(python), "-B", "-m", "zfly_runtime", "--core", str(core),
                "--core-kind", manifest["coreKind"], "--resources", str(resources / "geodata"),
                "--data-dir", str(scratch / "data"), "--ready-file", str(ready_file),
                "--parent-pid", str(os.getpid())], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("Owned runtime PID:", process.pid)
            for _ in range(300):
                if ready_file.exists():
                    ready = json.loads(ready_file.read_text())
                    if "error" in ready:
                        raise RuntimeError(ready["error"])
                    return ready
                if process.poll() is not None:
                    raise RuntimeError("Runtime exited before becoming ready")
                time.sleep(0.1)
            raise RuntimeError("Runtime startup timeout")

        def stop():
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        def request(method, path, value=None, auth=True):
            body = json.dumps(value).encode() if value is not None else None
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["Authorization"] = "Bearer " + ready["secret"]
            req = urllib.request.Request(ready["endpoint"] + path, data=body, method=method, headers=headers)
            with opener.open(req, timeout=30) as response:
                data = response.read()
                return json.loads(data) if data else None

        def action(name, **values):
            return request("POST", "/action", {"action": name, **values})

        def assert_proxy_port_released():
            with socket.socket() as check:
                check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                check.bind(("127.0.0.1", port))
                check.listen(1)

        try:
            ready = start()
            try:
                request("GET", "/state", auth=False)
                raise AssertionError("Missing authentication accepted")
            except urllib.error.HTTPError as error:
                if not (error.code == 401):
                    raise RuntimeError('Runtime verification failed: error.code == 401')
            state = request("GET", "/state")
            if not (not state["running"] and not state["network"]["systemProxy"] and not state["network"]["tunEnabled"]):
                raise RuntimeError('Runtime verification failed: not state["running"] and not state["network"]["systemProxy"] and not state["network"]["tunEnabled"]')
            config = "proxies: []\nproxy-groups:\n  - name: Local Test\n    type: select\n    proxies: [DIRECT, REJECT]\nrules:\n  - MATCH,Local Test\n"
            state = action("profile.import", label="Isolated smoke test", data=base64.b64encode(config.encode()).decode())["state"]
            identity = state["currentProfileID"]
            network = {**state["network"], "mixedPort": port}
            action("network.save", expected=state["network"], network=network)
            for cycle in range(3):
                state = action("running.set", running=True)["state"]
                if not (state["running"]):
                    raise RuntimeError('Runtime verification failed: state["running"]')
                # Verify the inbound proxy responds locally; do not request any destination.
                with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
                    connection.sendall(b"\x05\x01\x00")
                    if not (connection.recv(2) == b"\x05\x00"):
                        raise RuntimeError("SOCKS5 handshake failed")
                if cycle < 2:
                    if not (not action("running.set", running=False)["state"]["running"]):
                        raise RuntimeError('Runtime verification failed: not action("running.set", running=False)["state"]["running"]')
                    assert_proxy_port_released()
            action("proxy.select", group="Local Test", proxy="REJECT")
            request("PATCH", "/configs", {"mode": "global"})
            archive = action("tool.run", tool="backupLocal")["file"]
            action("running.set", running=False)
            assert_proxy_port_released()
            action("tool.run", tool="restart")
            stop()
            ready = start()
            state = request("GET", "/state")
            if not (state["currentProfileID"] == identity and not state["running"]):
                raise RuntimeError('Runtime verification failed: state["currentProfileID"] == identity and not state["running"]')
            if not (state["network"]["mode"] == "global"):
                raise RuntimeError('Runtime verification failed: state["network"]["mode"] == "global"')
            if not (request("GET", "/proxies")["proxies"]["Local Test"]["now"] == "REJECT"):
                raise RuntimeError('Runtime verification failed: request("GET", "/proxies")["proxies"]["Local Test"]["now"] == "REJECT"')
            if not (action("running.set", running=True)["state"]["running"]):
                raise RuntimeError('Runtime verification failed: action("running.set", running=True)["state"]["running"]')
            action("tool.run", tool="restoreLocalAll", data=archive["data"])
            if not (not request("GET", "/state")["running"]):
                raise RuntimeError('Runtime verification failed: not request("GET", "/state")["running"]')
            assert_proxy_port_released()
            if not ((scratch / "data/state.json").stat().st_mode & 0o777 == 0o600):
                raise RuntimeError('Runtime verification failed: (scratch / "data/state.json").stat().st_mode & 0o777 == 0o600')
            if not (not (scratch / "data/proxy-lease.json").exists()):
                raise RuntimeError('Runtime verification failed: not (scratch / "data/proxy-lease.json").exists()')
            verify_automatic_groups(action, request)
            print("PASS: owned core start/stop/restart, SOCKS5 handshake, import, selection, persistence, restore, auth and private state.")
            return 0
        finally:
            stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "build/macos/ZFly.app")
    sys.exit(verify(parser.parse_args().app.resolve()))
