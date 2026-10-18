import base64
import json
import os
from pathlib import Path

from gi.repository import Adw, Gtk

from .management import WORKSPACE_SECTIONS, PROFILE_SECTIONS
from .settings import button, entry, choice, chosen


class WorkspacePanel(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=20, margin_end=20, margin_bottom=20)
        self.window = window
        self.drafts = {}
        self.profile_ids = []
        self.profile_labels = []
        self.active_key = None
        self.loading_buffer = False
        self.updating_profiles = False
        self.loading = set()
        self.attempted = set()
        self.bridge_available = False
        self.pending_load = None
        self.section_names = list(WORKSPACE_SECTIONS)
        fields = Adw.PreferencesGroup()
        self.section = choice(fields, "分类", list(WORKSPACE_SECTIONS.values()), WORKSPACE_SECTIONS["dns"])
        self.profile = choice(fields, "配置", ["选择配置"], "选择配置")
        self.profile.set_visible(False)
        self.append(fields)
        self.note = Gtk.Label(label="设置由 ZFly 独立保存并应用；嵌套配置使用 JSON，追加规则每行一条。", wrap=True, xalign=0)
        self.note.add_css_class("dim-label")
        self.append(self.note)
        self.editor = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.NONE, top_margin=12, bottom_margin=12, left_margin=12, right_margin=12, vexpand=True)
        self.editor.add_css_class("card")
        self.buffer = self.editor.get_buffer()
        self.buffer.connect("changed", self._changed)
        self.append(Gtk.ScrolledWindow(child=self.editor, vexpand=True, min_content_height=220))
        self.status = Gtk.Label(label="选择分类后载入。", xalign=0, wrap=True)
        self.status.add_css_class("caption")
        self.append(self.status)
        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=8, row_spacing=8)
        for label, callback in (("重新载入", self.reload), ("保存并应用", self.save), ("导出文本", self.export)):
            actions.insert(button(label, callback), -1)
        self.append(actions)
        self.section.connect("notify::selected", self._select)
        self.profile.connect("notify::selected", self._select)

    def reset(self):
        self.drafts.clear()
        self.active_key = None
        self.profile_ids = []
        self.profile_labels = []
        self.loading.clear()
        self.attempted.clear()
        self.bridge_available = False
        self.pending_load = None
        self.loading_buffer = True
        self.buffer.set_text("")
        self.loading_buffer = False
        self.status.set_label("连接已切换，请重新载入。")

    def receive(self):
        state = self.window.management_state
        if state is not None and not self.bridge_available:
            self.attempted.clear()
        self.bridge_available = state is not None
        profiles = state["profiles"] if state else []
        identities = [item["id"] for item in profiles]
        labels = [item["label"] for item in profiles]
        if identities != self.profile_ids or labels != self.profile_labels:
            current = self.active_key[1] if self.active_key else None
            self.profile_ids = identities
            self.profile_labels = labels
            self.updating_profiles = True
            self.profile.set_model(Gtk.StringList.new([item["label"] for item in profiles] or ["选择配置"]))
            selected = current if current in identities else (state or {}).get("currentProfileID")
            self.profile.set_selected(identities.index(selected) if selected in identities else 0)
            self.updating_profiles = False
        key = self._key()
        has_scope = key[0] not in PROFILE_SECTIONS or key[1] is not None
        if self.window.page == "规则与高级" and (key != self.active_key or (has_scope and key not in self.drafts and key not in self.loading and key not in self.attempted)):
            self._select()

    def resume(self):
        if self.pending_load == self._key() and not self.window.busy and self.window.page == "规则与高级":
            self.pending_load = None
            self.load()

    def _key(self):
        section = self.section_names[self.section.get_selected()]
        index = self.profile.get_selected()
        identity = self.profile_ids[index] if index < len(self.profile_ids) and section in PROFILE_SECTIONS else None
        return section, identity

    def _select(self, *_args):
        if self.updating_profiles:
            return
        key = self._key()
        self.profile.set_visible(key[0] in PROFILE_SECTIONS)
        self.note.set_label("password 为 null 时保留已有密码；字符串更新密码，空字符串清除密码。备份恢复入口在网络工具。" if key[0] == "dav" else "设置由 ZFly 独立保存并应用；嵌套配置使用 JSON，追加规则每行一条。")
        self.active_key = key
        draft = self.drafts.get(key)
        self.loading_buffer = True
        self.buffer.set_text(draft["text"] if draft else "")
        self.loading_buffer = False
        self.editor.set_editable(bool(draft and draft["document"]["writable"]))
        self.status.set_label("有未保存的更改" if draft and draft["text"] != draft["document"]["text"] else "已载入" if draft else "点击重新载入。")
        if key[0] in PROFILE_SECTIONS and key[1] is None:
            self.status.set_label("请先在配置与订阅中添加配置。")
            return
        if not draft and key not in self.attempted and self.window.management_state and self.window.page == "规则与高级":
            self.load()

    def _changed(self, *_args):
        if not self.loading_buffer and self.active_key in self.drafts:
            draft = self.drafts[self.active_key]
            draft["text"] = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
            self.status.set_label("有未保存的更改" if draft["text"] != draft["document"]["text"] else "已载入")

    def load(self):
        key = self._key()
        if self.window.closed or not self.window.management_client or self.window.management_state is None:
            return
        if key in self.loading:
            return
        if self.window.busy:
            self.pending_load = key
            return
        if key[0] in PROFILE_SECTIONS and key[1] is None:
            self.window.toast("请先选择配置。")
            return
        command = {"action": "workspace.read", "section": key[0]}
        if key[1] is not None:
            command["id"] = key[1]
        def receive(result):
            document = result["document"]
            self.drafts[key] = {"document": document, "text": document["text"]}
            if self._key() == key:
                self._select()
        self.loading.add(key)
        self.attempted.add(key)
        self.window.manage(command, receive, done=lambda: self.loading.discard(key))

    def reload(self):
        draft = self.drafts.get(self._key())
        if draft and draft["text"] != draft["document"]["text"]:
            dialog = Adw.MessageDialog(transient_for=self.window, modal=True, heading="丢弃当前分类的更改？", body="重新载入会覆盖此分类的未保存草稿。")
            dialog.add_response("cancel", "取消")
            dialog.add_response("discard", "丢弃并载入")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda _dialog, response: self.load() if response == "discard" else None)
            dialog.present()
        else:
            self.load()

    def save(self):
        key = self._key()
        draft = self.drafts.get(key)
        if not draft or not draft["document"]["writable"]:
            self.window.toast("请先载入可编辑的分类。")
            return
        text = draft["text"]
        if draft["document"]["format"] == "json":
            try:
                json.loads(text)
            except ValueError:
                self.window.toast("JSON 格式无效，请检查逗号、括号和引号。")
                return
        command = {"action": "workspace.write", "section": key[0], "text": text, "revision": draft["document"]["revision"]}
        if key[1] is not None:
            command["id"] = key[1]
        def receive(result):
            document = result["document"]
            if self.drafts[key]["text"] == text:
                self.drafts[key]["text"] = document["text"]
            self.drafts[key]["document"] = document
            if self._key() == key:
                self._select()
            self.window.refresh()
        self.window.manage(command, receive)

    def export(self):
        draft = self.drafts.get(self._key())
        if not draft:
            return
        dialog = Gtk.FileChooserNative(title="导出文本", transient_for=self.window, action=Gtk.FileChooserAction.SAVE, accept_label="保存", cancel_label="取消")
        dialog.set_current_name(self._key()[0] + (".json" if draft["document"]["format"] == "json" else ".txt"))
        text = draft["text"]
        def response(chooser, result):
            target = chooser.get_file()
            chooser.destroy()
            if result == Gtk.ResponseType.ACCEPT and target and target.get_path():
                self.window._submit(lambda: Path(target.get_path()).write_text(text, encoding="utf-8"), lambda _value: self.window.toast("文本已导出。"), action=True)
        dialog.connect("response", response)
        dialog.show()


