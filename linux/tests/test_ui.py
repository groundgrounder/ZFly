"""GTK regressions; run with --safe-mode in an isolated graphical session.

Normal headless unittest discovery skips these tests. They never start a core
or use the host's networking settings. XDG directories should be temporary.
"""

import os
import copy
from pathlib import Path
import sys
import tempfile
import threading
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
        from zfly.controller import ControllerError
        from gi.repository import Adw, Gio, GLib, Gtk
        UI_AVAILABLE = True
    except (ImportError, ValueError):
        pass


def descendants(widget):
    child = widget.get_first_child()
    while child:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


@unittest.skipUnless(SAFE_MODE and UI_AVAILABLE, "requires GTK and explicit --safe-mode")
class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")
        Adw.init()
        cls.app = Application(preview=True)
        cls.app.register(None)

    def setUp(self):
        self.window = Window(self.app, preview=True)
        self.window.present()
        self.wait(lambda: self.window.data is not None and not self.window.busy and not self.window.refreshing)

    def tearDown(self):
        self.window.close()
        for window in Gtk.Window.list_toplevels():
            window.destroy()
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
            time.sleep(.01)
        self.fail("GTK condition did not become true")

    def click(self, root, text):
        item = next(child for child in descendants(root) if isinstance(child, Gtk.Button) and child.get_label() == text)
        item.emit("clicked")

    def dialog(self):
        return next((window for window in Gtk.Window.list_toplevels() if window != self.window and window.get_visible()), None)

    def test_preview_never_constructs_native_runtime(self):
        self.assertTrue(self.window.preview)
        self.assertIsNone(self.window.runtime)

    def test_packaged_navigation_icons_are_available(self):
        theme = Gtk.IconTheme.get_for_display(self.window.get_display())
        for name in ("document-new-symbolic", "network-transmit-receive-symbolic", "applications-engineering-symbolic", "preferences-system-symbolic", "io.github.zfly.ZFly"):
            with self.subTest(icon=name):
                self.assertTrue(theme.has_icon(name))

    def test_profiles_navigation_does_not_change_settings_category(self):
        self.window.settings_panel.categories.set_selected(1)
        self.window.navigate_to(1)
        self.assertIs(self.window.stack.get_visible_child(), self.window.settings_panel.profiles)
        self.window.navigate_to(7)
        self.assertIs(self.window.stack.get_visible_child(), self.window.settings_panel)
        self.assertEqual(self.window.settings_panel.stack.get_visible_child_name(), "network")
        self.assertEqual(self.window.settings_panel.categories.get_model().get_n_items(), 4)

    def test_mode_selection_updates_saved_state_and_visible_selection(self):
        self.window.mode_buttons["global"].emit("clicked")
        self.wait(lambda: self.window.management_state["network"]["mode"] == "global" and not self.window.busy and not self.window.refreshing)
        self.assertTrue(self.window.mode_buttons["global"].get_active())
        self.assertEqual(sum(button.get_active() for button in self.window.mode_buttons.values()), 1)
        self.assertEqual(self.window.data["configs"]["mode"], "global")
        self.assertEqual(self.window.metric_rows["路由模式"].get_label(), "全局")

    def test_failed_mode_change_restores_previous_selection(self):
        original = self.window.management_client.perform
        def fail(command):
            if command["action"] == "network.save":
                raise ControllerError("测试：设置未保存")
            return original(command)
        self.window.management_client.perform = fail
        self.window.mode_buttons["global"].emit("clicked")
        self.wait(lambda: not self.window.busy)
        self.assertEqual(self.window.management_state["network"]["mode"], "rule")
        self.assertTrue(self.window.mode_buttons["rule"].get_active())
        self.assertFalse(self.window.mode_buttons["global"].get_active())

    def test_node_search_has_a_recoverable_empty_state(self):
        self.window.navigate_to(2)
        self.window.search.set_text("no_such_node_zfly_test")
        self.wait(lambda: self.window.proxy_no_results.get_visible())
        self.assertFalse(self.window.proxy_list.get_visible())
        self.click(self.window.proxy_no_results, "清除搜索")
        self.wait(lambda: not self.window.proxy_no_results.get_visible())
        self.assertTrue(self.window.proxy_list.get_visible())

    def test_connection_search_distinguishes_no_matches_from_no_connections(self):
        self.window.navigate_to(3)
        self.window.search.set_text("no_such_connection_zfly_test")
        self.wait(lambda: self.window.connection_no_results.get_visible())
        self.assertFalse(self.window.connection_empty.get_visible())
        self.window.search.set_text("")
        self.wait(lambda: self.window.connection_list.get_visible())
        self.window.controller._data["connections"]["connections"] = []
        self.window.refresh()
        self.wait(lambda: self.window.connection_empty.get_visible())
        self.assertFalse(self.window.connection_no_results.get_visible())

    def test_subscription_validation_is_visible_inside_the_dialog(self):
        self.window.settings_panel.profile_properties()
        self.wait(lambda: self.dialog() is not None)
        dialog = self.dialog()
        self.assertTrue(any(isinstance(child, Adw.HeaderBar) for child in descendants(dialog)))
        self.click(dialog, "保存")
        self.assertTrue(dialog.error_label.get_visible())
        self.assertIn("HTTP", dialog.error_label.get_label())
        self.assertTrue(dialog.get_sensitive())
        self.assertEqual(len(self.window.management_state["profiles"]), 2)
        self.click(dialog, "取消")
        self.wait(lambda: self.dialog() is None)

    def test_subscription_server_error_stays_in_the_dialog_and_unlocks_it(self):
        self.window.settings_panel.profile_properties()
        self.wait(lambda: self.dialog() is not None)
        dialog = self.dialog()
        url = next(child for child in descendants(dialog) if isinstance(child, Adw.EntryRow) and child.get_title() == "订阅 URL")
        url.set_text("https://example.invalid/subscription")
        def fail(_command):
            raise ControllerError("测试：订阅下载失败")
        self.window.management_client.perform = fail
        self.click(dialog, "保存")
        self.wait(lambda: dialog.error_label.get_visible())
        self.assertIn("订阅下载失败", dialog.error_label.get_label())
        self.assertTrue(dialog.get_sensitive())
        self.assertTrue(dialog.get_deletable())

    def test_form_width_follows_a_narrow_main_window(self):
        self.window.set_default_size(360, 760)
        self.wait(lambda: self.window.get_width() == 360 and self.window.split.get_collapsed())
        self.window.settings_panel.profile_properties()
        self.wait(lambda: self.dialog() is not None and self.dialog().get_width() > 0)
        self.assertLessEqual(self.dialog().get_width(), self.window.get_width())
        self.assertTrue(self.window.split.get_collapsed())

    def test_breakpoints_keep_navigation_available_at_each_width(self):
        for width in (360, 680, 1040):
            with self.subTest(width=width):
                self.window.set_default_size(width, 760)
                self.wait(lambda: self.window.get_width() == width and self.window.split.get_collapsed() == (width <= 700))
                self.window.navigate_to(1)
                self.assertIs(self.window.stack.get_visible_child(), self.window.settings_panel.profiles)
                self.assertTrue(self.window.split.get_show_content())

    def test_catalog_json_error_is_local_and_preserves_the_draft(self):
        self.window.navigate_to(4)
        self.wait(lambda: not self.window.busy and not self.window.catalog_panel.loading)
        self.window.catalog_panel.open()
        self.wait(lambda: self.dialog() is not None)
        dialog = self.dialog()
        editor = next(child for child in descendants(dialog) if isinstance(child, Gtk.TextView))
        editor.get_buffer().set_text("{")
        self.click(dialog, "保存并应用")
        self.assertTrue(dialog.error_label.get_visible())
        self.assertIn("第 1 行", dialog.error_label.get_label())
        self.assertTrue(editor.get_buffer().get_modified())

    def test_tools_reveal_diagnostic_results(self):
        self.window.navigate_to(6)
        self.window.tools_panel.inspect("dnsQuery")
        self.wait(lambda: self.window.tools_panel.result_expander.get_expanded())
        buffer = self.window.tools_panel.output.get_buffer()
        self.assertIn("192.0.2.1", buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True))

    def test_workspace_reports_json_location_and_preserves_unsaved_text(self):
        self.window.navigate_to(5)
        panel = self.window.workspace_panel
        self.wait(lambda: panel._key() in panel.drafts and not self.window.busy)
        panel.buffer.set_text("{")
        panel.save()
        self.assertTrue(panel.error.get_visible())
        self.assertIn("第 1 行", panel.error.get_label())
        self.window.navigate_to(0)
        self.window.navigate_to(5)
        self.assertEqual(panel.buffer.get_text(panel.buffer.get_start_iter(), panel.buffer.get_end_iter(), True), "{")

    def test_catalog_edit_allows_previously_loaded_categories_to_reload(self):
        management = self.window.management_client
        created = management.perform({"action": "profile.create", "label": "缓存回归"})
        identity = created["state"]["profiles"][-1]["id"]
        for kind, extra in (("proxies", {}), ("groups", {"name": identity})):
            command = {"action": "catalog.read", "section": kind, **extra}
            document = management.perform(command)["document"]
            management.perform({**command, "action": "catalog.save", "text": document["text"], "revision": document["revision"]})
        self.window.refresh()
        self.wait(lambda: not self.window.refreshing)
        self.window.navigate_to(4)
        panel = self.window.catalog_panel
        self.wait(lambda: panel.key() in panel.cache and not self.window.busy)
        panel.kind.set_selected(1)
        group_key = panel.key()
        self.wait(lambda: group_key in panel.cache and not self.window.busy)
        self.assertEqual(len(panel.cache[group_key]), 1)
        panel.kind.set_selected(0)
        panel.open(panel.cache[panel.key()][0]["id"])
        self.wait(lambda: self.dialog() is not None and not self.window.busy)
        self.click(self.dialog(), "保存并应用")
        self.wait(lambda: self.dialog() is None and not self.window.busy)
        panel.kind.set_selected(1)
        self.wait(lambda: group_key in panel.cache and not self.window.busy)
        self.assertEqual(len(panel.cache[group_key]), 1)

    def test_catalog_refresh_error_is_visible_with_cached_items(self):
        self.window.navigate_to(4)
        panel = self.window.catalog_panel
        self.wait(lambda: panel.key() in panel.cache and not self.window.busy)
        panel.cache[panel.key()] = [{"id": "cached", "label": "已有节点", "detail": "socks5"}]
        panel.render()
        original = self.window.management_client.perform
        def fail(command):
            if command["action"] == "catalog.list":
                raise ControllerError("测试：资源刷新失败")
            return original(command)
        self.window.management_client.perform = fail
        panel.load()
        self.wait(lambda: not self.window.busy)
        self.assertTrue(panel.list_scroll.get_visible())
        self.assertIn("资源刷新失败", panel.note.get_label())

    def test_core_settings_and_overview_share_the_preview_network_state(self):
        panel = self.window.settings_panel
        panel.core_fields["mode"].set_selected(1)
        panel.core_fields["ipv6"].set_active(True)
        panel.save_core()
        self.wait(lambda: not self.window.busy)
        self.assertEqual(self.window.management_client.snapshot()["network"]["mode"], "global")
        self.assertTrue(self.window.management_state["network"]["ipv6"])
        self.assertTrue(self.window.mode_buttons["global"].get_active())

    def test_network_flags_are_reflected_in_preview_core_settings(self):
        expected = copy.deepcopy(self.window.management_state["network"])
        network = {**expected, "ipv6": True, "allowLan": True, "tcpConcurrent": False, "logLevel": "debug"}
        self.window.manage({"action": "network.save", "network": network, "expected": expected}, lambda _result: self.window.refresh())
        self.wait(lambda: not self.window.busy and not self.window.refreshing)
        configs = self.window.data["configs"]
        self.assertTrue(configs["ipv6"])
        self.assertTrue(configs["allow-lan"])
        self.assertFalse(configs["tcp-concurrent"])
        self.assertEqual(configs["log-level"], "debug")

    def test_pending_subscription_cannot_close_before_the_save_finishes(self):
        self.window.settings_panel.profile_properties()
        self.wait(lambda: self.dialog() is not None)
        dialog = self.dialog()
        url = next(child for child in descendants(dialog) if isinstance(child, Adw.EntryRow) and child.get_title() == "订阅 URL")
        url.set_text("https://example.invalid/subscription")
        release = threading.Event()
        self.addCleanup(release.set)
        def delayed(_command):
            release.wait(timeout=3)
            raise ControllerError("测试：保存失败")
        self.window.management_client.perform = delayed
        self.click(dialog, "保存")
        self.assertTrue(self.window.busy)
        dialog.close()
        self.pump()
        self.assertTrue(dialog.get_visible())
        release.set()
        self.wait(lambda: not self.window.busy)
        self.assertTrue(dialog.error_label.get_visible())

    def test_safe_mode_has_its_own_application_identity(self):
        normal = Application(preview=False)
        self.assertNotEqual(self.app.get_application_id(), normal.get_application_id())
        self.assertTrue(self.app.get_application_id().endswith(".Preview"))

    def test_disconnected_core_does_not_keep_the_running_headline(self):
        def fail():
            raise ControllerError("测试：核心连接失败")
        self.window.controller.snapshot = fail
        self.window.refresh()
        self.wait(lambda: not self.window.refreshing)
        self.assertFalse(self.window.connected)
        self.assertEqual(self.window.running_title.get_label(), "核心连接中断")
        self.assertEqual(self.window.route_row.get_subtitle(), "节点信息暂不可用")

    def test_refresh_started_before_mutation_cannot_overwrite_new_state(self):
        release = threading.Event()
        started = threading.Event()
        self.addCleanup(release.set)
        management = self.window.management_client
        original = management.snapshot
        old_state = original()
        def slow_snapshot():
            if threading.current_thread().name == "zfly-controller" and not started.is_set():
                started.set()
                release.wait(timeout=3)
                return old_state
            return original()
        management.snapshot = slow_snapshot
        self.window.refresh()
        self.wait(started.is_set)
        self.window.toggle_running()
        self.wait(lambda: not self.window.busy)
        self.assertFalse(self.window.management_state["running"])
        release.set()
        self.wait(lambda: not self.window.refreshing)
        self.assertFalse(self.window.management_state["running"])
        self.assertEqual(self.window.power_button.get_label(), "启动代理")

    def test_restoring_all_data_reloads_editor_and_catalog_caches(self):
        self.window.navigate_to(5)
        workspace = self.window.workspace_panel
        self.wait(lambda: workspace._key() in workspace.drafts and not self.window.busy)
        workspace.buffer.set_text('{"draft": true}')
        self.window.navigate_to(4)
        catalog = self.window.catalog_panel
        self.wait(lambda: catalog.key() in catalog.cache and not self.window.busy)
        catalog.cache[catalog.key()] = [{"id": "old", "label": "旧节点", "detail": "socks5"}]
        self.window.navigate_to(6)
        manager = self.window.management_client
        original = manager.perform
        def restored(command):
            if command.get("tool") == "restoreDAVAll":
                document = original({"action": "workspace.read", "section": "dns"})["document"]
                original({"action": "workspace.write", "section": "dns", "text": '{"restored": true}', "revision": document["revision"]})
            return original(command)
        manager.perform = restored
        self.window.manage({"action": "tool.run", "tool": "restoreDAVAll"})
        self.wait(lambda: not self.window.busy)
        self.assertFalse(catalog.cache)
        self.window.navigate_to(5)
        self.wait(lambda: workspace._key() in workspace.drafts and not self.window.busy)
        self.assertEqual(workspace.drafts[workspace._key()]["text"], '{"restored": true}')

    def test_restoring_profiles_keeps_unrelated_unsaved_workspace_settings(self):
        self.window.navigate_to(5)
        workspace = self.window.workspace_panel
        self.wait(lambda: workspace._key() in workspace.drafts and not self.window.busy)
        workspace.buffer.set_text('{"keep": true}')
        document = self.window.management_client.perform({"action": "workspace.read", "section": "profileRules", "id": "sample-local"})["document"]
        key = ("profileRules", "sample-local")
        workspace.drafts[key] = {"document": document, "text": "MATCH,DIRECT"}
        self.window.manage({"action": "tool.run", "tool": "restoreDAVProfiles"})
        self.wait(lambda: not self.window.busy)
        self.assertNotIn(key, workspace.drafts)
        self.assertEqual(workspace.drafts[("dns", None)]["text"], '{"keep": true}')

    def export_chooser(self, path):
        class Chooser:
            def __init__(self, **_kwargs):
                self.callback = None
            def set_current_name(self, _name):
                pass
            def connect(self, _signal, callback):
                self.callback = callback
            def show(self):
                self.callback(self, Gtk.ResponseType.ACCEPT)
            def get_file(self):
                return Gio.File.new_for_path(str(path))
            def destroy(self):
                pass
        return Chooser

    def test_workspace_export_is_private_and_replaces_the_selected_file(self):
        self.window.navigate_to(5)
        panel = self.window.workspace_panel
        self.wait(lambda: panel._key() in panel.drafts and not self.window.busy)
        with tempfile.TemporaryDirectory(prefix="zfly-export-test-") as temporary:
            target = Path(temporary) / "dns.json"
            target.write_text("old")
            target.chmod(0o644)
            with patch("zfly.workspace.Gtk.FileChooserNative", self.export_chooser(target)):
                panel.export()
            self.wait(lambda: not self.window.busy)
            self.assertEqual(target.read_text(), panel.drafts[panel._key()]["text"])
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_yaml_export_is_private_and_replaces_the_selected_file(self):
        with tempfile.TemporaryDirectory(prefix="zfly-export-test-") as temporary:
            target = Path(temporary) / "profile.yaml"
            target.write_text("old")
            target.chmod(0o644)
            with patch("zfly.settings.Gtk.FileChooserNative", self.export_chooser(target)):
                self.window.settings_panel.export_file("profile.yaml", b"mixed-port: 7890\n")
            self.assertEqual(target.read_bytes(), b"mixed-port: 7890\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    if not SAFE_MODE or not UI_AVAILABLE:
        raise SystemExit("Run with --safe-mode using GTK / libadwaita and an isolated display.")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit("An isolated graphical display is required.")
    unittest.main()
