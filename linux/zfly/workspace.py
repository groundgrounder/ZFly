import base64
import json
import os
from pathlib import Path

from gi.repository import Adw, Gtk

from .management import WORKSPACE_SECTIONS, PROFILE_SECTIONS
from .settings import button, entry, choice, chosen
from .ui import label, primary_button
from zfly_runtime.storage import atomic_write


class WorkspacePanel(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16, vexpand=True)
        self.add_css_class("zfly-page")
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
        self.append(label("规则与高级", "title-1"))
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
        self.editor.add_css_class("zfly-editor")
        self.buffer = self.editor.get_buffer()
        self.buffer.connect("changed", self._changed)
        self.append(Gtk.ScrolledWindow(child=self.editor, vexpand=True, min_content_height=220))
        self.status = Gtk.Label(label="选择分类后载入。", xalign=0, wrap=True)
        self.status.add_css_class("caption")
        self.append(self.status)
        self.error = label("", "error")
        self.error.set_visible(False)
        self.append(self.error)
        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, column_spacing=8, row_spacing=8)
        for title, callback in (("重新载入", self.reload), ("保存并应用", self.save), ("导出文本", self.export), ("格式化 JSON", self.format_json)):
            item = button(title, callback)
            if callback == self.save:
                primary_button(item)
            actions.insert(item, -1)
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
        self.editor.set_editable(False)
        self.error.set_visible(False)
        self.status.set_label("连接已切换，请重新载入。")

    def invalidate_profiles(self):
        """A profiles-only restore leaves unrelated settings drafts intact."""
        self.drafts = {key: draft for key, draft in self.drafts.items() if key[0] not in PROFILE_SECTIONS}
        self.attempted = {key for key in self.attempted if key[0] not in PROFILE_SECTIONS}
        if self.pending_load and self.pending_load[0] in PROFILE_SECTIONS:
            self.pending_load = None
        if self.active_key and self.active_key[0] in PROFILE_SECTIONS:
            self.active_key = None
            self.loading_buffer = True
            self.buffer.set_text("")
            self.loading_buffer = False
            self.editor.set_editable(False)
            self.error.set_visible(False)
            self.status.set_label("配置已恢复，请重新载入。")

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
        self.error.set_visible(False)
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
            except json.JSONDecodeError as error:
                self._show_error(f"JSON 第 {error.lineno} 行、第 {error.colno} 列：{error.msg}")
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
        self.error.set_visible(False)
        self.window.manage(command, receive, on_error=self._show_error)

    def _show_error(self, message):
        self.error.set_label(message)
        self.error.set_visible(True)
        self.editor.grab_focus()

    def format_json(self):
        draft = self.drafts.get(self._key())
        if not draft or draft["document"]["format"] != "json" or not draft["document"]["writable"]:
            self._show_error("请选择可编辑的 JSON 分类。")
            return
        try:
            text = json.dumps(json.loads(draft["text"]), ensure_ascii=False, indent=2)
        except json.JSONDecodeError as error:
            self._show_error(f"JSON 第 {error.lineno} 行、第 {error.colno} 列：{error.msg}")
            return
        self.buffer.set_text(text)
        self.error.set_visible(False)

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
                self.window._submit(lambda: atomic_write(target.get_path(), text), lambda _value: self.window.toast("文本已导出。"), action=True)
        dialog.connect("response", response)
        dialog.show()


