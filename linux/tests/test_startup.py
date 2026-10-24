import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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

    def test_language_defaults_to_system_when_preferences_are_missing(self):
        self.assertEqual(self.preferences.language(), "system")

    def test_legacy_preferences_default_to_system_and_keep_start_hidden(self):
        self.preferences.preferences_path.parent.mkdir(parents=True)
        self.preferences.preferences_path.write_text(
            json.dumps({"schema": 1, "startHidden": True}), encoding="utf-8"
        )

        self.assertEqual(self.preferences.language(), "system")
        self.assertEqual(self.preferences.state(), (False, True))

        self.preferences.configure_language("en")

        reloaded = StartupPreferences(self.root / "config", self.executable)
        self.assertEqual(reloaded.language(), "en")
        self.assertEqual(reloaded.state(), (False, True))

    def test_language_is_persisted_without_changing_startup_state(self):
        self.preferences.configure(True, True)
        entry = self.preferences.autostart_path.read_bytes()

        self.preferences.configure_language("en")

        reloaded = StartupPreferences(self.root / "config", self.executable)
        self.assertEqual(reloaded.language(), "en")
        self.assertEqual(reloaded.state(), (True, True))
        self.assertEqual(reloaded.autostart_path.read_bytes(), entry)
        self.assertEqual(
            json.loads(reloaded.preferences_path.read_text(encoding="utf-8")),
            {"schema": 1, "startHidden": True, "language": "en"},
        )
        self.assertEqual(reloaded.preferences_path.stat().st_mode & 0o777, 0o600)

    def test_startup_configuration_preserves_language(self):
        self.preferences.configure_language("zh-Hans")

        self.preferences.configure(True, False)

        reloaded = StartupPreferences(self.root / "config", self.executable)
        self.assertEqual(reloaded.language(), "zh-Hans")
        self.assertEqual(reloaded.state(), (True, False))

    def test_failed_language_write_rolls_back_all_consumer_visible_state(self):
        self.preferences.configure(True, True)
        self.preferences.configure_language("en")
        preferences_before = self.preferences.preferences_path.read_bytes()
        entry_before = self.preferences.autostart_path.read_bytes()
        original_write = self.preferences._atomic_write
        attempted = False

        def replace_then_fail(path, content, mode):
            nonlocal attempted
            if Path(path) == self.preferences.preferences_path and not attempted:
                attempted = True
                original_write(path, content, mode)
                raise OSError("simulated preference write failure")
            original_write(path, content, mode)

        with patch.object(self.preferences, "_atomic_write", side_effect=replace_then_fail):
            with self.assertRaisesRegex(OSError, "simulated preference write failure"):
                self.preferences.configure_language("zh-Hans")

        self.assertTrue(attempted)
        reloaded = StartupPreferences(self.root / "config", self.executable)
        self.assertEqual(reloaded.language(), "en")
        self.assertEqual(reloaded.state(), (True, True))
        self.assertEqual(reloaded.preferences_path.read_bytes(), preferences_before)
        self.assertEqual(reloaded.autostart_path.read_bytes(), entry_before)
        self.assertEqual(reloaded.preferences_path.stat().st_mode & 0o777, 0o600)

    def test_missing_executable_does_not_change_preferences(self):
        preferences = StartupPreferences(self.root / "other-config", self.root / "missing-zfly")

        with self.assertRaises(RuntimeError):
            preferences.configure(True, True)

        self.assertEqual(preferences.state(), (False, False))
        self.assertFalse(preferences.preferences_path.exists())


if __name__ == "__main__":
    unittest.main()
