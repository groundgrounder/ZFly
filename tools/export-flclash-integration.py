#!/usr/bin/env python3
"""Export the existing FlClash management bridge without modifying its checkout."""

import argparse
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HOST_PATHS = (
    "lib/application.dart", "lib/main.dart", "lib/providers/actions/profiles.dart",
    "lib/providers/database.dart", "test/providers/database_test.dart", "lib/native",
)


def git(host, *arguments, expected=(0,)):
    result = subprocess.run(["git", "-C", str(host), *arguments], capture_output=True, check=False)
    if result.returncode not in expected:
        raise SystemExit(result.stderr.decode())
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, default=ROOT / "FlClash")
    arguments = parser.parse_args()
    host = arguments.host.resolve()
    base = git(host, "rev-parse", "HEAD")
    patch = git(host, "diff", "--binary", "HEAD", "--", *HOST_PATHS)
    untracked = git(host, "ls-files", "--others", "--exclude-standard", "-z", "--", "lib/native")
    for name in sorted(untracked.split(b"\0")):
        if name:
            patch += git(host, "diff", "--no-index", "--binary", "--", "/dev/null", name.decode(), expected=(1,))
    if not patch:
        raise SystemExit("No management bridge changes to export; existing export left intact.")
    destination = ROOT / "integrations/flclash"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "native-control.patch").write_bytes(patch)
    (destination / "base-commit.txt").write_bytes(base)
    print(f"Exported management bridge for FlClash {base.decode().strip()}")


if __name__ == "__main__":
    main()
