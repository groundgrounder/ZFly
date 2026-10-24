#!/usr/bin/env python3
"""Stage ZFly's Linux payload and build DEB or RPM packages."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
VERSION = "0.2.0"
CORE_VERSION = "1.19.32"
CORE_URL = (
    "https://github.com/MetaCubeX/mihomo/releases/download/"
    f"v{CORE_VERSION}/mihomo-linux-amd64-compatible-v{CORE_VERSION}.gz"
)
CORE_SHA256 = "ba3ce607747a07f948fc35780e108a4a7c7f552a38b9bd4d115f313ebcb89c20"
ICON_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)


class PackagingError(Exception):
    pass


def _copy_file(source: Path, destination: Path, mode: int = 0o644) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def _copy_sources(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_core(destination: Path) -> None:
    request = urllib.request.Request(
        CORE_URL,
        headers={"User-Agent": "ZFly-distribution-packager/0.2.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _verify_static_elf(core: Path) -> None:
    with core.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:4] != b"\x7fELF":
            raise PackagingError("mihomo is not an ELF64 executable")
        if header[4] != 2 or header[5] != 1:
            raise PackagingError("mihomo must be a little-endian ELF64 executable")

        fields = struct.unpack("<16sHHIQQQIHHHHHH", header)
        elf_type, machine = fields[1], fields[2]
        program_offset, program_size, program_count = fields[5], fields[9], fields[10]
        if elf_type not in (2, 3) or machine != 62:
            raise PackagingError("mihomo must be an x86_64 ELF executable")
        if program_size < 56:
            raise PackagingError("mihomo has an invalid ELF program header size")
        core_size = core.stat().st_size
        if program_offset + program_size * program_count > core_size:
            raise PackagingError("mihomo has a truncated ELF program header table")

        for index in range(program_count):
            stream.seek(program_offset + index * program_size)
            program_header = stream.read(56)
            if len(program_header) != 56:
                raise PackagingError("mihomo has a truncated ELF program header")
            program_type = struct.unpack_from("<I", program_header)[0]
            if program_type in (2, 3):  # PT_DYNAMIC or PT_INTERP
                raise PackagingError("mihomo is not a statically linked ELF executable")


def _verify_core_version(core: Path) -> None:
    result = subprocess.run(
        [str(core), "-v"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 or f"v{CORE_VERSION}" not in output:
        raise PackagingError(
            f"mihomo did not report the expected v{CORE_VERSION} version: {output.strip()}"
        )


def _prepare_core(archive: Path, core: Path) -> None:
    actual_hash = _sha256(archive)
    if actual_hash != CORE_SHA256:
        raise PackagingError(
            f"mihomo archive SHA-256 mismatch: expected {CORE_SHA256}, got {actual_hash}"
        )
    with gzip.open(archive, "rb") as compressed, core.open("wb") as output:
        shutil.copyfileobj(compressed, output)
    core.chmod(0o755)
    _verify_static_elf(core)
    _verify_core_version(core)


def _empty_staging_root(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise PackagingError(f"staging root is not a directory: {path}")
    if path.exists() and any(path.iterdir()):
        raise PackagingError(f"staging root must be empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def _stage_payload(staging: Path, core: Path) -> None:
    _empty_staging_root(staging)
    share = staging / "usr/share"
    _copy_sources(ROOT / "linux/zfly", share / "zfly/zfly")
    _copy_sources(ROOT / "runtime/zfly_runtime", share / "zfly/zfly_runtime")
    _copy_file(PACKAGING / "zfly", staging / "usr/bin/zfly", 0o755)
    _copy_file(PACKAGING / "io.github.zfly.ZFly.desktop", share / "applications/io.github.zfly.ZFly.desktop")
    _copy_file(core, staging / "usr/lib/zfly/mihomo", 0o755)

    icons = share / "icons/hicolor"
    _copy_file(ROOT / "assets/icon.svg", icons / "scalable/apps/io.github.zfly.ZFly.svg")
    for size in ICON_SIZES:
        _copy_file(
            ROOT / f"assets/icons/zfly-{size}.png",
            icons / f"{size}x{size}/apps/io.github.zfly.ZFly.png",
        )
    _copy_file(ROOT / "assets/tray-outline.svg", icons / "scalable/status/zfly-tray-stopped-symbolic.svg")
    _copy_file(ROOT / "assets/tray-filled.svg", icons / "scalable/status/zfly-tray-running-symbolic.svg")
    for state in ("stopped", "running"):
        _copy_file(
            ROOT / f"assets/icons/zfly-tray-{state}-symbolic.png",
            icons / f"32x32/status/zfly-tray-{state}-symbolic.png",
        )

    licenses = share / "doc/zfly"
    _copy_file(ROOT / "LICENSE", licenses / "LICENSE")
    _copy_file(ROOT / "runtime/zfly_runtime/_vendor/PyYAML-LICENSE", licenses / "PyYAML-LICENSE")
    _copy_file(PACKAGING / "copyright", licenses / "copyright")


def command_prepare(args: argparse.Namespace) -> None:
    staging = args.staging.resolve()
    _empty_staging_root(staging)
    with tempfile.TemporaryDirectory(prefix="zfly-core-") as temporary:
        temporary_path = Path(temporary)
        archive = args.core_archive.resolve() if args.core_archive else temporary_path / "mihomo.gz"
        if not args.core_archive:
            _download_core(archive)
        core = temporary_path / "mihomo"
        _prepare_core(archive, core)
        _stage_payload(staging, core)


def _require_staging(staging: Path) -> None:
    required = (
        staging / "usr/bin/zfly",
        staging / "usr/lib/zfly/mihomo",
        staging / "usr/share/zfly/zfly/__main__.py",
        staging / "usr/share/zfly/zfly_runtime/__main__.py",
        staging / "usr/share/doc/zfly/copyright",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise PackagingError("staging root is incomplete; run prepare first: " + ", ".join(missing))


def _install_debian_metadata(staging: Path) -> None:
    debian = staging / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    _copy_file(PACKAGING / "debian/control", debian / "control", 0o644)
    _copy_file(PACKAGING / "debian/postinst", debian / "postinst", 0o755)
    _copy_file(PACKAGING / "debian/postrm", debian / "postrm", 0o755)


def command_deb(args: argparse.Namespace) -> None:
    staging = args.staging.resolve()
    _require_staging(staging)
    _install_debian_metadata(staging)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".zfly-deb-", dir=output.parent) as temporary:
        built = Path(temporary) / output.name
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(staging), str(built)],
            check=True,
        )
        os.replace(built, output)


def command_rpm(args: argparse.Namespace) -> None:
    staging = args.staging.resolve()
    _require_staging(staging)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".zfly-rpm-", dir=output.parent) as temporary:
        temporary_path = Path(temporary)
        topdir = temporary_path / "topdir"
        results = temporary_path / "results"
        topdir.mkdir(parents=True)
        results.mkdir()
        subprocess.run(
            [
                "rpmbuild",
                "-bb",
                str(PACKAGING / "zfly.spec"),
                "--define",
                f"_topdir {topdir}",
                "--define",
                f"_buildrootdir {temporary_path / 'buildroot'}",
                "--define",
                f"_rpmdir {results}",
                "--define",
                f"zfly_stage {staging}",
            ],
            check=True,
        )
        packages = list(results.rglob("*.rpm"))
        if len(packages) != 1:
            raise PackagingError(f"rpmbuild produced {len(packages)} RPM files instead of one")
        os.replace(packages[0], output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    prepare = subcommands.add_parser(
        "prepare",
        aliases=["stage"],
        help="stage the /usr payload and verified mihomo core",
    )
    prepare.add_argument("--staging", type=Path, required=True, help="empty staging root")
    prepare.add_argument(
        "--core-archive",
        type=Path,
        help="official mihomo .gz asset (downloaded from the pinned URL when omitted)",
    )
    prepare.set_defaults(handler=command_prepare)

    deb = subcommands.add_parser("deb", help="build a Debian package from a staging root")
    deb.add_argument("--staging", type=Path, required=True, help="root prepared by the prepare command")
    deb.add_argument("--output", type=Path, default=Path(f"zfly_{VERSION}-1_amd64.deb"), help="output .deb path")
    deb.set_defaults(handler=command_deb)

    rpm = subcommands.add_parser("rpm", help="build an RPM package from the same staging root")
    rpm.add_argument("--staging", type=Path, required=True, help="root prepared by the prepare command")
    rpm.add_argument("--output", type=Path, default=Path(f"zfly-{VERSION}-1.x86_64.rpm"), help="output .rpm path")
    rpm.set_defaults(handler=command_rpm)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except (PackagingError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
