import base64
import copy
from pathlib import Path

from gi.repository import Adw, Gio, Gtk

from .controller import CONFIG_CHOICES, CONFIG_FLAGS, CONFIG_PORTS, CONFIG_TEXT, ControllerError, validate_config
from .management import validate_network
from .startup import StartupPreferences


def button(label, callback):
    item = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    item.connect("clicked", lambda _button: callback())
    return item


def entry(group, title, text="", password=False):
    row = (Adw.PasswordEntryRow if password else Adw.EntryRow)(title=title, text=str(text))
    group.add(row)
    return row


def switch(group, title, active=False):
    row = Adw.SwitchRow(title=title, active=active)
    group.add(row)
    return row


def choice(group, title, values, current):
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(values))
    if current in values:
        row.set_selected(values.index(current))
    row.values = values
    group.add(row)
    return row


def chosen(row):
    return row.values[row.get_selected()]


def remove_children(box):
    child = box.get_first_child()
    while child:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


class SettingsPanel(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window = window
        self.core_baseline = None
        self.network_baseline = None
        self.core_fields = {}
        self.network_fields = {}
        self.profiles_key = None
        self.expanded_profiles = set()
        self.startup_preferences = StartupPreferences()
        self.startup_state = self.startup_preferences.state()
        self.updating_startup = False
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(180)
        categories = Gtk.DropDown(model=Gtk.StringList.new(["运行时核心", "配置与订阅", "网络接管", "外观", "应用启动"]), margin_start=20, margin_end=20)
        categories.connect("notify::selected", lambda row, _param: self.stack.set_visible_child_name(["core", "profiles", "network", "appearance", "startup"][row.get_selected()]))
        self.categories = categories
        self.append(categories)
        self.append(self.stack)
        self.core = Adw.PreferencesPage(title="运行时核心")
        self.profiles = Adw.PreferencesPage(title="配置与订阅")
        self.network = Adw.PreferencesPage(title="网络接管")
        self.appearance = Adw.PreferencesPage(title="外观")
        self.startup = Adw.PreferencesPage(title="应用启动")
        for name, page in (("core", self.core), ("profiles", self.profiles), ("network", self.network), ("appearance", self.appearance), ("startup", self.startup)):
            self.stack.add_titled(page, name, page.get_title())
        core_actions = Adw.PreferencesGroup(title="运行时核心设置", description="修改由 ZFly 持久保存；失败时恢复原设置。")
        self.core.add(core_actions)
        row = Adw.ActionRow(title="应用当前编辑")
        row.add_suffix(button("重新载入", self.reload_core))
        row.add_suffix(button("应用", self.save_core))
        core_actions.add(row)
        self.core_group = Adw.PreferencesGroup()
        self.core.add(self.core_group)
        self.core_note = Adw.PreferencesGroup(description="请先连接核心。")
        self.core.add(self.core_note)
        self.bridge_note = Adw.PreferencesGroup(title="ZFly 本地运行时", description="应用自动启动自己的服务与核心，无需其他应用。")
        self.profiles.add(self.bridge_note)
        self.profile_actions = Adw.PreferencesGroup()
        self.profiles.add(self.profile_actions)
        actions = Adw.ActionRow(title="配置管理")
        actions.add_suffix(button("添加订阅", lambda: self.profile_properties()))
        actions.add_suffix(button("导入 YAML", self.import_file))
        self.profile_actions.add(actions)
        custom = Adw.ActionRow(title="自定义配置")
        custom.add_suffix(button("创建", self.create_profile))
        self.profile_actions.add(custom)
        self.profile_items = Adw.PreferencesGroup()
        self.profiles.add(self.profile_items)
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
        actions.add_suffix(button("保存", self.save_network))
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
        if self.window.data and (self.core_baseline is None or (
            self.core_baseline != self.window.data["configs"] and not self._dirty(self.core_fields, self.core_baseline)
        )):
            self.reload_core()
        state = self.window.management_state
        available = state is not None
        self.profile_actions.set_sensitive(available)
        self.network_actions.set_sensitive(available)
        self.network_group.set_sensitive(available)
        self.run_button.set_sensitive(available)
        self.bridge_note.set_description(self.window.management_status)
        if not available:
            self.profiles_key = None
            self._render_profiles([])
            self.network_note.set_description("请等待 ZFly 启动完成。")
            return
        self.network_note.set_description(f"核心：{state['coreStatus']} · TUN 授权：{state['tunAuthorization']} · 保存后由 ZFly 应用。")
        self.running_row.set_subtitle("开启" if state["running"] else "关闭")
        self.run_button.set_label("停止核心" if state["running"] else "启动核心")
        if self.network_baseline is None or (
            self.network_baseline != state["network"] and not self._dirty(self.network_fields, self.network_baseline)
        ):
            self.reload_network()
        key = (state["currentProfileID"], repr(state["profiles"]))
        if key != self.profiles_key:
            self._render_profiles(state["profiles"])
            self.profiles_key = key

    def reset(self):
        self.core_baseline = None
        self.network_baseline = None
        self.profiles_key = None
        self.expanded_profiles.clear()
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
        self.core_note.set_description("端口 0 表示关闭；监听端口不能重复。轮询不会覆盖正在编辑的草稿，重新载入会丢弃草稿。")

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
        baseline = copy.deepcopy(self.core_baseline)
        def work():
            controller.configure(draft, baseline)
            return controller.snapshot()
        def receive(data):
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

    def _render_profiles(self, profiles):
        group = self._replace_group(self.profiles, "profile_items")
        for profile in profiles:
            active = self.window.management_state["currentProfileID"] == profile["id"]
            title = ("✓ " if active else "") + profile["label"]
            row = Adw.ExpanderRow(title=title, subtitle={"url": "订阅", "file": "本地 YAML", "custom": "自定义配置"}.get(profile["type"], profile["type"]))
            row.set_use_markup(False)
            row.set_expanded(profile["id"] in self.expanded_profiles)
            def expanded(item, _param, identity=profile["id"]):
                if item.get_expanded():
                    self.expanded_profiles.add(identity)
                else:
                    self.expanded_profiles.discard(identity)
            row.connect("notify::expanded", expanded)
            group.add(row)
            actions = Adw.ActionRow(title="操作")
            use = button("使用中" if active else "使用", lambda p=profile: self.window.manage({"action": "profile.select", "id": p["id"]}))
            use.set_sensitive(not active)
            actions.add_suffix(use)
            actions.add_suffix(button("属性", lambda p=profile: self.profile_properties(p)))
            actions.add_suffix(button("删除", lambda p=profile: self.delete_profile(p)))
            row.add_row(actions)
            if profile["type"] == "url":
                info = Adw.ActionRow(title="订阅", subtitle=f"自动更新：{'每 ' + str(profile['updateHours']) + ' 小时' if profile['autoUpdate'] else '关闭'} · {profile.get('updated') or '尚未更新'}")
                info.set_use_markup(False)
                info.add_suffix(button("更新订阅", lambda p=profile: self.window.manage({"action": "profile.update", "id": p["id"]})))
                row.add_row(info)
            files = Adw.ActionRow(title="配置文件", subtitle="节点与代理组在自定义资源管理，规则在规则与高级管理。" if profile["type"] == "custom" else "本地文件可编辑，订阅只读。")
            if profile["type"] != "custom":
                files.add_suffix(button("查看 / 编辑", lambda p=profile: self.read_profile(p)))
            files.add_suffix(button("导出", lambda p=profile: self.read_profile(p, export=True)))
            row.add_row(files)

    def profile_properties(self, profile=None):
        dialog, content, group, error = self.form_window("配置属性" if profile else "添加订阅")
        label = entry(group, "名称", profile["label"] if profile else "")
        url = key = auto = hours = None
        if profile is None or profile["type"] == "url":
            url = entry(group, "订阅 URL", profile["url"] if profile else "")
            if profile is None:
                key = entry(group, "Age 解密密钥（可选）", password=True)
            auto = switch(group, "自动更新", profile["autoUpdate"] if profile else True)
            hours = entry(group, "更新间隔（小时）", profile["updateHours"] if profile else 24)
        def save():
            interval = hours.get_text() if hours else str(profile["updateHours"])
            if not interval.isdecimal() or not 1 <= int(interval) <= 8760:
                error.set_label("自动更新间隔应为 1–8760 小时。")
                return
            command = {"action": "profile.edit" if profile else "profile.add", "label": label.get_text(), "url": url.get_text().strip() if url else profile["url"], "autoUpdate": auto.get_active() if auto else profile["autoUpdate"], "updateHours": int(interval)}
            if profile:
                command["id"] = profile["id"]
                command["revision"] = profile.get("revision")
            if key:
                command["ageSecretKey"] = key.get_text()
            self.window.manage(command, lambda _result: dialog.close(), pending=dialog)
        content.append(button("保存", save))
        dialog.present()

    def create_profile(self):
        dialog, content, group, error = self.form_window("创建自定义配置")
        label = entry(group, "名称")
        def create():
            name = label.get_text().strip()
            if not name:
                error.set_label("请填写名称。")
                return
            self.window.manage({"action": "profile.create", "label": name}, lambda _result: dialog.close(), pending=dialog)
        content.append(button("创建", create))
        dialog.present()

    def form_window(self, title):
        dialog = Adw.Window(title=title, transient_for=self.window, modal=True, default_width=560, default_height=460)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_start=20, margin_end=20, margin_top=20, margin_bottom=20)
        group = Adw.PreferencesGroup()
        content.append(group)
        error = Gtk.Label(wrap=True, xalign=0)
        error.add_css_class("error")
        content.append(error)
        dialog.set_content(Gtk.ScrolledWindow(child=content, hscrollbar_policy=Gtk.PolicyType.NEVER))
        return dialog, content, group, error

    def delete_profile(self, profile):
        dialog = Adw.MessageDialog(transient_for=self.window, modal=True, heading="删除配置？", body=f"将删除“{profile['label']}”及其本地文件。")
        dialog.add_response("cancel", "取消")
        dialog.add_response("delete", "删除")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self.window.manage({"action": "profile.delete", "id": profile["id"], "revision": profile.get("revision")}) if response == "delete" else None)
        dialog.present()

    def import_file(self):
        chooser = Gtk.FileChooserNative(title="导入 YAML", transient_for=self.window, action=Gtk.FileChooserAction.OPEN, accept_label="导入", cancel_label="取消")
        def response(dialog, result):
            try:
                if result != Gtk.ResponseType.ACCEPT:
                    return
                path = Path(dialog.get_file().get_path())
                if path.stat().st_size > 4 * 1024 * 1024:
                    raise ControllerError("配置文件不能超过 4 MiB。")
                data = path.read_bytes()
                self.window.manage({"action": "profile.import", "label": path.stem, "data": base64.b64encode(data).decode()})
            except (OSError, ControllerError) as error:
                self.window.toast(str(error))
            finally:
                dialog.destroy()
        chooser.connect("response", response)
        chooser.show()

    def read_profile(self, profile, export=False):
        def receive(result):
            file = result["file"]
            data = base64.b64decode(file["data"], validate=True)
            if export:
                self.export_file(file["name"], data)
            else:
                try:
                    self.edit_file(profile, file, data.decode("utf-8"))
                except UnicodeDecodeError:
                    self.window.toast("文件不是 UTF-8 文本，请导出后使用外部编辑器。")
        self.window.manage({"action": "profile.read", "id": profile["id"]}, receive)

    def export_file(self, name, data):
        chooser = Gtk.FileChooserNative(title="导出 YAML", transient_for=self.window, action=Gtk.FileChooserAction.SAVE, accept_label="导出", cancel_label="取消")
        chooser.set_current_name(Path(name).name)
        def response(dialog, result):
            try:
                if result == Gtk.ResponseType.ACCEPT:
                    dialog.get_file().replace_contents(data, None, False, Gio.FileCreateFlags.REPLACE_DESTINATION, None)
            except Exception:
                self.window.toast("导出失败，请检查目标文件权限。")
            finally:
                dialog.destroy()
        chooser.connect("response", response)
        chooser.show()

    def edit_file(self, profile, file, text):
        dialog = Adw.Window(title=profile["label"], transient_for=self.window, modal=True, default_width=760, default_height=620)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=20, margin_end=20, margin_top=20, margin_bottom=20)
        content.append(Gtk.Label(label="保存前由核心校验。订阅只读；加密文件请使用外部编辑器。", wrap=True, xalign=0))
        editor = Gtk.TextView(monospace=True, editable=profile["type"] == "file")
        buffer = editor.get_buffer()
        buffer.set_text(text)
        buffer.set_modified(False)
        def close_request(_dialog):
            if not buffer.get_modified():
                return False
            confirmation = Adw.MessageDialog(transient_for=dialog, modal=True, heading="放弃尚未保存的修改？")
            confirmation.add_response("cancel", "继续编辑")
            confirmation.add_response("discard", "放弃修改")
            confirmation.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            confirmation.set_close_response("cancel")
            def response(_confirmation, result):
                if result == "discard":
                    buffer.set_modified(False)
                    dialog.close()
            confirmation.connect("response", response)
            confirmation.present()
            return True
        dialog.connect("close-request", close_request)
        content.append(Gtk.ScrolledWindow(child=editor, vexpand=True))
        actions = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        actions.append(button("关闭", dialog.close))
        if profile["type"] == "file":
            def saved(_result):
                buffer.set_modified(False)
                dialog.close()
            def save():
                data = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).encode()
                self.window.manage({"action": "profile.write", "id": profile["id"], "revision": file["revision"], "data": base64.b64encode(data).decode()}, saved, pending=dialog)
            actions.append(button("校验并保存", save))
        content.append(actions)
        dialog.set_content(content)
        dialog.present()
