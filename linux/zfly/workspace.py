import json

from gi.repository import Adw, Gtk

from .management import WORKSPACE_SECTIONS, PROFILE_SECTIONS, READONLY_SECTIONS
from .ui import button, choice, label, primary_button
from .files import write_file


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

    def receive(self, refresh=False):
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
        if refresh and self.window.page == "规则与高级" and key[0] in READONLY_SECTIONS:
            self.load(background=True)

    def resume(self):
        if self.pending_load == self._key() and not self.window.busy and not self.window.refreshing and self.window.page == "规则与高级":
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

    def load(self, background=False):
        key = self._key()
        if self.window.closed or not self.window.management_client or self.window.management_state is None:
            return
        if key in self.loading:
            return
        if self.window.busy or (background and self.window.refreshing):
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
            if self._key() == key and (not background or self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True) != document["text"]):
                if background and not document["writable"]:
                    self._update_readonly(document["text"])
                else:
                    self._select()
        self.loading.add(key)
        self.attempted.add(key)
        self.window.manage(command, receive, done=lambda: self.loading.discard(key),
                           background=background, on_error=self._show_error)

    def _update_readonly(self, text):
        insert = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        bound = self.buffer.get_iter_at_mark(self.buffer.get_selection_bound()).get_offset()
        adjustment = self.editor.get_parent().get_vadjustment()
        position = adjustment.get_value()
        self.loading_buffer = True
        self.buffer.set_text(text)
        count = self.buffer.get_char_count()
        self.buffer.select_range(self.buffer.get_iter_at_offset(min(insert, count)), self.buffer.get_iter_at_offset(min(bound, count)))
        self.loading_buffer = False
        from gi.repository import GLib
        GLib.idle_add(lambda: (adjustment.set_value(position), GLib.SOURCE_REMOVE)[1])

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
        key = self._key()
        def response(chooser, result):
            target = chooser.get_file()
            chooser.destroy()
            if result == Gtk.ResponseType.ACCEPT and target:
                text = self.drafts.get(key, draft)["text"]
                self.window._submit(lambda: write_file(target, text), lambda _value: self.window.toast("文本已导出。"), action=True)
        dialog.connect("response", response)
        dialog.show()
