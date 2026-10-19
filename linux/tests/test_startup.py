from pathlib import Path
import tempfile
import unittest

from zfly.startup import StartupPreferences


class StartupPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.executable = self.root / "ZFly App" / "zfly"
        self.executable.parent.mkdir()
        self.executable.write_text("#!/bin/sh\n")
        self.executable.chmod(0o755)
        self.preferences = StartupPreferences(self.root / "config", self.executable)

    def tearDown(self):
        self.temporary.cleanup()

    def test_login_entry_and_hidden_argument_are_persisted(self):
        self.preferences.configure(True, True)

        enabled, hidden = self.preferences.state()
        entry = self.preferences.autostart_path.read_text()
        self.assertTrue(enabled)
        self.assertTrue(hidden)
        self.assertIn('Exec="' + str(self.executable.resolve()) + '" --start-hidden', entry)
        icon = Path(next(line.removeprefix("Icon=") for line in entry.splitlines() if line.startswith("Icon=")))
        self.assertTrue(icon.is_absolute())
        self.assertTrue(icon.is_file())
        self.assertEqual(icon.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.preferences.preferences_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.preferences.autostart_path.stat().st_mode & 0o777, 0o644)

    def test_disabling_login_start_removes_only_entry_and_keeps_hidden_preference(self):
        self.preferences.configure(True, True)
        self.preferences.configure(False, True)

        self.assertEqual(self.preferences.state(), (False, True))
        self.assertFalse(self.preferences.autostart_path.exists())

    def test_missing_executable_does_not_change_preferences(self):
        preferences = StartupPreferences(self.root / "other-config", self.root / "missing-zfly")

        with self.assertRaises(RuntimeError):
            preferences.configure(True, True)

        self.assertEqual(preferences.state(), (False, False))
        self.assertFalse(preferences.preferences_path.exists())


if __name__ == "__main__":
    unittest.main()
