#!/usr/bin/env python3
"""Render all macOS preview pages and inspect only the processes launched here.

Requires a built app and a macOS graphical session. Never uses LaunchServices to
launch the app, never inspects an installed ZFly, and never changes networking.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PAGES = {"overview": "概览", "profiles": "订阅", "proxies": "代理", "connections": "连接",
         "catalog": "资源", "workspace": "规则", "tools": "工具", "settings": "设置"}


def output(*args):
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()


def exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def inspect_runtime(pid):
    arguments = shlex.split(output("/bin/ps", "-p", str(pid), "-o", "args="))
    if not arguments:
        return None
    if "zfly_runtime" not in arguments or "--preview" not in arguments:
        raise RuntimeError("Unexpected child process of this preview: " + str(pid))
    session = Path(arguments[arguments.index("--ready-file") + 1]).parent
    files = output("/usr/sbin/lsof", "-nP", "-p", str(pid), "-Fn").splitlines()
    directories = {str(Path(line[1:]).parent) for line in files if line.startswith("n") and line.endswith("/runtime.lock")}
    listeners = [line[1:] for line in output("/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn").splitlines() if line.startswith("n")]
    if any(not address.startswith("127.0.0.1:") for address in listeners) or len(listeners) > 1:
        raise RuntimeError("Preview opened a listener beyond its local management port")
    grandchildren = output("/usr/bin/pgrep", "-P", str(pid)).split()
    if grandchildren:
        raise RuntimeError("Preview runtime spawned subprocesses: " + ", ".join(grandchildren))
    return {"pid": pid, "sessionDirectory": str(session), "dataDirectories": sorted(directories), "listeners": listeners}


def render(binary, directory, variant, page, label):
    name = variant + "-" + page
    screenshot = directory / (name + ".png")
    # A previous image must never make a failed run look successful.
    screenshot.unlink(missing_ok=True)
    environment = {k: v for k, v in os.environ.items() if k not in ("ZFLY_DEV_PYTHON", "ZFLY_DEV_RUNTIME")}
    children, records = set(), {}
    started = time.monotonic()
    with (directory / (name + ".log")).open("wb") as log:
        app = subprocess.Popen([str(binary), "--safe-mode", "--preview-state", variant,
                                "--preview-page", label, "--render-preview", str(screenshot)],
                               stdout=log, stderr=log, env=environment)
        (directory / (name + ".pid")).write_text(str(app.pid) + "\n")
        print(name + ": app PID " + str(app.pid), flush=True)
        try:
            while app.poll() is None:
                if time.monotonic() - started > 30:
                    raise RuntimeError("Preview render timed out: " + name)
                for value in output("/usr/bin/pgrep", "-P", str(app.pid)).split():
                    pid = int(value)
                    children.add(pid)
                    record = inspect_runtime(pid)
                    if record:
                        old = records.get(pid, {})
                        record["dataDirectories"] = sorted(set(record["dataDirectories"] + old.get("dataDirectories", [])))
                        record["listeners"] = sorted(set(record["listeners"] + old.get("listeners", [])))
                        records[pid] = record
                time.sleep(0.02)
            if app.wait() != 0 or not screenshot.is_file():
                raise RuntimeError("Preview did not produce an image: " + name)
            header = screenshot.read_bytes()[:24]
            if header[:8] != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError("Invalid preview PNG: " + name)
            width, height = struct.unpack(">II", header[16:24])
            if width < 900 or height < 600:
                raise RuntimeError("Unexpected preview dimensions: " + name)
            if not records or not any(item["listeners"] for item in records.values()):
                raise RuntimeError("Did not observe the preview management listener: " + name)
            for pid in children:
                if exists(pid):
                    raise RuntimeError("Runtime survived a normal preview exit: " + str(pid))
            for record in records.values():
                for path in record["dataDirectories"] + [record["sessionDirectory"]]:
                    if Path(path).exists():
                        raise RuntimeError("Preview directory survived normal exit: " + path)
            return {"state": variant, "page": label, "appPID": app.pid, "runtime": list(records.values()),
                    "image": str(screenshot.relative_to(ROOT) if screenshot.is_relative_to(ROOT) else screenshot), "width": width, "height": height,
                    "exitCode": app.returncode, "cleanupVerified": True}
        finally:
            # PIDs come only from this task's app and its observed children.
            if app.poll() is None:
                app.send_signal(signal.SIGTERM)
                try:
                    app.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    app.kill()
                    app.wait()
            deadline = time.monotonic() + 5
            while any(exists(pid) for pid in children) and time.monotonic() < deadline:
                time.sleep(0.05)
            for pid in children:
                if exists(pid):
                    os.kill(pid, signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "build/macos/ZFly.app")
    parser.add_argument("--output", type=Path, default=ROOT / "build/macos/preview-backend-review")
    args = parser.parse_args()
    binary = args.app.resolve() / "Contents/MacOS/ZFly"
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    for variant in ("default", "empty", "stopped"):
        for page, label in PAGES.items():
            results.append(render(binary, directory, variant, page, label))
            (directory / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print("Verified 24 screenshots, owned process trees, management listeners and directory cleanup.")


if __name__ == "__main__":
    main()
