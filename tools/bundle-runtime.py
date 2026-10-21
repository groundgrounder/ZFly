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
import re
import plistlib

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def python_home():
    if value := os.environ.get("ZFLY_PYTHON_HOME"):
        return Path(value).expanduser().resolve()
    candidates = [ROOT / "build/dependencies/python"]
    uv = Path.home() / ".local/share/uv/python"
    arch = "aarch64" if platform.machine() == "arm64" else "x86_64"
    candidates += sorted(uv.glob(f"cpython-3.12.*-macos-{arch}-none"),
                         key=lambda path: tuple(map(int, re.search(r"cpython-(\d+)\.(\d+)\.(\d+)", path.name).groups())), reverse=True)
    for path in candidates:
        if (path / "bin/python3.12").is_file():
            return path
    raise SystemExit("需要可重定位的 CPython 3.12（python-build-standalone）。将其放入 build/dependencies/python，或设置 ZFLY_PYTHON_HOME。")


def core_source():
    candidates = [Path(os.environ["ZFLY_CORE"]).expanduser()] if os.environ.get("ZFLY_CORE") else [
        ROOT / "build/dependencies/ZFlyCore", ROOT / "build/dependencies/mihomo",
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            kind = os.environ.get("ZFLY_CORE_KIND", "mihomo" if "mihomo" in path.name.lower() else "ipc")
            if kind not in ("ipc", "mihomo"):
                raise SystemExit("ZFLY_CORE_KIND 必须是 ipc 或 mihomo")
            return path.resolve(), kind
    raise SystemExit("缺少核心：设置 ZFLY_CORE 为独立 Go 核心或 mihomo 的可执行文件路径。")


GEODATA_NAMES = ("GEOIP.dat", "GEOSITE.dat", "GEOIP.metadb", "ASN.mmdb")


def resource_source(variable, candidates, *, directory=False):
    choices = [Path(os.environ[variable]).expanduser()] if os.environ.get(variable) else candidates
    for path in choices:
        if directory and all((path / name).is_file() and (path / name).stat().st_size for name in GEODATA_NAMES):
            return path
        if not directory and path.is_file() and path.stat().st_size:
            return path
    raise SystemExit(f"缺少构建资源 {variable}；请提供有效路径。")


def verify_artifacts(app):
    resources = app / "Contents/Resources"
    required = ["Contents/MacOS/ZFly", "Contents/Helpers/ZFlyCore", "Contents/Info.plist",
                "Contents/Resources/ZFly.icns", "Contents/Resources/python/bin/python3",
                "Contents/Resources/python/cert.pem", "Contents/Resources/runtime-manifest.json",
                "Contents/Resources/runtime/zfly_runtime/service.py"]
    required += ["Contents/Resources/geodata/" + name for name in GEODATA_NAMES]
    required += ["Contents/Resources/Licenses/" + name for name in ("ZFly-GPL-3.0.txt", "Python.txt", "PyYAML.txt", "Core.txt", "mihomo.txt")]
    for name in required:
        path = app / name
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit("构建产物缺失或为空：" + name)
    if not list(resources.rglob("preview.json")) or not list(resources.rglob("en.lproj/Localizable.strings")):
        raise SystemExit("构建产物缺少预览或本地化资源。")
    with (app / "Contents/Info.plist").open("rb") as stream:
        metadata = plistlib.load(stream)
    if metadata.get("CFBundleIdentifier") != "io.github.zfly.ZFly":
        raise SystemExit("应用标识错误。")
    manifest = json.loads((resources / "runtime-manifest.json").read_text())
    if hashlib.sha256((app / "Contents/Helpers/ZFlyCore").read_bytes()).hexdigest() != manifest["coreSHA256"]:
        raise SystemExit("核心校验和错误。")
    print("Verified executable, metadata, icons, preview, geodata and license inventory.")


def assemble(app):
    source_python = python_home()
    source_core, kind = core_source()
    geodata = resource_source("ZFLY_GEODATA", [ROOT / "FlClash/assets/data", ROOT / "build/flclash-host/assets/data"], directory=True)
    core_license = resource_source("ZFLY_CORE_LICENSE", [ROOT / "FlClash/LICENSE", ROOT / "build/flclash-host/LICENSE"])
    mihomo_license = resource_source("ZFLY_MIHOMO_LICENSE", [ROOT / "FlClash/core/Clash.Meta/LICENSE", ROOT / "build/flclash-host/core/Clash.Meta/LICENSE"])
    certificate = Path(os.environ.get("SSL_CERT_FILE", "/etc/ssl/cert.pem"))
    if not certificate.is_file() or not certificate.stat().st_size:
        raise SystemExit("SSL_CERT_FILE 证书文件不存在或为空。")
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
    shutil.copyfile(certificate, python / "cert.pem")
    shutil.copytree(ROOT / "runtime/zfly_runtime", resources / "runtime/zfly_runtime",
                    ignore=shutil.ignore_patterns("__pycache__"))
    helpers = app / "Contents/Helpers"
    helpers.mkdir(exist_ok=True)
    # Replace the file instead of overwriting an inode from a previously signed build.
    staged_core = helpers / ".ZFlyCore.new"
    shutil.copy2(source_core, staged_core)
    os.chmod(staged_core, 0o755)
    staged_core.replace(helpers / "ZFlyCore")
    for name in (*GEODATA_NAMES, "country.mmdb"):
        if (geodata / name).is_file():
            shutil.copyfile(geodata / name, resources / "geodata" / name)
    licenses = resources / "Licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", licenses / "ZFly-GPL-3.0.txt")
    shutil.copyfile(source_python / "lib/python3.12/LICENSE.txt", licenses / "Python.txt")
    shutil.copyfile(ROOT / "runtime/zfly_runtime/_vendor/PyYAML-LICENSE", licenses / "PyYAML.txt")
    shutil.copyfile(core_license, licenses / "Core.txt")
    shutil.copyfile(mihomo_license, licenses / "mihomo.txt")
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
    verify_artifacts(app)
    print(f"Bundled {kind} core ({manifest['coreSHA256'][:12]}) and standalone Python; no FlClash app required at runtime.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    (verify_artifacts if args.verify_only else assemble)(args.app.resolve())
