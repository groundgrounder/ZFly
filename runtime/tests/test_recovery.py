"""Offline recovery CLI uses explicit temporary directories and starts no core."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from zfly_runtime.storage import StateStore, default_state


class RecoveryTests(unittest.TestCase):
    def test_recovery_cli_needs_no_ready_file_or_core_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            original = b'{broken original'
            (data / "state.json").write_bytes(original)
            archive = root / "backup.json"
            archive.write_text(json.dumps({"application": "ZFly", "version": 1, "state": default_state()}))
            result = subprocess.run([sys.executable, "-B", "-m", "zfly_runtime", "--data-dir", str(data),
                                     "--recover-state-from", str(archive)], capture_output=True, text=True,
                                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            store = StateStore(data)
            try:
                self.assertEqual(store.load(), default_state())
            finally:
                store.close()
            preserved = list(data.glob("state.before-recovery-*.json"))
            self.assertEqual(len(preserved), 1)
            self.assertEqual(preserved[0].read_bytes(), original)
            self.assertEqual((data / "state.json").stat().st_mode & 0o777, 0o600)
            self.assertFalse((data / "core").exists())

    def test_invalid_recovery_backup_leaves_original_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            original = b'{keep this state'
            (data / "state.json").write_bytes(original)
            archive = root / "bad.json"
            archive.write_text('{"application":"ZFly","version":1,"state":{}}')
            result = subprocess.run([sys.executable, "-B", "-m", "zfly_runtime", "--data-dir", str(data),
                                     "--recover-state-from", str(archive)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual((data / "state.json").read_bytes(), original)
            self.assertFalse(list(data.glob("state.before-recovery-*")))
