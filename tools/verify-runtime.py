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
import urllib.request

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
    except PermissionError:
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
                assert error.code == 401
            state = request("GET", "/state")
            assert not state["running"] and not state["network"]["systemProxy"] and not state["network"]["tunEnabled"]
            config = "proxies: []\nproxy-groups:\n  - name: Local Test\n    type: select\n    proxies: [DIRECT, REJECT]\nrules:\n  - MATCH,Local Test\n"
            state = action("profile.import", label="Isolated smoke test", data=base64.b64encode(config.encode()).decode())["state"]
            identity = state["currentProfileID"]
            network = {**state["network"], "mixedPort": port}
            action("network.save", expected=state["network"], network=network)
            for cycle in range(3):
                state = action("running.set", running=True)["state"]
                assert state["running"]
                # Verify the inbound proxy responds locally; do not request any destination.
                with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
                    connection.sendall(b"\x05\x01\x00")
                    assert connection.recv(2) == b"\x05\x00", "SOCKS5 handshake failed"
                if cycle < 2:
                    assert not action("running.set", running=False)["state"]["running"]
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
            assert state["currentProfileID"] == identity and not state["running"]
            assert state["network"]["mode"] == "global"
            assert request("GET", "/proxies")["proxies"]["Local Test"]["now"] == "REJECT"
            assert action("running.set", running=True)["state"]["running"]
            action("tool.run", tool="restoreLocalAll", data=archive["data"])
            assert not request("GET", "/state")["running"]
            assert_proxy_port_released()
            assert (scratch / "data/state.json").stat().st_mode & 0o777 == 0o600
            assert not (scratch / "data/proxy-lease.json").exists()
            print("PASS: owned core start/stop/restart, SOCKS5 handshake, import, selection, persistence, restore, auth and private state.")
            return 0
        finally:
            stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "build/macos/ZFly.app")
    sys.exit(verify(parser.parse_args().app.resolve()))
