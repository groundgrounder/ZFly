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
        self.management_client = PreviewManagement() if preview else None
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
        self._build()
        self.connect("close-request", self._close)
        self.timer = GLib.timeout_add_seconds(2, self._tick)
        if preview:
            self.refresh()
        else:
            self.start_runtime()

    def _build(self):
        self.status = Gtk.Label(label="演示模式 · 不连接核心" if self.preview else "尚未连接", wrap=True, xalign=0)
        self.status.add_css_class("caption")
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_toolbar = Adw.ToolbarView(content=sidebar)
        brand = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        brand.append(Gtk.Image(icon_name="io.github.zfly.ZFly", pixel_size=36))
        brand.append(Adw.WindowTitle(title="ZFly", subtitle="原生网络客户端"))
        sidebar_header = Adw.HeaderBar(title_widget=brand)
        sidebar_toolbar.add_top_bar(sidebar_header)
        self.navigation = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, vexpand=True)
        self.navigation.add_css_class("navigation-sidebar")
        for name, icon in (("概览", "view-grid-symbolic"), ("配置与订阅", "document-new-symbolic"), ("代理组", "network-workgroup-symbolic"), ("连接", "network-transmit-receive-symbolic"), ("自定义资源", "folder-symbolic"), ("规则与高级", "text-x-generic-symbolic"), ("网络工具", "applications-engineering-symbolic"), ("设置", "preferences-system-symbolic")):
            row = Gtk.ListBoxRow()
            row.page = name
            content = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
            content.append(Gtk.Image(icon_name=icon))
            content.append(Gtk.Label(label=name, xalign=0))
            row.set_child(content)
            self.navigation.append(row)
        sidebar.append(self.navigation)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=16, margin_end=16, margin_bottom=16)
        footer.append(self.status)
        self.activity = Gtk.Spinner()
        self.activity.set_halign(Gtk.Align.END)
        self.activity.set_visible(False)
        footer.append(self.activity)
        self.settings_button = action_button("运行信息", self.settings, "preferences-system-symbolic")
        self.settings_button.set_sensitive(not self.preview)
        footer.append(self.settings_button)
        sidebar.append(footer)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(220)
        self.content_title = Adw.WindowTitle(title="概览")
        self.refresh_button = action_button("刷新", self.refresh, "view-refresh-symbolic")
        header = Adw.HeaderBar(title_widget=self.content_title)
        header.pack_end(self.refresh_button)
        self.search = Gtk.SearchEntry(placeholder_text="搜索节点或连接", margin_start=20, margin_end=20, margin_top=12)
        self.search.connect("search-changed", lambda _entry: self._render_lists(force=True))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.append(self.search)
        content.append(self.stack)
        self.stack.set_vexpand(True)
        self.overview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_start=24, margin_end=24, margin_top=12, margin_bottom=24)
        overview_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.overview)
        self.stack.add_named(overview_scroll, "概览")
        title = Gtk.Label(label="让连接，自由发生。", xalign=0)
        title.add_css_class("title-1")
        self.overview.append(title)
        self.power_button = action_button("启动代理", self.toggle_running)
        self.power_button.add_css_class("suggested-action")
        self.power_button.add_css_class("pill")
        self.power_button.set_sensitive(False)
        self.overview.append(self.power_button)
        modes = Gtk.Box(spacing=8)
        for mode, label in (("rule", "规则"), ("global", "全局"), ("direct", "直连")):
            modes.append(action_button(label, lambda mode=mode: self.set_mode(mode)))
        self.overview.append(modes)
        self.metrics = Adw.PreferencesGroup(title="运行状态", description="ZFly 独立管理配置、订阅和代理核心")
        self.metric_rows = {}
        for name in ("累计下载", "累计上传", "当前连接", "核心内存", "路由模式", "代理端口", "核心版本", "最近更新"):
            row = Adw.ActionRow(title=name)
            value = Gtk.Label(label="—", selectable=True)
            row.add_suffix(value)
            self.metric_rows[name] = value
            self.metrics.add(row)
        self.overview.append(self.metrics)
        explanation = Gtk.Label(label="添加订阅或导入 YAML，选择配置并启动代理。系统代理会在停止或退出时恢复。", wrap=True, xalign=0)
        explanation.add_css_class("dim-label")
        self.overview.append(explanation)

        proxies = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_start=24, margin_end=24, margin_bottom=24)
        self.group_picker = Adw.ComboRow(title="代理组")
        picker_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        picker_box.add_css_class("boxed-list")
        picker_box.append(self.group_picker)
        proxies.append(picker_box)
        self.group_note = Gtk.Label(xalign=0, wrap=True)
        self.group_note.add_css_class("dim-label")
        proxies.append(self.group_note)
        proxies.append(action_button("整组测速", self.measure_group))
        self.proxy_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.proxy_list.add_css_class("boxed-list")
        proxies.append(self.proxy_list)
        self.group_picker.connect("notify::selected", lambda *_args: self._render_proxies())
        self.stack.add_named(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=proxies), "代理组")

        connections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24, margin_end=24, margin_bottom=24)
        self.connection_note = Gtk.Label(label="暂无连接", xalign=0, wrap=True)
        self.connection_note.add_css_class("dim-label")
        connections.append(self.connection_note)
        self.connection_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.connection_list.add_css_class("boxed-list")
        connections.append(self.connection_list)
        self.stack.add_named(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=connections), "连接")

        self.settings_panel = SettingsPanel(self)
        self.stack.add_named(self.settings_panel, "设置")
        self.workspace_panel = WorkspacePanel(self)
        self.catalog_panel = CatalogPanel(self)
        self.tools_panel = ToolsPanel(self)
        self.stack.add_named(self.workspace_panel, "规则与高级")
        self.stack.add_named(self.catalog_panel, "自定义资源")
        self.stack.add_named(Gtk.ScrolledWindow(child=self.tools_panel, hscrollbar_policy=Gtk.PolicyType.NEVER), "网络工具")

        toolbar = Adw.ToolbarView(content=content)
        toolbar.add_top_bar(header)
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="ZFly", child=sidebar_toolbar),
            content=Adw.NavigationPage(title="概览", child=self.toasts),
        )
        self.set_content(self.split)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 700sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        self.navigation.connect("row-selected", self._navigate)
        self.navigation.select_row(self.navigation.get_row_at_index(0))

    def _navigate(self, _list, row):
        if row:
            self.page = row.page
            self.stack.set_visible_child_name("设置" if self.page == "配置与订阅" else self.page)
            if self.page == "配置与订阅":
                self.settings_panel.categories.set_selected(1)
            self.content_title.set_title(self.page)
            self.split.set_show_content(True)
            self.search.set_visible(self.page in ("代理组", "连接"))
            self._render_lists(force=True)
            if self.page == "规则与高级":
                self.workspace_panel.receive()
            if self.page == "自定义资源":
                self.catalog_panel.receive()

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
        if self.shutdown_complete or self.preview:
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
        self.settings_button.set_sensitive(not self.preview and not self.busy)
        self.proxy_list.set_sensitive(not self.busy and self.connected)
        self.connection_list.set_sensitive(not self.busy and self.connected)
        self.settings_panel.set_sensitive(not self.busy)
        self.workspace_panel.set_sensitive(not self.busy)
        self.catalog_panel.set_sensitive(not self.busy)
        self.tools_panel.set_sensitive(not self.busy)

    def _submit(self, operation, receive, action=False, done=None, background=False):
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
                    if action:
                        self.toast(str(error))
                    else:
                        self.connected = False
                        self.status.set_label("连接失败：" + str(error))
                else:
                    try:
                        receive(value)
                    except Exception:
                        self.toast("返回的数据格式无效，请刷新后重试。")
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

    def manage(self, command, receive=None, pending=None, done=None):
        if not self.management_client or self.management_state is None:
            self.toast("请等待 ZFly 本地运行时就绪。")
            return
        if self.busy or self.closed:
            return
        management = self.management_client
        if pending:
            pending.set_sensitive(False)
            pending.set_deletable(False)
        def complete():
            if pending:
                pending.set_sensitive(True)
                pending.set_deletable(True)
            if done:
                done()
        def apply(result):
            self.management_state = result["state"]
            self.settings_panel.receive()
            if receive:
                receive(result)
            self.workspace_panel.receive()
            self.catalog_panel.receive()
            if not command["action"].endswith((".read", ".list")):
                self.toast("已更新演示数据" if self.preview else "操作已完成。")
        self._submit(lambda: management.perform(command), apply, action=True, done=complete)

    def _receive(self, data):
        self.data = data
        self.connected = True
        self.status.set_label(self.controller.endpoint if self.preview else "已连接 · " + self.controller.endpoint)
        connections = data["connections"]
        values = {
            "累计下载": size(connections.get("downloadTotal")),
            "累计上传": size(connections.get("uploadTotal")),
            "当前连接": str(len(connections.get("connections") or [])),
            "核心内存": size(connections.get("memory")),
            "路由模式": data["configs"].get("mode", "—"),
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
        groups = sorted(name for name, proxy in proxies.items() if proxy.get("all") is not None)
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

    def _render_proxies(self):
        if not self.data:
            return
        clear(self.proxy_list)
        selected = self.group_picker.get_selected_item()
        if not selected:
            self.group_note.set_label("暂无代理组，请先添加并选择配置。")
            return
        name = selected.get_string()
        proxies = self.data["proxies"]["proxies"]
        group = proxies.get(name)
        if group is None:
            self.group_note.set_label("代理组已变化，请重新选择。")
            return
        manual = group["type"] == "Selector"
        self.group_note.set_label(group["type"] + (" · 选择节点后立即生效" if manual else " · 由核心自动管理"))
        query = self.search.get_text().casefold()
        names = [proxy for proxy in group.get("all", []) if query in proxy.casefold()]
        for proxy in names[:200]:
            info = proxies.get(proxy, {})
            history = info.get("history") or []
            delay = self.delays.get(proxy, history[-1].get("delay") if history else None)
            delay_text = "未测速" if delay is None else (f"{delay} ms" if delay > 0 else "不可用")
            row = Adw.ActionRow(title=proxy, subtitle=f"{info.get('type', 'Proxy')} · {delay_text}")
            row.set_use_markup(False)
            if group.get("now") == proxy:
                row.add_prefix(Gtk.Image(icon_name="object-select-symbolic"))
            row.add_suffix(action_button("测速", lambda proxy=proxy: self._action("delay", proxy)))
            if manual:
                button = action_button("已选" if group.get("now") == proxy else "选择", lambda proxy=proxy: self._action("select", name, proxy))
                button.set_sensitive(group.get("now") != proxy)
                row.add_suffix(button)
            self.proxy_list.append(row)
        if len(names) > 200:
            self.group_note.set_label(self.group_note.get_label() + " · 显示前 200 项，请搜索筛选")

    def _render_connections(self):
        clear(self.connection_list)
        query = self.search.get_text().casefold()
        items = self.data["connections"].get("connections") or []
        items = [item for item in items if query in (destination(item) + " " + str((item.get("metadata") or {}).get("process", ""))).casefold()]
        self.connection_note.set_label(f"{len(items)} 条连接" + (" · 显示前 200 条，请搜索筛选" if len(items) > 200 else ""))
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
        super().__init__(application_id="io.github.zfly.ZFly")
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