class ToolsPanel(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.add_css_class("zfly-page")
        self.window = window
        self.append(label("网络工具", "title-1"))
        group = Adw.PreferencesGroup(title="DNS 诊断", description="查询域名解析，或查看当前生效的路由规则。")
        self.domain = entry(group, "域名", "example.com")
        self.record_type = choice(group, "记录类型", ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "HTTPS"], "A")
        row = Adw.ActionRow(title="查询解析结果")
        row.add_suffix(primary_button(button("查询", lambda: self.inspect("dnsQuery"))))
        group.add(row)
        rules = Adw.ActionRow(title="生效规则", subtitle="查看核心当前使用的规则")
        rules.add_suffix(button("查看", lambda: self.inspect("rules")))
        group.add(rules)
        self.append(group)

        result_group = Adw.PreferencesGroup()
        self.result_expander = Adw.ExpanderRow(title="诊断结果", subtitle="执行查询或操作后，在这里查看结果")
        self.output = Gtk.TextView(monospace=True, editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                   top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        self.result_expander.add_row(Gtk.ScrolledWindow(child=self.output, min_content_height=180, max_content_height=300, hscrollbar_policy=Gtk.PolicyType.NEVER))
        result_group.add(self.result_expander)
        self.append(result_group)

        maintenance = Adw.PreferencesGroup(title="维护", description="按需管理核心、订阅与资源。")
        core = Adw.ExpanderRow(title="核心维护", subtitle="重载配置、管理缓存与活动连接")
        for title, description, action, callback, destructive in (
            ("应用配置", "重新应用当前配置", "应用", lambda: self.tool("apply"), False),
            ("重启核心", "重新启动 ZFly 管理的核心进程", "重启", lambda: self.tool("restart"), False),
            ("回收内存", "请求核心回收闲置内存", "回收", lambda: self.tool("gc"), False),
            ("清空 DNS 缓存", "后续请求将重新解析域名", "清空", lambda: self.inspect("flushDNS"), False),
            ("清空 Fake-IP", "清除核心记录的地址映射", "清空", lambda: self.inspect("flushFakeIP"), False),
            ("关闭全部连接", "应用需要重新建立连接", "关闭", lambda: self.inspect("closeConnections"), True),
        ):
            row = Adw.ActionRow(title=title, subtitle=description)
            item = button(action, callback)
            if destructive:
                item.add_css_class("destructive-action")
            row.add_suffix(item)
            core.add_row(row)
        maintenance.add(core)
        resources = Adw.ExpanderRow(title="订阅与资源", subtitle="更新订阅、Geo 资源或指定提供者")
        for title, tool in (("全部订阅", "updateProfiles"), ("全部资源", "updateResources")):
            row = Adw.ActionRow(title=title)
            row.add_suffix(button("更新", lambda name=tool: self.tool(name)))
            resources.add_row(row)
        self.provider = Adw.EntryRow(title="提供者名称")
        resources.add_row(self.provider)
        row = Adw.ActionRow(title="指定提供者")
        row.add_suffix(button("更新", lambda: self.tool("updateProvider")))
        resources.add_row(row)
        maintenance.add(resources)
        self.append(maintenance)

        backup = Adw.PreferencesGroup(title="备份与恢复", description="备份包含订阅链接和凭据，请保存在可信位置。")
        local = Adw.ActionRow(title="本地备份", subtitle="将配置与应用偏好保存到文件")
        local.add_suffix(button("备份", lambda: self.tool("backupLocal")))
        backup.add(local)
        restore = Adw.ExpanderRow(title="从本地恢复", subtitle="选择备份文件，恢复前会再次确认")
        for title, name in (("仅恢复配置", "restoreLocalProfiles"), ("恢复全部数据", "restoreLocalAll")):
            row = Adw.ActionRow(title=title)
            row.add_suffix(button("选择文件", lambda tool=name: self.tool(tool)))
            restore.add_row(row)
        backup.add(restore)
        dav = Adw.ExpanderRow(title="WebDAV", subtitle="使用已保存的远程备份连接")
        connection = Adw.ActionRow(title="连接设置")
        connection.add_suffix(button("设置", self.open_dav))
        dav.add_row(connection)
        for title, name, action in (("测试连接", "testDAV", "测试"), ("备份到 WebDAV", "backupDAV", "备份"), ("仅恢复配置", "restoreDAVProfiles", "恢复"), ("恢复全部数据", "restoreDAVAll", "恢复")):
            row = Adw.ActionRow(title=title)
            row.add_suffix(button(action, lambda tool=name: self.tool(tool)))
            dav.add_row(row)
        backup.add(dav)
        self.append(backup)

    def open_dav(self):
        self.window.navigate_to(5)
        self.window.workspace_panel.section.set_selected(self.window.workspace_panel.section_names.index("dav"))

    def _show_result(self, text):
        self.output.get_buffer().set_text(text)
        self.result_expander.set_subtitle("最近一次操作的结果")
        self.result_expander.set_expanded(True)

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
        self.window._submit(lambda: controller.inspect(name, domain, record_type), lambda result: self._show_result(result), action=True, on_error=self._show_result)

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
                    self.window.manage(command, self._tool_result, on_error=self._show_result)
            chooser.connect("response", response)
            chooser.show()
        else:
            self.window.manage(command, self._tool_result, on_error=self._show_result)

    def _tool_result(self, result):
        self._show_result(result.get("message", "操作已完成。"))
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
                    atomic_write(target.get_path(), base64.b64decode(archive["data"], validate=True))
                self.window._submit(write, lambda _value: self.window.toast("备份已保存。"), action=True)
        dialog.connect("response", response)
        dialog.show()
