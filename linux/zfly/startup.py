import json
import os
from pathlib import Path
import tempfile

from .icons import icon_file
from .i18n import LANGUAGES, tr


class StartupPreferences:
    def __init__(self, config_home=None, executable=None):
        config_home = config_home or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        self.config_home = Path(config_home).expanduser()
        self.preferences_path = self.config_home / "zfly" / "preferences.json"
        self.autostart_path = self.config_home / "autostart" / "io.github.zfly.ZFly.generated.desktop"
        self.executable = executable or os.environ.get("ZFLY_EXECUTABLE")

    def state(self):
        return self.autostart_path.exists(), self._read_preferences()["startHidden"]

    def language(self):
        return self._read_preferences()["language"]

    def configure(self, enabled, hidden):
        previous_enabled, previous_hidden = self.state()
        previous_language = self.language()
        content = self._desktop_entry(hidden) if enabled else None
        try:
            self._write_preferences(hidden, previous_language)
            if content is not None:
                self._atomic_write(self.autostart_path, content, 0o644)
            else:
                self.autostart_path.unlink(missing_ok=True)
        except Exception as error:
            try:
                self._write_preferences(previous_hidden, previous_language)
                if previous_enabled:
                    self._atomic_write(self.autostart_path, self._desktop_entry(previous_hidden), 0o644)
                else:
                    self.autostart_path.unlink(missing_ok=True)
            except Exception as rollback_error:
                raise RuntimeError(tr("启动设置保存失败，且回滚未完成：{error}", error=rollback_error)) from error
            raise

    def configure_language(self, value):
        if value not in LANGUAGES:
            raise ValueError(f"unsupported language: {value!r}")
        previous = self._read_preferences()
        try:
            self._write_preferences(previous["startHidden"], value)
        except Exception as error:
            try:
                self._write_preferences(previous["startHidden"], previous["language"])
            except Exception as rollback_error:
                raise RuntimeError(tr("启动设置保存失败，且回滚未完成：{error}", error=rollback_error)) from error
            raise

    def _read_preferences(self):
        try:
            value = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            language = value.get("language", "system")
            return {"startHidden": value.get("startHidden") is True,
                    "language": language if language in LANGUAGES else "system"}
        except (OSError, ValueError, AttributeError):
            return {"startHidden": False, "language": "system"}

    def _write_preferences(self, hidden, language):
        data = json.dumps({"schema": 1, "startHidden": bool(hidden), "language": language}, separators=(",", ":")) + "\n"
        self._atomic_write(self.preferences_path, data, 0o600)

    def _desktop_entry(self, hidden):
        if not self.executable:
            raise RuntimeError(tr("无法确定 ZFly 启动程序；请使用应用包内的桌面入口。"))
        executable = Path(self.executable).expanduser().resolve()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(tr("ZFly 启动程序不可用，请重新安装应用包。"))
        command = self._quote_exec(str(executable))
        if hidden:
            command += " --start-hidden"
        icon = icon_file()
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=ZFly\n"
            "Comment=Start ZFly when you log in\n"
            f"Exec={command}\n"
            f"Icon={icon if icon else 'io.github.zfly.ZFly'}\n"
            "Terminal=false\n"
            "NoDisplay=true\n"
            "StartupNotify=false\n"
        )

    @staticmethod
    def _quote_exec(value):
        if "\n" in value or "\r" in value:
            raise ValueError("invalid executable path")
        escaped = value.replace("%", "%%").replace("\\", "\\\\")
        escaped = escaped.replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        return f'"{escaped}"'

    @staticmethod
    def _atomic_write(path, content, mode):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".zfly-", dir=path.parent)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
