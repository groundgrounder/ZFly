import copy

from gi.repository import Adw, Gtk

from .controller import ControllerError, validate_config
from zfly_runtime.schema import CONFIG_CHOICES, CONFIG_FLAGS, CONFIG_PORTS, CONFIG_TEXT
from .management import validate_network
from .startup import StartupPreferences
from .ui import (SectionPage, button, entry, switch, choice, chosen,
                 primary_button)


class SettingsPanel(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window = window
        self.core_baseline = None
        self.network_baseline = None
        self.core_fields = {}
        self.network_fields = {}
        self.startup_preferences = StartupPreferences()
        self.startup_state = self.startup_preferences.state()
        self.updating_startup = False
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(180)
        categories = Gtk.DropDown(model=Gtk.StringList.new(["核心设置", "网络接管", "外观", "应用启动"]), margin_start=24, margin_end=24, margin_top=24)
        categories.connect("notify::selected", lambda row, _param: self.stack.set_visible_child_name(["core", "network", "appearance", "startup"][row.get_selected()]))
        self.categories = categories
        self.append(Adw.Clamp(child=categories, maximum_size=900, tightening_threshold=680))
        self.append(self.stack)
        self.core = SectionPage(title="核心设置")
        self.network = SectionPage(title="网络接管")
        self.appearance = SectionPage(title="外观")
        self.startup = SectionPage(title="应用启动")
        for name, page in (("core", self.core), ("network", self.network), ("appearance", self.appearance), ("startup", self.startup)):
            self.stack.add_titled(page, name, page.get_title())
        core_actions = Adw.PreferencesGroup(title="运行时核心设置", description="修改由 ZFly 持久保存；失败时恢复原设置。")
        self.core.add(core_actions)
        row = Adw.ActionRow(title="应用当前编辑")
        row.add_suffix(button("重新载入", self.reload_core))
        row.add_suffix(primary_button(button("应用", self.save_core)))
        core_actions.add(row)
        self.core_group = Adw.PreferencesGroup()
        self.core.add(self.core_group)
        self.core_note = Adw.PreferencesGroup(description="请先连接核心。")
        self.core.add(self.core_note)
        self.network_note = Adw.PreferencesGroup(title="持久化网络设置", description="等待本地运行时就绪。")
        self.network.add(self.network_note)
        self.running_row = Adw.ActionRow(title="核心运行请求")
        self.run_button = button("启动 / 停止", self.toggle_running)
        self.running_row.add_suffix(self.run_button)
        self.network_note.add(self.running_row)
        self.network_actions = Adw.PreferencesGroup()
        self.network.add(self.network_actions)
        actions = Adw.ActionRow(title="保存到 ZFly")
        actions.add_suffix(button("重新载入", self.reload_network))
        actions.add_suffix(primary_button(button("保存", self.save_network)))
        self.network_actions.add(actions)
        self.network_group = Adw.PreferencesGroup(description="系统代理支持 GNOME 桌面；TUN 需要独立核心具有系统权限。系统 DNS 接管尚未实现。")
        self.network.add(self.network_group)
        appearance = Adw.PreferencesGroup(title="GTK / libadwaita 原生外观")
        self.appearance.add(appearance)
        theme = choice(appearance, "主题（本次运行）", ["跟随系统", "浅色", "深色"], "跟随系统")
        theme.connect("notify::selected", lambda row, _param: Adw.StyleManager.get_default().set_color_scheme(
            [Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK][row.get_selected()]))
        login_enabled, start_hidden = self.startup_state
        startup_group = Adw.PreferencesGroup(
            title="登录启动",
            description="启动时隐藏窗口后，可再次从应用启动器打开 ZFly。",
        )
        self.startup.add(startup_group)
        self.login_row = switch(startup_group, "登录时启动 ZFly", login_enabled)
        self.hidden_row = switch(startup_group, "启动时隐藏窗口", start_hidden)
        self.login_row.set_sensitive(not window.preview)
        self.hidden_row.set_sensitive(not window.preview)
        self.login_row.connect("notify::active", self._save_startup)
        self.hidden_row.connect("notify::active", self._save_startup)

    def _save_startup(self, *_args):
        if self.updating_startup:
            return
        enabled, hidden = self.login_row.get_active(), self.hidden_row.get_active()
        try:
            self.startup_preferences.configure(enabled, hidden)
            self.startup_state = (enabled, hidden)
        except Exception as error:
            self.updating_startup = True
            self.login_row.set_active(self.startup_state[0])
            self.hidden_row.set_active(self.startup_state[1])
            self.updating_startup = False
            self.window.toast(str(error))

    def receive(self):
        if self.window.connected and self.window.data and (self.core_baseline is None or (
            self.core_baseline != self.window.data["configs"] and not self._dirty(self.core_fields, self.core_baseline)
        )):
            self.reload_core()
        state = self.window.management_state
        available = state is not None
        self.network_actions.set_sensitive(available)
        self.network_group.set_sensitive(available)
        self.run_button.set_sensitive(available)
        if not available:
            self.network_note.set_description("请等待 ZFly 启动完成。")
            return
        self.network_note.set_description(f"核心：{state['coreStatus']} · TUN 授权：{state['tunAuthorization']} · 保存后由 ZFly 应用。")
        self.running_row.set_subtitle("开启" if state["running"] else "关闭")
        self.run_button.set_label("停止核心" if state["running"] else "启动核心")
        if self.network_baseline is None or (
            self.network_baseline != state["network"] and not self._dirty(self.network_fields, self.network_baseline)
        ):
            self.reload_network()

    def reset(self):
        self.core_baseline = None
        self.network_baseline = None
        self.core_fields.clear()
        self.network_fields.clear()
        self.core.remove(self.core_group)
        self.core_group = Adw.PreferencesGroup()
        self.core.add(self.core_group)
        self.network.remove(self.network_group)
        self.network_group = Adw.PreferencesGroup()
        self.network.add(self.network_group)

    def _replace_group(self, page, attr):
        old = getattr(self, attr)
        page.remove(old)
        group = Adw.PreferencesGroup()
        setattr(self, attr, group)
        page.add(group)
        return group

    def _dirty(self, fields, baseline):
        for key, row in fields.items():
            expected = baseline[key]
            if isinstance(row, Adw.SwitchRow):
                if row.get_active() != expected:
                    return True
            elif isinstance(row, Adw.ComboRow):
                if chosen(row) != expected:
                    return True
            else:
                text = row.get_text()
                if isinstance(expected, list):
                    if [item.strip() for item in text.split(",") if item.strip()] != expected:
                        return True
                elif isinstance(expected, int):
                    if not text.strip().isdecimal() or int(text) != expected:
                        return True
                elif text != str(expected):
                    return True
        return False

    def reload_core(self):
        if not self.window.data:
            return
        self.core_baseline = copy.deepcopy(self.window.data["configs"])
        group = self._replace_group(self.core, "core_group")
        self.core_fields = {}
        titles = {"mode": "路由模式", "log-level": "日志级别", "find-process-mode": "进程匹配", "ipv6": "IPv6", "allow-lan": "允许局域网", "tcp-concurrent": "TCP 并发", "sniffing": "域名嗅探", "mixed-port": "混合端口", "port": "HTTP 端口", "socks-port": "SOCKS 端口", "bind-address": "监听地址", "interface-name": "出口接口（留空自动选择）"}
        for key, value in self.core_baseline.items():
            if value is None:
                continue
            if key in CONFIG_CHOICES:
                row = choice(group, titles[key], CONFIG_CHOICES[key], value)
            elif key in CONFIG_FLAGS:
                row = switch(group, titles[key], value)
            elif key in CONFIG_PORTS + CONFIG_TEXT:
                row = entry(group, titles[key], value)
            else:
                continue
            self.core_fields[key] = row
        self.core_note.set_description("混合端口范围为 1–65535；HTTP / SOCKS 端口 0 表示关闭，监听端口不能重复。轮询不会覆盖正在编辑的草稿，重新载入会丢弃草稿。")

    def save_core(self):
        if self.core_baseline is None or not self.window.connected:
            self.window.toast("请先连接核心。")
            return
        try:
            draft = copy.deepcopy(self.core_baseline)
            for key, row in self.core_fields.items():
                if key in CONFIG_CHOICES:
                    value = chosen(row)
                elif key in CONFIG_FLAGS:
                    value = row.get_active()
                elif key in CONFIG_PORTS:
                    text = row.get_text().strip()
                    if not text.isdecimal():
                        raise ControllerError("端口应为 0–65535 的整数。")
                    value = int(text)
                else:
                    value = row.get_text()
                draft[key] = value
            validate_config(draft)
        except ControllerError as error:
            self.window.toast(str(error))
            return
        controller = self.window.controller
        management = self.window.management_client
        baseline = copy.deepcopy(self.core_baseline)
        def work():
            controller.configure(draft, baseline)
            try:
                return controller.snapshot(), management.snapshot() if management else None
            except ControllerError:
                return None, None
        def receive(result):
            data, state = result
            if data is None:
                self.core_baseline = copy.deepcopy(draft)
                self.window.connected = False
                self.window.toast("设置已保存，最新状态暂时无法读取，请刷新查看。")
                self.window.refresh()
                return
            self.window.set_management_state(state)
            self.window._receive(data)
            self.reload_core()
            self.window.toast("已应用运行时设置。")
        self.window._submit(work, receive, action=True)

    def reload_network(self):
        state = self.window.management_state
        if not state:
            return
        self.network_baseline = copy.deepcopy(state["network"])
        group = self._replace_group(self.network, "network_group")
        group.set_description("设置由 ZFly 保存并应用，失败时还原原配置。系统 DNS 接管尚未实现。")
        fields = self.network_fields = {}
        for key, title in (("tunEnabled", "启用 TUN"), ("systemProxy", "系统代理"), ("bypassPrivateRoute", "绕过私有路由"), ("ipv6", "IPv6"), ("allowLan", "允许局域网"), ("tcpConcurrent", "TCP 并发")):
            fields[key] = switch(group, title, self.network_baseline[key])
        for key, title, values in (("tunStack", "TUN 协议栈", ["gvisor", "system", "mixed"]), ("mode", "路由模式", CONFIG_CHOICES["mode"]), ("logLevel", "日志级别", CONFIG_CHOICES["log-level"])):
            fields[key] = choice(group, title, values, self.network_baseline[key])
        for key, title in (("tunDevice", "TUN 设备名"), ("mixedPort", "混合端口"), ("bypassDomains", "绕过地址（用逗号分隔）")):
            value = self.network_baseline[key]
            fields[key] = entry(group, title, ", ".join(value) if isinstance(value, list) else value)

    def save_network(self):
        if self.network_baseline is None:
            return
        try:
            draft = copy.deepcopy(self.network_baseline)
            for key, row in self.network_fields.items():
                if isinstance(row, Adw.SwitchRow):
                    value = row.get_active()
                elif isinstance(row, Adw.ComboRow):
                    value = chosen(row)
                elif key == "mixedPort":
                    text = row.get_text().strip()
                    if not text.isdecimal():
                        raise ControllerError("混合端口应为整数。")
                    value = int(text)
                elif key == "bypassDomains":
                    value = [item.strip() for item in row.get_text().split(",") if item.strip()]
                else:
                    value = row.get_text()
                draft[key] = value
            validate_network(draft)
        except ControllerError as error:
            self.window.toast(str(error))
            return
        self.window.manage({"action": "network.save", "network": draft, "expected": self.network_baseline}, lambda _result: self.reload_network())

    def toggle_running(self):
        state = self.window.management_state
        if state:
            self.window.manage({"action": "running.set", "running": not state["running"]})
