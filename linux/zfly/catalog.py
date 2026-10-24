import json

from .i18n import tr, trn
from gi.repository import Adw, Gtk

from .pages import Page
from .catalog_model import CATALOG_KINDS
from .ui import EmptyState, button, choice, clear, label, modal_window, primary_button, show_error


class CatalogPanel(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16, vexpand=True)
        self.add_css_class("zfly-page")
        self.window = window
        self.cache = {}
        self.profile_ids = []
        self.profile_labels = []
        self.updating = False
        self.pending = False
        self.loading = False
        self.attempted = set()
        self.bridge_available = False
        self.load_error = ""
        self.kinds = ["proxies", "groups"]
        heading = Gtk.Box(spacing=12)
        heading.append(label("自定义资源", "title-1", hexpand=True))
        heading.append(primary_button(button("新建", lambda: self.open())))
        self.append(heading)
        fields = Adw.PreferencesGroup(description=tr("节点在自定义配置之间共享，代理组属于所选配置；规则和扩展方式在规则与高级中管理。"))
        self.kind = choice(fields, "分类", [CATALOG_KINDS[k] for k in self.kinds], CATALOG_KINDS["proxies"])
        self.profile = choice(fields, "自定义配置", ["选择配置"], "选择配置")
        self.profile.set_visible(False)
        self.append(fields)
        actions = Gtk.Box(spacing=12)
        actions.append(button("刷新", self.load))
        self.append(actions)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list_scroll = Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.list_scroll)
        self.empty = EmptyState("创建自己的节点", "添加节点后，可在自定义配置中引用它们。", "folder-symbolic", ("新建节点", self._empty_action))
        self.empty.set_vexpand(True)
        self.append(self.empty)
        self.note = Gtk.Label(label=tr("请连接本地运行时。"), xalign=0, wrap=True)
        self.append(self.note)
        self.kind.connect("notify::selected", self.select)
        self.profile.connect("notify::selected", self.select)

    def reset(self):
        self.cache.clear()
        self.load_error = ""
        self.attempted.clear()
        self.bridge_available = False
        self.pending = self.loading = False
        self.profile_ids = []
        self.profile_labels = []
        self.render()

    def key(self):
        kind = self.kinds[self.kind.get_selected()]
        index = self.profile.get_selected()
        return kind, (self.profile_ids[index] if kind == "groups" and index < len(self.profile_ids) else None)

    def available(self):
        kind, identity = self.key()
        return not self.window.closed and self.window.management_client is not None and self.window.management_state is not None and (kind != "groups" or identity is not None)

    def command(self, action, identity=None):
        kind, profile = self.key()
        command = {"action": action, "section": kind}
        if identity is not None:
            command["id"] = identity
        if profile is not None:
            command["name"] = profile
        return command

    def receive(self):
        available = self.window.management_state is not None
        if available and not self.bridge_available:
            self.attempted.clear()
        self.bridge_available = available
        previous = self.key()
        profiles = [p for p in (self.window.management_state or {}).get("profiles", []) if p["type"] == "custom"]
        identities, labels = [p["id"] for p in profiles], [p["label"] for p in profiles]
        if identities != self.profile_ids or labels != self.profile_labels:
            selected = self.key()[1]
            self.profile_ids, self.profile_labels = identities, labels
            self.updating = True
            self.profile.set_model(Gtk.StringList.new(labels or [tr("选择配置")]))
            current = (self.window.management_state or {}).get("currentProfileID")
            selected = selected if selected in identities else current
            self.profile.set_selected(identities.index(selected) if selected in identities else 0)
            self.updating = False
        if self.window.page == Page.CATALOG:
            self.profile.set_visible(self.key()[0] == "groups")
            if self.key() != previous:
                self.render()
            if self.key() not in self.cache and self.key() not in self.attempted and not self.loading:
                self.load()

    def select(self, *_args):
        if self.updating:
            return
        self.load_error = ""
        self.profile.set_visible(self.key()[0] == "groups")
        self.render()
        if self.key() not in self.cache and self.key() not in self.attempted:
            self.load()

    def resume(self):
        if self.pending and not self.window.busy and not self.loading:
            self.pending = False
            self.load()

    def render(self):
        clear(self.list)
        items = self.cache.get(self.key(), [])
        for item in items:
            row = Adw.ActionRow(title=item["label"], subtitle=item["detail"])
            row.set_use_markup(False)
            row.add_suffix(button("编辑", lambda identity=item["id"]: self.open(identity)))
            self.list.append(row)
        self.list_scroll.set_visible(bool(items))
        self.empty.set_visible(not items)
        self.empty.action.set_visible(self.available())
        if self.load_error:
            self.empty.update(tr("无法载入资源"), tr(self.load_error))
            self.empty.action.set_label(tr("重试"))
        elif not self.available():
            self.empty.update(tr("先准备一份自定义配置") if self.key()[0] == "groups" else tr("正在准备资源"), tr("请在配置与订阅中创建自定义配置。") if self.key()[0] == "groups" else tr("等待 ZFly 本地运行时就绪。"))
        else:
            groups = self.key()[0] == "groups"
            self.empty.update(tr("创建自己的代理组") if groups else tr("创建自己的节点"), tr("将节点组织为代理组，按需选择连接方式。") if groups else tr("添加节点后，可在自定义配置中引用它们。"))
            self.empty.action.set_label(tr("新建代理组") if groups else tr("新建节点"))
        self.note.set_label(tr(self.load_error) if self.load_error else trn("{count} 项资源", "{count} 项资源", len(items)) if items else "")
        if self.load_error:
            self.note.add_css_class("error")
        else:
            self.note.remove_css_class("error")

    def _empty_action(self):
        if self.load_error:
            self.load()
        else:
            self.open()

    def load(self):
        if self.window.page != Page.CATALOG or not self.available() or self.loading:
            return
        if self.window.busy:
            self.pending = True
            return
        target = self.key()
        self.load_error = ""
        self.loading = True
        self.attempted.add(target)
        def receive(result):
            self.cache[target] = result["items"]
            if self.key() == target:
                self.render()
        def failed(message):
            if self.key() == target:
                self.load_error = message
                self.render()
        self.window.manage(self.command("catalog.list"), receive, done=lambda: setattr(self, "loading", False), on_error=failed)

    def open(self, identity=None):
        if not self.available():
            self.window.toast(tr("请先连接本地运行时，并选择自定义配置。"))
            return
        command = self.command("catalog.read", identity)
        self.window.manage(command, lambda result: self.editor(command, result["document"]))

    def editor(self, command, document):
        dialog, content, _header = modal_window(self.window, document["title"], 700, 580)
        content.append(label("使用 JSON 编辑完整节点或代理组。保存前由核心校验。", "dim-label"))
        editor = Gtk.TextView(monospace=True, vexpand=True, top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        editor.add_css_class("card")
        buffer = editor.get_buffer()
        buffer.set_text(document["text"])
        buffer.set_modified(False)
        content.append(Gtk.ScrolledWindow(child=editor, vexpand=True, min_content_height=240))
        content.append(dialog.error_label)
        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=8, row_spacing=8)
        def send(action):
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            show_error(dialog, "")
            if action == "catalog.save":
                try:
                    value = json.loads(text)
                    if not isinstance(value, dict):
                        show_error(dialog, tr("资源需要一个 JSON 对象。"))
                        return
                except json.JSONDecodeError as error:
                    show_error(dialog, tr("JSON 第 {line} 行、第 {column} 列：{message}", line=error.lineno, column=error.colno, message=error.msg))
                    return
            request = {**command, "action": action, "text": text, "revision": document["revision"]}
            def saved(_result):
                self.cache.clear()
                self.attempted.clear()
                self.load_error = ""
                buffer.set_modified(False)
                dialog.close()
                self.load()
            self.window.manage(request, saved, pending=dialog, on_error=lambda message: show_error(dialog, message))
        def format_json():
            try:
                text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
                buffer.set_text(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
                show_error(dialog, "")
            except json.JSONDecodeError as error:
                show_error(dialog, tr("JSON 第 {line} 行、第 {column} 列：{message}", line=error.lineno, column=error.colno, message=error.msg))
        def confirm(heading, callback):
            alert = Adw.MessageDialog(transient_for=dialog, modal=True, heading=tr(heading))
            alert.add_response("cancel", tr("取消"))
            alert.add_response("continue", tr("继续"))
            alert.set_response_appearance("continue", Adw.ResponseAppearance.DESTRUCTIVE)
            alert.set_close_response("cancel")
            alert.connect("response", lambda _alert, response: callback() if response == "continue" else None)
            alert.present()
        if command.get("id") is not None:
            delete = button("删除", lambda: confirm("删除此资源？节点或代理组的引用可能失效。", lambda: send("catalog.delete")))
            delete.add_css_class("destructive-action")
            actions.insert(delete, -1)
        actions.insert(button("格式化 JSON", format_json), -1)
        actions.insert(primary_button(button("保存并应用", lambda: send("catalog.save"))), -1)
        def close_request(_dialog):
            if not buffer.get_modified():
                return False
            def discard():
                buffer.set_modified(False)
                dialog.close()
            confirm("丢弃未保存的编辑？", discard)
            return True
        dialog.connect("close-request", close_request)
        content.append(actions)
        dialog.present()
