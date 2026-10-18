from gi.repository import Adw, Gtk

from .catalog_model import CATALOG_KINDS
from .settings import button, choice


class CatalogPanel(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=20, margin_end=20, margin_bottom=20)
        self.window = window
        self.cache = {}
        self.profile_ids = []
        self.profile_labels = []
        self.updating = False
        self.pending = False
        self.loading = False
        self.attempted = set()
        self.bridge_available = False
        self.kinds = ["proxies", "groups"]
        fields = Adw.PreferencesGroup(description="节点在自定义配置之间共享，代理组属于所选配置；规则和扩展方式在规则与高级中管理。")
        self.kind = choice(fields, "分类", [CATALOG_KINDS[k] for k in self.kinds], CATALOG_KINDS["proxies"])
        self.profile = choice(fields, "自定义配置", ["选择配置"], "选择配置")
        self.profile.set_visible(False)
        self.append(fields)
        actions = Gtk.Box(spacing=12)
        actions.append(button("刷新", self.load))
        actions.append(button("新建", lambda: self.open()))
        self.append(actions)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.note = Gtk.Label(label="请连接本地运行时。", xalign=0, wrap=True)
        self.append(self.note)
        self.kind.connect("notify::selected", self.select)
        self.profile.connect("notify::selected", self.select)

    def reset(self):
        self.cache.clear()
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
            self.profile.set_model(Gtk.StringList.new(labels or ["选择配置"]))
            current = (self.window.management_state or {}).get("currentProfileID")
            selected = selected if selected in identities else current
            self.profile.set_selected(identities.index(selected) if selected in identities else 0)
            self.updating = False
        if self.window.page == "自定义资源":
            self.profile.set_visible(self.key()[0] == "groups")
            if self.key() != previous:
                self.render()
            if self.key() not in self.cache and self.key() not in self.attempted and not self.loading:
                self.load()

    def select(self, *_args):
        if self.updating:
            return
        self.profile.set_visible(self.key()[0] == "groups")
        self.render()
        if self.key() not in self.cache and self.key() not in self.attempted:
            self.load()

    def resume(self):
        if self.pending and not self.window.busy and not self.loading:
            self.pending = False
            self.load()

    def render(self):
        child = self.list.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.list.remove(child)
            child = following
        for item in self.cache.get(self.key(), []):
            row = Adw.ActionRow(title=item["label"], subtitle=item["detail"])
            row.set_use_markup(False)
            row.add_suffix(button("编辑", lambda identity=item["id"]: self.open(identity)))
            self.list.append(row)
        self.note.set_label("暂无资源，点击新建添加。" if self.available() and not self.cache.get(self.key()) else "请连接本地运行时，并为代理组选择自定义配置。" if not self.available() else "")

    def load(self):
        if self.window.page != "自定义资源" or not self.available() or self.loading:
            return
        if self.window.busy:
            self.pending = True
            return
        target = self.key()
        self.loading = True
        self.attempted.add(target)
        def receive(result):
            self.cache[target] = result["items"]
            if self.key() == target:
                self.render()
        self.window.manage(self.command("catalog.list"), receive, done=lambda: setattr(self, "loading", False))

    def open(self, identity=None):
        if not self.available():
            self.window.toast("请先连接本地运行时，并选择自定义配置。")
            return
        command = self.command("catalog.read", identity)
        self.window.manage(command, lambda result: self.editor(command, result["document"]))

    def editor(self, command, document):
        dialog = Adw.Window(title=document["title"], transient_for=self.window, modal=True, default_width=700, default_height=580)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=20, margin_end=20, margin_top=20, margin_bottom=20)
        content.append(Gtk.Label(label="使用 JSON 编辑原版资源。节点和代理组保存前交给核心校验。", wrap=True, xalign=0))
        editor = Gtk.TextView(monospace=True, vexpand=True)
        buffer = editor.get_buffer()
        buffer.set_text(document["text"])
        buffer.set_modified(False)
        content.append(Gtk.ScrolledWindow(child=editor, vexpand=True))
        actions = Gtk.Box(spacing=12)
        def send(action):
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            request = {**command, "action": action, "text": text, "revision": document["revision"]}
            def saved(_result):
                self.cache.clear()
                buffer.set_modified(False)
                dialog.close()
                self.load()
            self.window.manage(request, saved, pending=dialog)
        def confirm(heading, callback):
            alert = Adw.MessageDialog(transient_for=dialog, modal=True, heading=heading)
            alert.add_response("cancel", "取消")
            alert.add_response("continue", "继续")
            alert.set_response_appearance("continue", Adw.ResponseAppearance.DESTRUCTIVE)
            alert.set_close_response("cancel")
            alert.connect("response", lambda _alert, response: callback() if response == "continue" else None)
            alert.present()
        if command.get("id") is not None:
            actions.append(button("删除", lambda: confirm("删除此资源？节点或代理组的引用可能失效。", lambda: send("catalog.delete"))))
        actions.append(button("关闭", dialog.close))
        actions.append(button("保存并应用", lambda: send("catalog.save")))
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
        dialog.set_content(content)
        dialog.present()
