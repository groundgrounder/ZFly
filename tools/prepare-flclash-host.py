#!/usr/bin/env python3
"""Prepare a patched build snapshot while leaving the original FlClash checkout clean."""

import argparse
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def git(repository, *arguments, **options):
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True, stderr=subprocess.PIPE, **options,
    )


def extract_revision(repository, revision, destination):
    with tempfile.TemporaryFile() as archive:
        git(repository, "archive", "--format=tar", revision, stdout=archive)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as contents:
            contents.extractall(destination, filter="data")
    entries = git(repository, "ls-tree", "-r", "-z", revision, stdout=subprocess.PIPE).stdout
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, _, commit = metadata.split()
        if mode != b"160000":
            continue
        relative = Path(os.fsdecode(path))
        submodule = repository / relative
        if not (submodule / ".git").exists():
            raise RuntimeError(f"Submodule is not initialized: {submodule}")
        extract_revision(submodule, commit.decode(), destination / relative)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "FlClash")
    parser.add_argument("--output", type=Path, default=ROOT / "build/flclash-host")
    arguments = parser.parse_args()
    source = arguments.source.resolve()
    output = arguments.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error(f"Output already exists; choose a new directory: {output}")
    integration = ROOT / "integrations/flclash"
    revision = (integration / "base-commit.txt").read_text().strip()
    patch = integration / "native-control.patch"
    try:
        git(source, "cat-file", "-e", f"{revision}^{{commit}}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".zfly-host-", dir=output.parent) as temporary:
            snapshot = Path(temporary) / "host"
            snapshot.mkdir()
            extract_revision(source, revision, snapshot)
            environment = dict(os.environ, GIT_CEILING_DIRECTORIES=str(snapshot.parent))
            git(snapshot, "apply", "--check", str(patch), env=environment)
            git(snapshot, "apply", str(patch), env=environment)
            snapshot.rename(output)
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr.decode().strip()) from error
    except (OSError, RuntimeError, tarfile.TarError) as error:
        raise SystemExit(str(error)) from error
    print(f"Prepared patched FlClash {revision} at {output}")
    print("Original checkout unchanged. Build Flutter from the prepared directory.")


if __name__ == "__main__":
    main()
