#!/usr/bin/env python3
"""Assemble a relocatable macOS runtime; never launch or modify a source app."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def python_home():
    if value := os.environ.get("ZFLY_PYTHON_HOME"):
        return Path(value).expanduser().resolve()
    candidates = [ROOT / "build/dependencies/python"]
    uv = Path.home() / ".local/share/uv/python"
    arch = "aarch64" if platform.machine() == "arm64" else "x86_64"
    candidates += sorted(uv.glob(f"cpython-3.12.*-macos-{arch}-none"), reverse=True)
    for path in candidates:
        if (path / "bin/python3.12").is_file():
            return path
    raise SystemExit("需要可重定位的 CPython 3.12（python-build-standalone）。将其放入 build/dependencies/python，或设置 ZFLY_PYTHON_HOME。")


def core_source():
    candidates = [Path(os.environ["ZFLY_CORE"]).expanduser()] if os.environ.get("ZFLY_CORE") else [
        ROOT / "build/dependencies/ZFlyCore", ROOT / "build/dependencies/mihomo",
        Path("/Applications/FlClash.app/Contents/MacOS/FlClashCore"),
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            kind = os.environ.get("ZFLY_CORE_KIND", "mihomo" if "mihomo" in path.name.lower() else "ipc")
            if kind not in ("ipc", "mihomo"):
                raise SystemExit("ZFLY_CORE_KIND 必须是 ipc 或 mihomo")
            return path.resolve(), kind
    raise SystemExit("缺少核心：设置 ZFLY_CORE 为独立 Go 核心或 mihomo 的可执行文件路径。")


def assemble(app):
    source_python = python_home()
    source_core, kind = core_source()
    resources = app / "Contents/Resources"
    python = resources / "python"
    # These directories contain only generated app resources, rebuilt on every run.
    for path in (python, resources / "runtime", resources / "geodata"):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
    (python / "bin").mkdir()
    (python / "lib").mkdir()
    shutil.copy2(source_python / "bin/python3.12", python / "bin/python3.12", follow_symlinks=True)
    (python / "bin/python3").symlink_to("python3.12")
    shutil.copy2(source_python / "lib/libpython3.12.dylib", python / "lib/libpython3.12.dylib", follow_symlinks=True)
    shutil.copytree(source_python / "lib/python3.12", python / "lib/python3.12",
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip"))
    shutil.copyfile(os.environ.get("SSL_CERT_FILE", "/etc/ssl/cert.pem"), python / "cert.pem")
    shutil.copytree(ROOT / "runtime/zfly_runtime", resources / "runtime/zfly_runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    helpers = app / "Contents/Helpers"
    helpers.mkdir(exist_ok=True)
    # Replace the file instead of overwriting an inode from a previously signed build.
    staged_core = helpers / ".ZFlyCore.new"
    shutil.copy2(source_core, staged_core)
    os.chmod(staged_core, 0o755)
    staged_core.replace(helpers / "ZFlyCore")
    geodata = Path(os.environ.get("ZFLY_GEODATA", str(ROOT / "FlClash/assets/data")))
    for name in ("GEOIP.dat", "GEOSITE.dat", "GEOIP.metadb", "ASN.mmdb", "country.mmdb"):
        if (geodata / name).is_file():
            shutil.copyfile(geodata / name, resources / "geodata" / name)
    licenses = resources / "Licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", licenses / "ZFly-GPL-3.0.txt")
    shutil.copyfile(source_python / "lib/python3.12/LICENSE.txt", licenses / "Python.txt")
    shutil.copyfile(ROOT / "runtime/zfly_runtime/_vendor/PyYAML-LICENSE", licenses / "PyYAML.txt")
    for name in ("LICENSE", "Clash.Meta/LICENSE"):
        source = ROOT / "FlClash/core" / name
        if source.is_file():
            shutil.copyfile(source, licenses / ("mihomo.txt" if "/" in name else "Core.txt"))
    manifest = {"version": 1, "coreKind": kind, "sourceCoreSHA256": hashlib.sha256(source_core.read_bytes()).hexdigest(),
                "pythonVersion": "3.12", "architecture": platform.machine()}
    # Reject accidental Homebrew dependencies. Copies must work after moving the app.
    binaries = [python / "bin/python3.12", python / "lib/libpython3.12.dylib", *python.rglob("*.so"), helpers / "ZFlyCore"]
    for binary in binaries:
        architectures = run("lipo", "-archs", str(binary), capture_output=True, text=True).stdout.split()
        if manifest["architecture"] not in architectures:
            raise SystemExit(f"运行时架构不匹配：{binary.name} 不包含 {manifest['architecture']}")
        output = run("otool", "-L", str(binary), capture_output=True, text=True).stdout.splitlines()[1:]
        for line in output:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith("/") and not dependency.startswith(("/System/", "/usr/lib/")):
                # A dylib's own install ID is not a dependency; make it portable too.
                if binary.suffix == ".dylib" and Path(dependency).name == binary.name:
                    run("install_name_tool", "-id", "@loader_path/" + binary.name, str(binary), capture_output=True)
                else:
                    raise SystemExit(f"运行时存在外部依赖：{binary.name} → {dependency}")
        run("codesign", "--force", "--sign", "-", str(binary), capture_output=True)
    manifest["coreSHA256"] = hashlib.sha256((helpers / "ZFlyCore").read_bytes()).hexdigest()
    (resources / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    env = {"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(resources / "runtime"), "SSL_CERT_FILE": str(python / "cert.pem")}
    run(str(python / "bin/python3"), "-B", "-c",
        "import ssl, socket, sqlite3, ctypes, plistlib; import zfly_runtime.service; print('Bundled Python and ZFly runtime OK')", env=env)
    print(f"Bundled {kind} core ({manifest['coreSHA256'][:12]}) and standalone Python; no FlClash app required at runtime.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    assemble(parser.parse_args().app.resolve())