class ToolsPanel(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=20, margin_end=20, margin_bottom=20)
        self.window = window
        group = Adw.PreferencesGroup(title="DNS 查询")
        self.domain = entry(group, "域名", "example.com")
        self.record_type = choice(group, "记录类型", ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "HTTPS"], "A")
        row = Adw.ActionRow(title="查询")
        row.add_suffix(button("执行查询", lambda: self.inspect("dnsQuery")))
        group.add(row)
        self.append(group)
        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=8, row_spacing=8)
        for title, name in (("生效规则", "rules"), ("清空 DNS 缓存", "flushDNS"), ("清空 Fake-IP", "flushFakeIP"), ("关闭全部连接", "closeConnections")):
            actions.insert(button(title, lambda tool=name: self.inspect(tool)), -1)
        for title, name in (("应用配置", "apply"), ("重启核心", "restart"), ("回收内存", "gc"), ("更新全部订阅", "updateProfiles"), ("更新全部资源", "updateResources")):
            actions.insert(button(title, lambda tool=name: self.tool(tool)), -1)
        self.append(actions)
        providers = Adw.PreferencesGroup()
        self.provider = entry(providers, "提供者名称")
        row = Adw.ActionRow(title="指定提供者")
        row.add_suffix(button("更新", lambda: self.tool("updateProvider")))
        providers.add(row)
        self.append(providers)
        backup = Adw.PreferencesGroup(title="备份与恢复", description="备份包含订阅链接和凭据，请妥善保管。WebDAV 连接在规则与高级中配置。")
        backup_buttons = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=8, row_spacing=8)
        for title, name in (("本地备份", "backupLocal"), ("测试 WebDAV", "testDAV"), ("备份到 WebDAV", "backupDAV"), ("本地恢复配置", "restoreLocalProfiles"), ("本地恢复全部", "restoreLocalAll"), ("WebDAV 恢复配置", "restoreDAVProfiles"), ("WebDAV 恢复全部", "restoreDAVAll")):
            backup_buttons.insert(button(title, lambda tool=name: self.tool(tool)), -1)
        backup.add(backup_buttons)
        self.append(backup)
        self.output = Gtk.TextView(monospace=True, editable=False, top_margin=12, bottom_margin=12, left_margin=12, right_margin=12, vexpand=True)
        self.output.add_css_class("card")
        self.append(Gtk.ScrolledWindow(child=self.output, min_content_height=180, vexpand=True))

    def inspect(self, name):
        if name == "closeConnections":
            dialog = Adw.MessageDialog(transient_for=self.window, modal=True, heading="关闭全部连接？", body="应用需要重新建立网络连接。")
            dialog.add_response("cancel", "取消")
            dialog.add_response("close", "关闭全部连接")
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda _dialog, response: self._inspect(name) if response == "close" else None)
            dialog.present()
        else:
            self._inspect(name)

    def _inspect(self, name):
        controller = self.window.controller
        if not controller:
            self.window.toast("请先连接核心。")
            return
        domain, record_type = self.domain.get_text(), chosen(self.record_type)
        self.window._submit(lambda: controller.inspect(name, domain, record_type), lambda result: self.output.get_buffer().set_text(result), action=True)

    def tool(self, name):
        if name.startswith("restore"):
            dialog = Adw.MessageDialog(transient_for=self.window, modal=True, heading="恢复会覆盖当前数据", body="将替换配置和 ZFly 偏好，并停止代理。恢复后请检查配置再启动。" if name.endswith("All") else "将替换现有配置和相关资源，保留主程序偏好。")
            dialog.add_response("cancel", "取消")
            dialog.add_response("restore", "继续恢复")
            dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda _dialog, response: self._tool(name) if response == "restore" else None)
            dialog.present()
        else:
            self._tool(name)

    def _tool(self, name):
        command = {"action": "tool.run", "tool": name, "name": self.provider.get_text()}
        if name.startswith("restoreLocal"):
            chooser = Gtk.FileChooserNative(title="选择 ZFly 备份", transient_for=self.window,
                action=Gtk.FileChooserAction.OPEN, accept_label="恢复", cancel_label="取消")
            def response(dialog, answer):
                target = dialog.get_file()
                dialog.destroy()
                if answer == Gtk.ResponseType.ACCEPT and target and target.get_path():
                    try:
                        path = Path(target.get_path())
                        if path.stat().st_size > 16 * 1024 * 1024:
                            raise ValueError("备份超过 16 MiB。")
                        command["data"] = base64.b64encode(path.read_bytes()).decode()
                    except (OSError, ValueError) as error:
                        self.window.toast(str(error))
                        return
                    self.window.manage(command, self._tool_result)
            chooser.connect("response", response)
            chooser.show()
        else:
            self.window.manage(command, self._tool_result)

    def _tool_result(self, result):
        self.output.get_buffer().set_text(result.get("message", "操作已完成。"))
        if "file" not in result:
            return
        archive = result["file"]
        dialog = Gtk.FileChooserNative(title="保存 ZFly 备份", transient_for=self.window,
            action=Gtk.FileChooserAction.SAVE, accept_label="保存", cancel_label="取消")
        dialog.set_current_name(Path(archive["name"]).name)
        def response(chooser, answer):
            target = chooser.get_file()
            chooser.destroy()
            if answer == Gtk.ResponseType.ACCEPT and target and target.get_path():
                def write():
                    from zfly_runtime.storage import atomic_write
                    atomic_write(target.get_path(), base64.b64decode(archive["data"], validate=True))
                self.window._submit(write, lambda _value: self.window.toast("备份已保存。"), action=True)
        dialog.connect("response", response)
        dialog.show()
