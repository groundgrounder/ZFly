"""GTK regressions; run with --safe-mode in an isolated graphical session.

Normal headless unittest discovery skips these tests. They never start a core
or use the host's networking settings. XDG directories should be temporary.
"""

import os
import copy
import json
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

    def test_application_owned_icon_file_is_available(self):
        from zfly.icons import icon_file
        icon = icon_file()
        self.assertIsNotNone(icon)
        self.assertTrue(icon.is_file())
        self.assertEqual(icon.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_packaged_navigation_icons_are_available(self):
        theme = Gtk.IconTheme.get_for_display(self.window.get_display())
        for name in ("document-new-symbolic", "network-transmit-receive-symbolic", "applications-engineering-symbolic", "preferences-system-symbolic", "io.github.zfly.ZFly"):
            with self.subTest(icon=name):
                self.assertTrue(theme.has_icon(name))

    def test_profiles_navigation_does_not_change_settings_category(self):
        self.window.settings_panel.categories.set_selected(1)
        self.window.navigate_to(1)
        self.assertIs(self.window.stack.get_visible_child(), self.window.profiles_panel)
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
        self.window.profiles_panel.profile_properties()
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
        self.window.profiles_panel.profile_properties()
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
        self.window.profiles_panel.profile_properties()
        self.wait(lambda: self.dialog() is not None and self.dialog().get_width() > 0)
        self.assertLessEqual(self.dialog().get_width(), self.window.get_width())
        self.assertTrue(self.window.split.get_collapsed())

    def test_breakpoints_keep_navigation_available_at_each_width(self):
        for width in (360, 680, 1040):
            with self.subTest(width=width):
                self.window.set_default_size(width, 760)
                self.wait(lambda: self.window.get_width() == width and self.window.split.get_collapsed() == (width <= 700))
                self.window.navigate_to(1)
                self.assertIs(self.window.stack.get_visible_child(), self.window.profiles_panel)
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
        self.window.profiles_panel.profile_properties()
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

    def assert_shutdown_timeout_releases_task(self, background):
        operation_release, shutdown_release, cleaned = threading.Event(), threading.Event(), threading.Event()
        received = []
        class Runtime:
            def stop(_runtime):
                shutdown_release.wait(3)
                return False
        def operation():
            operation_release.wait(3)
        self.window.runtime = Runtime()
        try:
            self.assertTrue(self.window._submit(operation, received.append, background=background, done=cleaned.set))
            self.window.preview = False
            self.window.close()
            self.assertTrue(self.window.closed)
            operation_release.set()
            self.wait(cleaned.is_set)
            self.assertFalse(self.window.busy)
            self.assertFalse(self.window.refreshing)
            shutdown_release.set()
            self.wait(lambda: not self.window.closed)
            self.assertTrue(self.window.get_sensitive())
            self.assertFalse(received)
            complete = threading.Event()
            self.assertTrue(self.window._submit(lambda: None, lambda _: complete.set()))
            self.wait(complete.is_set)
        finally:
            operation_release.set()
            shutdown_release.set()
            self.window.preview = True

    def test_shutdown_timeout_releases_foreground_task_and_accepts_new_work(self):
        self.assert_shutdown_timeout_releases_task(background=False)

    def test_shutdown_timeout_releases_background_task_and_restores_polling(self):
        self.assert_shutdown_timeout_releases_task(background=True)

    def test_saved_core_settings_remain_successful_when_followup_snapshot_fails(self):
        messages = []
        self.window.toast = messages.append
        panel = self.window.settings_panel
        panel.core_fields["mode"].set_selected(1)
        with patch.object(self.window.controller, "snapshot", side_effect=ControllerError("测试：快照读取失败")):
            panel.save_core()
            self.wait(lambda: not self.window.busy and not self.window.refreshing)
        self.assertEqual(self.window.controller.configuration()["mode"], "global")
        self.assertTrue(any("设置已保存" in message for message in messages))
        self.assertFalse(panel._dirty(panel.core_fields, panel.core_baseline))
        self.window.refresh()
        self.wait(lambda: not self.window.refreshing)
        self.assertEqual(panel.core_baseline["mode"], "global")

    def proxy_labels(self, name):
        self.window._render_proxies()
        row = next(item for item in descendants(self.window.proxy_list)
                   if isinstance(item, Adw.ActionRow) and item.get_title() == name)
        return [item.get_label() for item in descendants(row) if isinstance(item, Gtk.Label)]

    def measure_node(self, name, delay):
        with patch.object(self.window.controller, "delay", return_value=delay):
            self.window._action("delay", name)
            self.wait(lambda: not self.window.measurements)
        self.assertIn(f"{delay} ms", self.proxy_labels(name))

    def test_profile_switch_discards_another_profiles_latency(self):
        self.window.navigate_to(2)
        self.measure_node("东京 / Tokyo", 9876)
        profile = next(item for item in self.window.management_state["profiles"]
                       if item["id"] != self.window.management_state["currentProfileID"])
        self.window.manage({"action": "profile.select", "id": profile["id"]})
        self.wait(lambda: not self.window.busy)
        self.assertNotIn("9876 ms", self.proxy_labels("东京 / Tokyo"))
        self.assertIn("42 ms", self.proxy_labels("东京 / Tokyo"))

    def test_catalog_edits_invalidate_same_name_node_latency(self):
        self.window.navigate_to(2)
        self.measure_node("东京 / Tokyo", 9876)
        manager = self.window.management_client
        command = {"action": "catalog.read", "section": "proxies"}
        document = manager.perform(command)["document"]
        self.window.manage({**command, "action": "catalog.save", "text": document["text"],
                            "revision": document["revision"]})
        self.wait(lambda: not self.window.busy)
        self.assertNotIn("9876 ms", self.proxy_labels("东京 / Tokyo"))

    def test_new_core_history_replaces_cached_latency(self):
        self.window.navigate_to(2)
        self.measure_node("东京 / Tokyo", 9876)
        self.window.controller._data["proxies"]["proxies"]["东京 / Tokyo"]["history"] = [{"delay": 12}]
        self.window.refresh()
        self.wait(lambda: not self.window.refreshing)
        self.assertIn("12 ms", self.proxy_labels("东京 / Tokyo"))
        self.assertNotIn("9876 ms", self.proxy_labels("东京 / Tokyo"))

    def test_group_transport_failure_is_distinct_from_an_unavailable_node(self):
        self.window.navigate_to(2)
        original = self.window.controller.delay
        def measure(name):
            if name == "东京 / Tokyo":
                raise ControllerError("测试：请求未完成")
            return original(name)
        with patch.object(self.window.controller, "delay", side_effect=measure):
            self.window.measure_group()
            self.wait(lambda: not self.window.measurements)
        labels = self.proxy_labels("东京 / Tokyo")
        self.assertIn("测速失败", labels)
        self.assertNotIn("不可用", labels)
        self.assertIn("42 ms", self.proxy_labels("自动选择"))

    def log_text(self):
        buffer = self.window.workspace_panel.buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def test_readonly_history_refreshes_on_toolbar_refresh_and_reentry(self):
        self.window.navigate_to(5)
        panel = self.window.workspace_panel
        panel.section.set_selected(panel.section_names.index("logs"))
        self.wait(lambda: ("logs", None) in panel.drafts and not self.window.busy and not self.window.refreshing)
        manager = self.window.management_client
        document = manager.perform({"action": "workspace.read", "section": "logs"})["document"]
        manager._documents[("logs", None)] = {**document, "text": '[{"Payload":"FIRST NEW LOG"}]', "revision": "first"}
        self.window.refresh()
        self.wait(lambda: "FIRST NEW LOG" in self.log_text() and not self.window.refreshing)
        self.window.navigate_to(0)
        manager._documents[("logs", None)] = {**document, "text": '[{"Payload":"SECOND NEW LOG"}]', "revision": "second"}
        self.window.navigate_to(5)
        self.wait(lambda: "SECOND NEW LOG" in self.log_text() and not self.window.busy and not self.window.refreshing)

    def test_toolbar_refresh_keeps_editable_unsaved_workspace_text(self):
        self.window.navigate_to(5)
        panel = self.window.workspace_panel
        self.wait(lambda: panel._key() in panel.drafts and not self.window.busy)
        panel.buffer.set_text('{"keep this draft": true}')
        self.window.refresh()
        self.wait(lambda: not self.window.refreshing)
        self.assertEqual(self.log_text(), '{"keep this draft": true}')

    def test_preview_core_workspace_updates_overview_and_settings(self):
        self.window.navigate_to(5)
        panel = self.window.workspace_panel
        panel.section.set_selected(panel.section_names.index("core"))
        self.wait(lambda: panel._key() in panel.drafts and not self.window.busy)
        value = json.loads(self.log_text())
        panel.buffer.set_text(json.dumps({**value, "mode": "global", "ipv6": True}))
        panel.save()
        self.wait(lambda: not self.window.busy and not self.window.refreshing)
        self.assertTrue(self.window.mode_buttons["global"].get_active())
        self.assertEqual(self.window.management_state["network"]["mode"], "global")
        self.assertEqual(self.window.data["configs"]["mode"], "global")
        self.assertTrue(self.window.settings_panel.core_fields["ipv6"].get_active())

    def import_chooser(self, target):
        class Chooser:
            def __init__(self, **_kwargs):
                pass
            def connect(self, _signal, callback):
                self.callback = callback
            def show(self):
                self.callback(self, Gtk.ResponseType.ACCEPT)
            def get_file(self):
                return target
            def destroy(self):
                pass
        return Chooser

    def test_remote_yaml_import_does_not_require_a_path_or_block_the_ui(self):
        release = threading.Event()
        self.addCleanup(release.set)
        class RemoteFile:
            def read(_file, _cancellable):
                if threading.current_thread() is threading.main_thread():
                    raise AssertionError("file read blocked the GTK thread")
                release.wait(3)
                return Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(b"mixed-port: 7890\n"))
            def get_basename(_file):
                return "RemoteSource.yaml"
        with patch("zfly.profiles.Gtk.FileChooserNative", self.import_chooser(RemoteFile())):
            self.window.profiles_panel.import_file()
        self.assertTrue(self.window.busy)
        self.pump()
        release.set()
        self.wait(lambda: any(item["label"] == "RemoteSource" for item in self.window.management_state["profiles"]) and not self.window.busy)

    def test_import_size_limit_rejects_file_before_creating_a_profile(self):
        errors = []
        self.window.toast = errors.append
        before = len(self.window.management_state["profiles"])
        class RemoteFile:
            def read(_file, _cancellable):
                return Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(b"x" * (4 * 1024 * 1024 + 1)))
            def get_basename(_file):
                return "oversized.yaml"
        with patch("zfly.profiles.Gtk.FileChooserNative", self.import_chooser(RemoteFile())):
            self.window.profiles_panel.import_file()
        self.wait(lambda: not self.window.busy)
        self.assertEqual(len(self.window.management_state["profiles"]), before)
        self.assertTrue(any("4 MiB" in error for error in errors))

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
            with patch("zfly.profiles.Gtk.FileChooserNative", self.export_chooser(target)):
                self.window.profiles_panel.export_file("profile.yaml", b"mixed-port: 7890\n")
            self.assertEqual(target.read_bytes(), b"mixed-port: 7890\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_unknown_choice_value_is_preserved(self):
        from zfly.ui import choice, chosen
        group = Adw.PreferencesGroup()
        row = choice(group, 'value', ['known'], 'future')
        self.assertEqual(chosen(row), 'future')

    def test_cancel_node_in_group_does_not_start_another_request(self):
        release, started = threading.Event(), threading.Event()
        self.window.navigate_to(2)
        calls = []
        def delay(name):
            calls.append(name)
            started.set()
            release.wait(3)
            return 9876
        with patch.object(self.window.controller, 'delay', side_effect=delay):
            try:
                self.window._measure(['东京 / Tokyo', '新加坡'], 'test-group')
                self.wait(started.is_set)
                self.assertFalse(self.window.busy)
                self.window.measure_proxy('东京 / Tokyo')
                self.assertNotIn('东京 / Tokyo', self.window.measuring)
            finally:
                release.set()
            self.wait(lambda: not self.window.measurements)
        self.assertEqual(calls.count('东京 / Tokyo'), 1)
        self.assertNotEqual(self.window.delays.get('东京 / Tokyo'), 9876)

    def test_overlapping_measurements_keep_progress_and_group_cancel_clears_it(self):
        release, started = threading.Event(), threading.Event()
        self.window.navigate_to(2)
        calls = []
        group = self.window.group_picker.get_selected_item().get_string()
        def delay(name):
            calls.append(name)
            started.set()
            release.wait(3)
            return 9876
        with patch.object(self.window.controller, 'delay', side_effect=delay):
            try:
                self.window._measure(['东京 / Tokyo'], group)
                self.wait(started.is_set)
                self.window._measure(['东京 / Tokyo'], 'overlap')
                self.pump()
                self.assertIn('东京 / Tokyo', self.window.measuring)
                self.window.measure_group()
                self.assertNotIn('东京 / Tokyo', self.window.measuring)
                self.assertNotIn(group, self.window.measurements)
            finally:
                release.set()
            self.wait(lambda: not self.window.measurements)
        self.assertEqual(calls, ['东京 / Tokyo'])
        self.assertNotEqual(self.window.delays.get('东京 / Tokyo'), 9876)

    def test_connection_without_id_still_renders(self):
        self.window.data['connections']['connections'] = [{'metadata': {'host': 'example.test'}}]
        self.window._render_connections()
        self.assertIsNotNone(self.window.connection_list.get_first_child())

    def test_readonly_poll_preserves_buffer_selection(self):
        panel = self.window.workspace_panel
        self.window.page = '规则与高级'
        panel.section.set_selected(panel.section_names.index('logs'))
        self.wait(lambda: panel.active_key in panel.drafts and not self.window.busy and not self.window.refreshing)
        panel.buffer.select_range(panel.buffer.get_start_iter(), panel.buffer.get_iter_at_offset(3))
        panel.receive(refresh=True)
        self.wait(lambda: not self.window.busy and not self.window.refreshing)
        self.assertTrue(panel.buffer.get_has_selection())

    def test_proxy_picker_falls_back_when_selected_group_disappears(self):
        self.window.page = '代理组'
        proxies = self.window.data['proxies']['proxies']
        proxies.clear()
        proxies.update({'GLOBAL': {'type': 'Selector', 'all': ['second']}, 'second': {'type': 'Selector', 'all': ['DIRECT']}})
        self.window._render_lists(force=True)
        self.assertEqual(self.window.group_picker.get_selected_item().get_string(), 'second')
        self.assertIsNotNone(self.window.proxy_list.get_first_child())


if __name__ == "__main__":
    if not SAFE_MODE or not UI_AVAILABLE:
        raise SystemExit("Run with --safe-mode using GTK / libadwaita and an isolated display.")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit("An isolated graphical display is required.")
    unittest.main()
