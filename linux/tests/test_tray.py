"""Tray protocol regressions, run only with --safe-mode on an isolated bus/display."""
import os
import sys
import time
import unittest
import warnings
from unittest.mock import patch

warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")

SAFE_MODE = "--safe-mode" in sys.argv
if SAFE_MODE:
    sys.argv.remove("--safe-mode")

UI_AVAILABLE = False
if SAFE_MODE:
    try:
        from zfly.app import Application, Window
        from zfly.tray import ITEM, MENU, WATCHER, WATCHER_PATH, StatusNotifierTray
        from gi.repository import Adw, Gio, GLib, Gtk
        UI_AVAILABLE = True
    except (ImportError, ValueError):
        pass

WATCHER_XML = """<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
  <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
  <signal name="StatusNotifierHostRegistered"/><signal name="StatusNotifierHostUnregistered"/>
</interface></node>"""


@unittest.skipUnless(SAFE_MODE and UI_AVAILABLE, "requires GTK and explicit --safe-mode")
class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")
        Adw.init()
        cls.app = Application(preview=True)
        cls.app.register(None)

    def setUp(self):
        self.app.quitting = False
        self.app.start_hidden = False
        self.app.preview = True
        if self.app.tray:
            self.app.tray.close()
        self.app.tray = None
        self.window = Window(self.app, preview=True)
        self.app.window = self.window
        self.window.present()
        self.bus = self.app.get_dbus_connection()
        self.wait(lambda: self.window.data is not None and not self.window.refreshing)
        self.registered = []
        self.owned = False
        self.owner_id = self.export_id = 0
        self.host = True
        self.reject_registration = False

    def tearDown(self):
        self.app.quitting = True
        if self.app.tray:
            self.app.tray.close()
            self.app.tray = None
        self.stop_watcher()
        if not self.window.closed:
            self.window.close()
        self.app._release_hidden()
        for window in Gtk.Window.list_toplevels():
            window.destroy()
        self.app.window = None
        self.app.preview = True
        self.pump()

    def pump(self):
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def wait(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if condition():
                return
            time.sleep(.005)
        self.fail("tray condition did not become true")

    def start_watcher(self):
        interface = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
        self.export_id = self.bus.register_object(WATCHER_PATH, interface, self.register,
            lambda *_args: GLib.Variant("b", self.host), None)
        self.owner_id = Gio.bus_own_name_on_connection(self.bus, WATCHER, Gio.BusNameOwnerFlags.NONE,
            lambda *_args: setattr(self, "owned", True), lambda *_args: setattr(self, "owned", False))
        self.wait(lambda: self.owned)

    def stop_watcher(self):
        if self.owner_id:
            Gio.bus_unown_name(self.owner_id)
            self.owner_id = 0
        if self.export_id:
            self.bus.unregister_object(self.export_id)
            self.export_id = 0
        self.owned = False

    def register(self, _bus, sender, _path, _interface, _method, parameters, invocation):
        if self.reject_registration:
            invocation.return_dbus_error(WATCHER + ".Error.Rejected", "test registration rejected")
            return
        self.registered.append((sender, parameters.unpack()[0]))
        invocation.return_value(None)

    def start_tray(self, watcher=True):
        if watcher:
            self.start_watcher()
        self.app.tray = StatusNotifierTray(self.app, self.bus)
        self.wait(lambda: not self.app.tray.checking)
        return self.app.tray

    def call(self, path, interface, method, parameters=None):
        complete, values, errors = [], [], []
        def received(bus, result):
            try:
                values.append(bus.call_finish(result).unpack())
            except GLib.Error as error:
                errors.append(error)
            complete.append(True)
        self.bus.call(self.bus.get_unique_name(), path, interface, method, parameters, None,
                      Gio.DBusCallFlags.NONE, 2000, None, received)
        self.wait(lambda: complete)
        if errors:
            raise errors[0]
        return values[0]

    def click(self, identity):
        tray = self.app.tray
        return self.call(tray.menu_path, MENU, "Event",
                         GLib.Variant("(isvu)", (identity, "clicked", GLib.Variant("i", 0), 0)))

    def test_registers_item_with_pixmap_and_readable_menu(self):
        tray = self.start_tray()
        self.assertTrue(tray.available)
        self.assertEqual(self.registered, [(self.bus.get_unique_name(), tray.path)])
        properties = self.call(tray.path, "org.freedesktop.DBus.Properties", "GetAll",
                               GLib.Variant("(s)", (ITEM,)))[0]
        self.assertEqual(properties["Id"], "zfly")
        self.assertEqual(properties["Status"], "Active")
        self.assertEqual(properties["Menu"], tray.menu_path)
        width, height, pixels = properties["IconPixmap"][0]
        self.assertEqual(len(pixels), width * height * 4)
        self.assertEqual((width, height), (32, 32))
        revision, layout = self.call(tray.menu_path, MENU, "GetLayout", GLib.Variant("(iias)", (0, -1, [])))
        self.assertGreater(revision, 0)
        self.assertEqual(layout[0], 0)
        self.assertEqual([item[1].get("label") for item in layout[2] if "label" in item[1]],
                         ["离线预览 · 代理运行中", "打开 ZFly", "停止代理", "退出 ZFly"])
        groups = self.call(tray.menu_path, MENU, "GetGroupProperties", GLib.Variant("(aias)", ([2, 3], ["label"])))[0]
        self.assertEqual(groups, [(2, {"label": "打开 ZFly"}), (3, {"label": "停止代理"})])
        self.assertEqual(self.call(tray.menu_path, MENU, "GetProperty", GLib.Variant("(is)", (2, "label"))), ("打开 ZFly",))
        self.assertEqual(self.call(tray.menu_path, MENU, "AboutToShow", GLib.Variant("(i)", (0,))), (False,))

    def test_close_hides_and_activate_reopens_without_stopping_the_app(self):
        tray = self.start_tray()
        self.window.close()
        self.pump()
        self.assertFalse(self.window.get_visible())
        self.assertFalse(self.window.closed)
        self.assertTrue(self.app.hidden_start_hold)
        self.call(tray.path, ITEM, "Activate", GLib.Variant("(ii)", (0, 0)))
        self.assertTrue(self.window.get_visible())
        self.assertFalse(self.app.hidden_start_hold)

    def test_menu_opens_window_and_toggles_preview_proxy(self):
        tray = self.start_tray()
        self.window.close()
        self.click(2)
        self.wait(self.window.get_visible)
        self.click(3)
        self.wait(lambda: not self.window.busy and not self.window.management_state["running"])
        self.assertEqual(tray.entries[3]["label"], "启动代理")
        self.click(3)
        self.wait(lambda: not self.window.busy and self.window.management_state["running"])
        self.assertEqual(tray.entries[3]["label"], "停止代理")
        self.assertIsNone(self.window.runtime)

    def test_quit_menu_uses_owned_runtime_cleanup_instead_of_hiding(self):
        self.start_tray()
        class Runtime:
            stopped = False
            def stop(runtime):
                runtime.stopped = True
                return True
        runtime = self.window.runtime = Runtime()
        self.window.preview = False
        self.window.close()
        self.assertFalse(runtime.stopped)
        self.assertFalse(self.window.get_visible())
        self.click(5)
        self.wait(lambda: self.window.shutdown_complete)
        self.assertTrue(runtime.stopped)
        self.assertTrue(self.window.closed)
        self.assertFalse(self.app.hidden_start_hold)

    def test_failed_quit_cleanup_restores_the_window_and_exit_action(self):
        self.start_tray()
        class Runtime:
            def stop(_runtime):
                return False
        self.window.runtime = Runtime()
        self.window.preview = False
        self.window.close()
        self.click(5)
        self.wait(lambda: not self.window.closed and not self.app.quitting and self.window.get_visible())
        self.assertFalse(self.window.shutdown_complete)
        self.assertTrue(self.app.tray.entries[5]["enabled"])
        self.window.preview = True

    def test_missing_watcher_leaves_normal_close_and_visible_start(self):
        tray = self.start_tray(watcher=False)
        self.assertFalse(tray.available)
        self.app.preview = False
        self.app.start_hidden = True
        self.app.activate()
        self.assertTrue(self.window.get_visible())
        self.assertFalse(self.app.hidden_start_hold)
        self.window.close()
        self.assertTrue(self.window.closed)

    def test_watcher_without_host_does_not_hide_the_window(self):
        self.host = False
        tray = self.start_tray()
        self.assertTrue(tray.registered)
        self.assertFalse(tray.available)
        self.app.preview = False
        self.app.start_hidden = True
        self.app.activate()
        self.assertTrue(self.window.get_visible())
        self.assertFalse(self.app.hidden_start_hold)

    def test_rejected_registration_recovers_a_pending_hidden_start(self):
        self.reject_registration = True
        self.start_watcher()
        self.window.set_visible(False)
        self.app.tray = StatusNotifierTray(self.app, self.bus)
        self.app.preview = False
        self.app.start_hidden = True
        self.app.activate()
        self.wait(lambda: not self.app.tray.checking and self.window.get_visible())
        self.assertFalse(self.app.tray.available)
        self.assertFalse(self.app.hidden_start_hold)
        self.assertEqual(self.app.hidden_start_timer, 0)

    def test_start_hidden_waits_for_registration_and_falls_back_without_a_host(self):
        self.start_watcher()
        self.window.set_visible(False)
        self.app.tray = StatusNotifierTray(self.app, self.bus)
        self.app.preview = False
        self.app.start_hidden = True
        self.app.activate()
        self.assertFalse(self.window.get_visible())
        self.wait(lambda: self.app.tray.available)
        self.assertTrue(self.app.hidden_start_hold)
        self.host = False
        self.bus.emit_signal(None, WATCHER_PATH, WATCHER, "StatusNotifierHostUnregistered", None)
        self.wait(self.window.get_visible)
        self.assertFalse(self.app.hidden_start_hold)

    def test_panel_restart_reveals_hidden_window_and_reregisters_the_icon(self):
        self.start_tray()
        self.window.close()
        self.stop_watcher()
        self.wait(lambda: not self.app.tray.available and self.window.get_visible())
        self.start_watcher()
        self.wait(lambda: self.app.tray.available and len(self.registered) == 2)
        self.window.close()
        self.assertFalse(self.window.get_visible())
        self.assertFalse(self.window.closed)

    def test_disabled_toggle_ignores_menu_clicks(self):
        tray = self.start_tray()
        self.window.busy = True
        self.app.update_tray()
        self.assertFalse(tray.entries[3]["enabled"])
        with patch.object(self.window, "toggle_running") as toggle:
            self.click(3)
            self.pump()
            toggle.assert_not_called()
        self.window.busy = False


if __name__ == "__main__":
    if not SAFE_MODE or not UI_AVAILABLE or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit("Run with --safe-mode in an isolated GTK display and session bus.")
    unittest.main()
