import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .controller import Controller, ControllerError, PreviewController
from .management import ManagementClient, PreviewManagement
from .settings import SettingsPanel
from .workspace import WorkspacePanel, ToolsPanel
from .catalog import CatalogPanel
from .native_runtime import NativeRuntime
from .ui import EmptyState, install_styles, label, page, page_box


def size(value):
    number = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(number) < 1024 or unit == "TiB":
            return f"{number:.1f} {unit}"
        number /= 1024


def destination(connection):
    meta = connection.get("metadata") or {}
    host = meta.get("host") or meta.get("destinationIP") or "未知目标"
    port = meta.get("destinationPort")
    return f"{host}:{port}" if port else host


def clear(box):
    child = box.get_first_child()
    while child:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def action_button(label, callback, icon=None):
    button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    if icon:
        button.set_icon_name(icon)
    button.set_tooltip_text(label)
    button.connect("clicked", lambda _button: callback())
    return button


class Window(Adw.ApplicationWindow):
    def __init__(self, app, preview):
        super().__init__(application=app, title="ZFly", default_width=1040, default_height=720)
        self.preview = preview
        self.controller = PreviewController() if preview else None
        self.management_client = PreviewManagement(self.controller) if preview else None
        self.management_state = self.management_client.snapshot() if preview else None
        self.management_status = "演示模式 · 操作不会修改系统" if preview else "正在启动 ZFly 本地运行时…"
        self.management_endpoint = ""
        self.runtime = None if preview else NativeRuntime()
        self.shutdown_complete = False
        self.data_directory = ""
        self.data = None
        self.delays = {}
        self.busy = False
        self.refreshing = False
        self.operation_revision = 0
        self.closed = False
        self.connected = preview
        self.generation = 0
        self.page = "概览"
        self.last_proxies = None
        self.last_connections = None
        self.updating_modes = False
        self._build()
        self.connect("close-request", self._close)
        self.timer = GLib.timeout_add_seconds(2, self._tick)
        if preview:
            self.refresh()
        else:
            self.start_runtime()

    def _build(self):
        install_styles()
        self.status = label("离线预览" if self.preview else "正在准备…", "caption")
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_toolbar = Adw.ToolbarView(content=sidebar)
        brand = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        brand.append(Gtk.Image(icon_name="io.github.zfly.ZFly", pixel_size=32))
        brand.append(Adw.WindowTitle(title="ZFly", subtitle="连接，自由掌握"))
        sidebar_toolbar.add_top_bar(Adw.HeaderBar(title_widget=brand))
        self.navigation = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, vexpand=True)
        self.navigation.add_css_class("navigation-sidebar")
        for name, icon in (("概览", "view-grid-symbolic"), ("配置与订阅", "document-new-symbolic"), ("代理组", "network-workgroup-symbolic"), ("连接", "network-transmit-receive-symbolic"), ("自定义资源", "folder-symbolic"), ("规则与高级", "text-x-generic-symbolic"), ("网络工具", "applications-engineering-symbolic"), ("设置", "preferences-system-symbolic")):
            row = Gtk.ListBoxRow()
            row.page = name
            content = Gtk.Box(spacing=12, margin_top=11, margin_bottom=11, margin_start=12, margin_end=12)
            content.append(Gtk.Image(icon_name=icon))
            content.append(label(name))
            row.set_child(content)
            self.navigation.append(row)
        sidebar.append(Gtk.ScrolledWindow(child=self.navigation, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True))
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=16, margin_end=16, margin_top=12, margin_bottom=16)
        activity_row = Gtk.Box(spacing=8)
        self.activity = Gtk.Spinner(visible=False)
        activity_row.append(self.activity)
        activity_row.append(self.status)
        footer.append(activity_row)
        self.settings_button = action_button("运行信息", self.settings)
        self.settings_button.add_css_class("flat")
        footer.append(self.settings_button)
        sidebar.append(footer)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=160)
        self.content_title = Adw.WindowTitle(title="概览")
        self.refresh_button = action_button("刷新", self.refresh, "view-refresh-symbolic")
        header = Adw.HeaderBar(title_widget=self.content_title)
        header.pack_end(self.refresh_button)
        self.search = Gtk.SearchEntry(placeholder_text="搜索节点", margin_start=24, margin_end=24, margin_top=12, margin_bottom=12)
        self.search.connect("search-changed", lambda _entry: self._render_lists(force=True))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.search)
        content.append(self.stack)
        self._build_overview()
        self._build_lists()

        self.settings_panel = SettingsPanel(self)
        self.stack.add_named(self.settings_panel, "设置")
        self.stack.add_named(self.settings_panel.profiles, "配置与订阅")
        self.workspace_panel = WorkspacePanel(self)
        self.catalog_panel = CatalogPanel(self)
        self.tools_panel = ToolsPanel(self)
        self.stack.add_named(page(self.workspace_panel, scroll=False), "规则与高级")
        self.stack.add_named(page(self.catalog_panel, scroll=False), "自定义资源")
        self.stack.add_named(page(self.tools_panel), "网络工具")

        toolbar = Adw.ToolbarView(content=content)
        toolbar.add_top_bar(header)
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.content_page = Adw.NavigationPage(title="概览", child=self.toasts)
        self.split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="ZFly", child=sidebar_toolbar),
            content=self.content_page, min_sidebar_width=220, max_sidebar_width=260,
        )
        self.set_content(self.split)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 700sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 500sp"))
        compact.add_setter(self.split, "collapsed", True)
        compact.add_setter(self.hero_header, "orientation", Gtk.Orientation.VERTICAL)
        compact.add_setter(self.power_button, "halign", Gtk.Align.START)
        self.add_breakpoint(compact)
        self.navigation.connect("row-selected", self._navigate)
        self.navigation.select_row(self.navigation.get_row_at_index(0))

    def _build_overview(self):
        self.overview = page_box()
        heading = Gtk.Box(spacing=12)
        heading.append(label("连接概览", "title-1", hexpand=True))
        preview_badge = label("离线预览", "zfly-preview", valign=Gtk.Align.CENTER)
        preview_badge.set_visible(self.preview)
        heading.append(preview_badge)
        self.overview.append(heading)
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        hero.add_css_class("zfly-hero")
        status_row = Gtk.Box(spacing=14)
        self.status_icon = Gtk.Image(icon_name="network-wired-symbolic", pixel_size=24, valign=Gtk.Align.CENTER)
        self.status_icon.add_css_class("zfly-status-icon")
        status_row.append(self.status_icon)
        status_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.running_title = label("正在准备连接", "title-2")
        self.running_description = label("正在启动本地服务", "dim-label")
        status_text.append(self.running_title)
        status_text.append(self.running_description)
        status_row.append(status_text)
        status_row.set_hexpand(True)
        self.hero_header = Gtk.Box(spacing=18)
        self.hero_header.append(status_row)
        self.power_button = action_button("启动代理", self.toggle_running)
        self.power_button.add_css_class("suggested-action")
        self.power_button.add_css_class("pill")
        self.power_button.set_halign(Gtk.Align.END)
        self.power_button.set_sensitive(False)
        self.hero_header.append(self.power_button)
        hero.append(self.hero_header)
        modes = Gtk.Box(spacing=4, homogeneous=True)
        modes.add_css_class("zfly-mode")
        self.mode_buttons = {}
        previous = None
        for mode, title in (("rule", "规则"), ("global", "全局"), ("direct", "直连")):
            item = Gtk.ToggleButton(label=title)
            item.add_css_class("flat")
            if previous:
                item.set_group(previous)
            previous = item
            item.connect("clicked", lambda button, mode=mode: self._mode_clicked(button, mode))
            self.mode_buttons[mode] = item
            modes.append(item)
        hero.append(modes)
        self.overview.append(hero)

        context = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        context.add_css_class("boxed-list")
        self.profile_row = Adw.ActionRow(title="当前配置", subtitle="尚未选择配置")
        self.profile_row.add_prefix(Gtk.Image(icon_name="document-new-symbolic"))
        self.profile_row.add_suffix(action_button("管理", lambda: self.navigate_to(1)))
        self.route_row = Adw.ActionRow(title="节点选择", subtitle="启动代理后查看节点")
        self.route_row.add_prefix(Gtk.Image(icon_name="network-workgroup-symbolic"))
        self.route_row.add_suffix(action_button("查看", lambda: self.navigate_to(2)))
        self.system_proxy_row = Adw.ActionRow(title="系统代理", subtitle="尚未开启")
        self.system_proxy_row.add_prefix(Gtk.Image(icon_name="preferences-system-symbolic"))
        self.system_proxy_row.add_suffix(action_button("设置", self.open_network_settings))
        for row in (self.profile_row, self.route_row, self.system_proxy_row):
            row.set_use_markup(False)
            row.set_subtitle_lines(2)
            context.append(row)
        self.overview.append(context)

        traffic = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        traffic.append(label("流量与活动", "heading"))
        self.metrics = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=2,
                                   max_children_per_line=4, homogeneous=True, column_spacing=12, row_spacing=12)
        self.metric_rows = {}
        for name, icon in (("累计下载", "go-down-symbolic"), ("累计上传", "go-up-symbolic"), ("当前连接", "network-transmit-receive-symbolic"), ("核心内存", "applications-engineering-symbolic")):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            card.add_css_class("card")
            card.add_css_class("zfly-metric")
            caption = Gtk.Box(spacing=6)
            caption.append(Gtk.Image(icon_name=icon))
            caption.append(label(name, "caption"))
            caption.add_css_class("dim-label")
            card.append(caption)
            value = label("—", "zfly-metric-value", selectable=True)
            card.append(value)
            self.metric_rows[name] = value
            self.metrics.insert(card, -1)
        traffic.append(self.metrics)
        traffic.append(label("累计流量来自当前核心会话", "caption", selectable=True))
        self.overview.append(traffic)
        details = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        details.add_css_class("boxed-list")
        expander = Adw.ExpanderRow(title="运行详情", subtitle="端口、核心版本与最近更新时间")
        for name in ("路由模式", "代理端口", "核心版本", "最近更新"):
            row = Adw.ActionRow(title=name)
            value = label("—", selectable=True)
            row.add_suffix(value)
            self.metric_rows[name] = value
            expander.add_row(row)
        details.append(expander)
        self.overview.append(details)
        self.stack.add_named(page(self.overview), "概览")

    def _build_lists(self):
        proxies = page_box(16)
        top = Gtk.Box(spacing=12)
        top.append(label("代理组", "title-1", hexpand=True))
        self.measure_button = action_button("整组测速", self.measure_group)
        top.append(self.measure_button)
        proxies.append(top)
        self.group_picker = Adw.ComboRow(title="当前代理组")
        picker_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        picker_box.add_css_class("boxed-list")
        picker_box.append(self.group_picker)
        proxies.append(picker_box)
        self.group_note = label("", "dim-label")
        proxies.append(self.group_note)
        self.proxy_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.proxy_list.add_css_class("boxed-list")
        proxies.append(self.proxy_list)
        self.proxy_empty = EmptyState("还没有代理节点", "添加配置后，代理节点会显示在这里。", "network-workgroup-symbolic", ("添加配置", lambda: self.navigate_to(1)))
        self.proxy_no_results = EmptyState("没有匹配的节点", "试试其他名称，或清除搜索条件。", "edit-find-symbolic", ("清除搜索", lambda: self.search.set_text("")))
        proxies.append(self.proxy_empty)
        proxies.append(self.proxy_no_results)
        self.group_picker.connect("notify::selected", lambda *_args: self._render_proxies())
        self.stack.add_named(page(proxies), "代理组")

        connections = page_box(16)
        connections.append(label("活动连接", "title-1"))
        self.connection_note = label("暂无连接", "dim-label")
        connections.append(self.connection_note)
        self.connection_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.connection_list.add_css_class("boxed-list")
        connections.append(self.connection_list)
        self.connection_empty = EmptyState("暂时没有连接", "代理启动后，经过核心的连接会显示在这里。", "network-transmit-receive-symbolic")
        self.connection_no_results = EmptyState("没有匹配的连接", "可以搜索目标域名、地址或进程名。", "edit-find-symbolic", ("清除搜索", lambda: self.search.set_text("")))
        connections.append(self.connection_empty)
        connections.append(self.connection_no_results)
        self.stack.add_named(page(connections), "连接")

    def navigate_to(self, index):
        self.navigation.select_row(self.navigation.get_row_at_index(index))
        self.split.set_show_content(True)

    def open_network_settings(self):
        self.navigate_to(7)
        self.settings_panel.categories.set_selected(1)

    def _navigate(self, _list, row):
        if row:
            previous = self.page
            self.page = row.page
            self.stack.set_visible_child_name(self.page)
            self.content_title.set_title(self.page)
            self.content_page.set_title(self.page)
            self.split.set_show_content(True)
            self.search.set_visible(self.page in ("代理组", "连接"))
            self.search.set_placeholder_text("搜索节点名称" if self.page == "代理组" else "搜索域名、地址或进程")
            if previous != self.page:
                self.search.set_text("")
            self._render_lists(force=True)
            if self.page == "规则与高级":
                self.workspace_panel.receive()
            if self.page == "自定义资源":
                self.catalog_panel.receive()

    def _mode_clicked(self, button, mode):
        if self.updating_modes:
            return
        state = self.management_state
        if button.get_active() and state and state["network"]["mode"] != mode:
            self.set_mode(mode)
        else:
            self._update_overview()

    def _update_overview(self):
        state = self.management_state
        ready = state is not None
        running = bool(state and state["running"])
        profiles = state["profiles"] if state else []
        current = next((item for item in profiles if item["id"] == state["currentProfileID"]), None) if state else None
        if not ready:
            title, description = "运行时尚未就绪", self.management_status
        elif not current:
            title, description = "从一份配置开始", "添加订阅或导入 YAML，即可启动代理。"
        elif running and not self.connected:
            title, description = "核心连接中断", "正在尝试重新连接，可在运行信息中查看状态。"
        elif running:
            title, description = "代理运行中", "演示状态，不会连接核心或更改系统网络。" if self.preview else "按所选路由模式处理连接。"
        else:
            title, description = "代理已停止", "配置已准备好，随时可以启动。"
        self.running_title.set_label(title)
        self.running_description.set_label(description)
        self.status_icon.set_from_icon_name("network-wired-symbolic" if running else "network-offline-symbolic")
        if running:
            self.status_icon.remove_css_class("stopped")
        else:
            self.status_icon.add_css_class("stopped")
        self.profile_row.set_subtitle(current["label"] if current else "尚未选择配置")
        self.system_proxy_row.set_subtitle(
            "状态暂不可用" if not ready else
            "演示：已开启" if self.preview and state and state["network"]["systemProxy"] and running else
            "已开启 · 仅遵循系统代理的应用生效" if state and state["network"]["systemProxy"] and running else
            "代理停止时不接管系统网络" if not running else "未开启 · 可在设置中启用")
        proxies = self.data["proxies"]["proxies"] if self.data else {}
        selected = self.group_picker.get_selected_item()
        group_name = selected.get_string() if selected else None
        group = proxies.get(group_name, {})
        self.route_row.set_subtitle("节点信息暂不可用" if not ready or not self.connected else f"{group_name} → {group['now']}" if running and group.get("now") else "启动代理后查看节点" if not running else "当前配置没有可选代理组")
        mode = state["network"]["mode"] if state else "rule"
        self.updating_modes = True
        self.mode_buttons[mode].set_active(True)
        for item in self.mode_buttons.values():
            item.set_sensitive(ready and not self.busy)
        self.updating_modes = False

    def _tick(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def start_runtime(self):
        if self.preview or self.busy or self.closed:
            return
        self.status.set_label("正在启动 ZFly 内置核心…")
        def ready(connection):
            if connection is not None:
                self.controller = Controller(connection["endpoint"], connection["secret"])
                self.management_client = ManagementClient(connection["endpoint"], connection["secret"])
                self.management_endpoint = connection["endpoint"]
                self.data_directory = connection["dataDirectory"]
            self.management_status = "ZFly 运行时已就绪"
            self.refresh()
        def start():
            if self.runtime.process is not None and self.runtime.process.poll() is None and self.management_client:
                self.management_client.perform({"action": "tool.run", "tool": "restart"})
                return None
            return self.runtime.start()
        self._submit(start, ready)

    def _close(self, *_args):
        if self.preview:
            self.closed = True
            self.generation += 1
            GLib.source_remove(self.timer)
            return False
        if self.shutdown_complete:
            return False
        if self.closed:
            return True
        self.closed = True
        self.generation += 1
        GLib.source_remove(self.timer)
        self.set_sensitive(False)
        self.status.set_label("正在还原系统代理并退出…")
        def work():
            stopped = self.runtime.stop()
            GLib.idle_add(finished, stopped)
        def finished(stopped):
            if stopped:
                self.shutdown_complete = True
                self.close()
            else:
                self.closed = False
                self.set_sensitive(True)
                self.timer = GLib.timeout_add_seconds(2, self._tick)
                self.toast("运行时仍在清理，请稍后再次退出。")
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True, name="zfly-shutdown").start()
        return True

    def toggle_running(self):
        state = self.management_state
        if state:
            self.manage({"action": "running.set", "running": not state["running"]}, lambda _result: self.refresh())

    def set_mode(self, mode):
        import copy
        if self.management_state:
            expected = copy.deepcopy(self.management_state["network"])
            network = dict(expected, mode=mode)
            self.manage({"action": "network.save", "network": network, "expected": expected}, lambda _result: self.refresh())

    def _sensitivity(self):
        active = self.busy or self.refreshing
        self.activity.set_visible(active)
        if active:
            self.activity.start()
        else:
            self.activity.stop()
        self.power_button.set_sensitive(not self.busy and bool(self.management_state and self.management_state["currentProfileID"]))
        self.power_button.set_label("停止代理" if self.management_state and self.management_state["running"] else "启动代理")
        self.refresh_button.set_sensitive(not self.busy and self.controller is not None)
        self.settings_button.set_sensitive(not self.busy)
        self.measure_button.set_sensitive(not self.busy and self.connected and self.group_picker.get_selected_item() is not None)
        self.proxy_list.set_sensitive(not self.busy and self.connected)
        self.connection_list.set_sensitive(not self.busy and self.connected)
        self.settings_panel.set_sensitive(not self.busy)
        self.settings_panel.profiles.set_sensitive(not self.busy)
        self.workspace_panel.set_sensitive(not self.busy)
        self.catalog_panel.set_sensitive(not self.busy)
        self.tools_panel.set_sensitive(not self.busy)
        self._update_overview()

    def _submit(self, operation, receive, action=False, done=None, background=False, on_error=None):
        if self.busy or self.closed or (background and self.refreshing):
            return
        if background:
            self.refreshing = True
        else:
            self.busy = True
            self.operation_revision += 1
        revision = self.generation
        operation_revision = self.operation_revision
        self._sensitivity()

        def finish(value, error):
            if self.closed:
                return GLib.SOURCE_REMOVE
            if background:
                self.refreshing = False
            else:
                self.busy = False
            if done:
                done()
            if revision == self.generation and operation_revision == self.operation_revision:
                if error:
                    if on_error:
                        on_error(str(error))
                    elif action:
                        self.toast(str(error))
                    else:
                        self.connected = False
                        self.status.set_label("连接失败：" + str(error))
                else:
                    try:
                        receive(value)
                    except Exception:
                        (on_error or self.toast)("返回的数据格式无效，请刷新后重试。")
            self._sensitivity()
            self.workspace_panel.resume()
            self.catalog_panel.resume()
            return GLib.SOURCE_REMOVE

        def work():
            try:
                value = operation()
            except Exception as error:
                message = str(error) if isinstance(error, ControllerError) else "控制器返回了无法识别的数据。"
                GLib.idle_add(finish, None, ControllerError(message))
            else:
                GLib.idle_add(finish, value, None)

        threading.Thread(target=work, daemon=True, name="zfly-controller").start()

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))

    def refresh(self):
        if not self.controller:
            return
        controller, management = self.controller, self.management_client
        def work():
            try:
                data, core_error = controller.snapshot(), None
            except ControllerError as error:
                data, core_error = None, str(error)
            try:
                state = management.snapshot() if management else None
                status = "演示模式 · 操作不会修改系统" if self.preview else "ZFly 运行时已就绪"
            except ControllerError as error:
                state, status = None, "本地运行时未连接：" + str(error)
            return data, core_error, state, status
        def receive(result):
            data, core_error, state, status = result
            self.management_state, self.management_status = state, status
            if data:
                self._receive(data)
            else:
                self.connected = False
                self.status.set_label("核心未连接：" + (core_error or "请检查 ZFly 运行信息。"))
            self.settings_panel.receive()
            self.workspace_panel.receive()
            self.catalog_panel.receive()
        self._submit(work, receive, background=True)

    def manage(self, command, receive=None, pending=None, done=None, on_error=None):
        if not self.management_client or self.management_state is None:
            (on_error or self.toast)("请等待 ZFly 本地运行时就绪。")
            return
        if self.busy or self.closed:
            return
        management = self.management_client
        if pending:
            pending.operation_pending = True
            pending.set_sensitive(False)
            pending.set_deletable(False)
        def complete():
            if pending:
                pending.operation_pending = False
                pending.set_sensitive(True)
                pending.set_deletable(True)
            if done:
                done()
        def apply(result):
            self.management_state = result["state"]
            restored = command["action"] == "tool.run" and command.get("tool") in (
                "restoreLocalProfiles", "restoreLocalAll", "restoreDAVProfiles", "restoreDAVAll",
            )
            if restored:
                self.catalog_panel.reset()
                if command["tool"].endswith("All"):
                    self.workspace_panel.reset()
                    self.settings_panel.reset()
                else:
                    self.workspace_panel.invalidate_profiles()
                self.delays.clear()
                self.last_proxies = self.last_connections = None
            if self.preview and command["action"] == "network.save":
                self._receive(self.controller.snapshot())
            self.settings_panel.receive()
            if receive:
                receive(result)
            self.workspace_panel.receive()
            self.catalog_panel.receive()
            if not command["action"].endswith((".read", ".list")):
                self.toast("已更新演示数据" if self.preview else "操作已完成。")
            if restored:
                self.refresh()
        self._submit(lambda: management.perform(command), apply, action=True, done=complete, on_error=on_error)

    def _receive(self, data):
        self.data = data
        self.connected = True
        self.status.set_label("本地运行时未连接" if self.management_state is None else "离线预览 · 无系统变更" if self.preview else "代理运行中" if self.management_state["running"] else "代理已停止")
        connections = data["connections"]
        values = {
            "累计下载": size(connections.get("downloadTotal")),
            "累计上传": size(connections.get("uploadTotal")),
            "当前连接": str(len(connections.get("connections") or [])),
            "核心内存": size(connections.get("memory")),
            "路由模式": {"rule": "规则", "global": "全局", "direct": "直连"}.get(data["configs"].get("mode"), "—"),
            "代理端口": str(data["configs"].get("mixed-port", "—")),
            "核心版本": data["version"]["version"],
            "最近更新": "演示数据" if self.preview else datetime.now().strftime("%H:%M:%S"),
        }
        for name, value in values.items():
            self.metric_rows[name].set_label(value)
        self._render_lists()
        self.settings_panel.receive()

    def _render_lists(self, force=False):
        if not self.data:
            return
        proxies = self.data["proxies"]["proxies"]
        groups = sorted((name for name, proxy in proxies.items() if proxy.get("all") is not None),
                        key=lambda name: (proxies[name].get("type") != "Selector", name))
        model = self.group_picker.get_model()
        old_names = [model.get_string(i) for i in range(model.get_n_items())] if model else []
        if groups != old_names:
            current = self.group_picker.get_selected_item()
            selected = current.get_string() if current else None
            self.group_picker.set_model(Gtk.StringList.new(groups))
            if selected in groups:
                self.group_picker.set_selected(groups.index(selected))
        if self.page == "代理组" and (force or proxies != self.last_proxies):
            self._render_proxies()
            self.last_proxies = proxies
        connections = self.data["connections"].get("connections") or []
        if self.page == "连接" and (force or connections != self.last_connections):
            self._render_connections()
            self.last_connections = connections
        self._update_overview()

    def _render_proxies(self):
        self.proxy_empty.set_visible(False)
        self.proxy_no_results.set_visible(False)
        if not self.data:
            return
        clear(self.proxy_list)
        self.proxy_list.set_visible(False)
        selected = self.group_picker.get_selected_item()
        if not selected:
            self.group_note.set_label("暂无代理组，请先添加并选择配置。")
            self.proxy_empty.set_visible(True)
            return
        name = selected.get_string()
        proxies = self.data["proxies"]["proxies"]
        group = proxies.get(name)
        if group is None:
            self.group_note.set_label("代理组已变化，请重新选择。")
            return
        manual = group["type"] == "Selector"
        members = group.get("all", [])
        self.group_note.set_label(f"{len(members)} 个节点 · " + ("手动选择，立即生效" if manual else "由核心自动选择节点"))
        query = self.search.get_text().casefold()
        names = [proxy for proxy in members if query in proxy.casefold()]
        self.proxy_list.set_visible(bool(names))
        self.proxy_empty.set_visible(not members)
        self.proxy_no_results.set_visible(bool(members) and not names)
        for proxy in names[:200]:
            info = proxies.get(proxy, {})
            history = info.get("history") or []
            delay = self.delays.get(proxy, history[-1].get("delay") if history else None)
            delay_text = "未测速" if delay is None else (f"{delay} ms" if delay > 0 else "不可用")
            row = Adw.ActionRow(title=proxy, subtitle=info.get('type', 'Proxy'))
            row.set_use_markup(False)
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            if group.get("now") == proxy:
                row.add_prefix(Gtk.Image(icon_name="object-select-symbolic"))
            latency = Gtk.Label(label=delay_text, valign=Gtk.Align.CENTER)
            latency.add_css_class("caption")
            latency.add_css_class("dim-label" if delay is None else "error" if delay <= 0 else "success" if delay < 150 else "warning")
            row.add_suffix(latency)
            row.add_suffix(action_button("测速", lambda proxy=proxy: self._action("delay", proxy)))
            if manual:
                button = action_button("已选" if group.get("now") == proxy else "选择", lambda proxy=proxy: self._action("select", name, proxy))
                button.set_sensitive(group.get("now") != proxy)
                row.add_suffix(button)
            self.proxy_list.append(row)
        if len(names) > 200:
            self.group_note.set_label(self.group_note.get_label() + " · 显示前 200 项，请搜索筛选")
        self._update_overview()

    def _render_connections(self):
        clear(self.connection_list)
        query = self.search.get_text().casefold()
        items = self.data["connections"].get("connections") or []
        total = len(items)
        items = [item for item in items if query in (destination(item) + " " + str((item.get("metadata") or {}).get("process", ""))).casefold()]
        self.connection_note.set_label((f"{len(items)} / {total} 条连接" if query else f"{total} 条连接") + (" · 显示前 200 条，请搜索筛选" if len(items) > 200 else ""))
        self.connection_list.set_visible(bool(items))
        self.connection_empty.set_visible(not total)
        self.connection_no_results.set_visible(bool(total) and not items)
        for connection in items[:200]:
            meta = connection.get("metadata") or {}
            subtitle = " · ".join(str(value) for value in (
                meta.get("process"), meta.get("network"), connection.get("rule"),
                " ← ".join(connection.get("chains") or []),
                "↓ " + size(connection.get("download")), "↑ " + size(connection.get("upload")),
            ) if value)
            row = Adw.ActionRow(title=destination(connection), subtitle=subtitle)
            row.set_use_markup(False)
            row.add_suffix(action_button("关闭这条连接", lambda identity=connection["id"]: self._action("close", identity), "window-close-symbolic"))
            self.connection_list.append(row)

    def _action(self, method, *args):
        if not self.connected or not self.controller:
            return
        controller = self.controller
        management = self.management_client if self.management_state else None

        def work():
            state = None
            if method == "select" and management:
                state = management.perform({"action": "proxy.select", "group": args[0], "proxy": args[1]})["state"]
                if self.preview:
                    controller.select(*args)
                value = None
            else:
                value = getattr(controller, method)(*args)
            return value, controller.snapshot(), state

        def receive(result):
            value, data, state = result
            if state:
                self.management_state = state
            if method == "delay":
                self.delays[args[0]] = value
                self.last_proxies = None
            self._receive(data)

        self._submit(work, receive, action=True)

    def measure_group(self):
        if not self.controller or not self.data:
            return
        selected = self.group_picker.get_selected_item()
        if not selected:
            return
        members = self.data["proxies"]["proxies"].get(selected.get_string(), {}).get("all") or []
        controller = self.controller
        def measure(name):
            try:
                return name, controller.delay(name)
            except ControllerError:
                return name, 0
        def work():
            with ThreadPoolExecutor(max_workers=4) as pool:
                return dict(pool.map(measure, members))
        def receive(values):
            self.delays.update(values)
            self._render_proxies()
        self._submit(work, receive, action=True)

    def settings(self):
        dialog = Adw.MessageDialog(transient_for=self, modal=True, heading="ZFly 独立运行时",
            body=self.management_status + "\n" + self.management_endpoint + "\n数据目录：" + self.data_directory)
        dialog.add_response("close", "好")
        if not self.connected or self.management_state is None:
            dialog.add_response("retry", "重试启动")
            dialog.connect("response", lambda _dialog, response: self.start_runtime() if response == "retry" else None)
        dialog.present()


class Application(Adw.Application):
    def __init__(self, preview=False, start_hidden=False):
        super().__init__(application_id="io.github.zfly.ZFly.Preview" if preview else "io.github.zfly.ZFly")
        self.preview = preview
        self.start_hidden = start_hidden
        self.window = None
        self.hidden_start_hold = False

    def do_activate(self):
        Gtk.Window.set_default_icon_name("io.github.zfly.ZFly")
        if self.window is None or self.window.shutdown_complete:
            self.window = Window(self, self.preview)
        if self.start_hidden and not self.preview:
            self.start_hidden = False
            self.hidden_start_hold = True
            self.hold()
            return
        self.window.present()
        if self.hidden_start_hold:
            self.hidden_start_hold = False
            self.release()
