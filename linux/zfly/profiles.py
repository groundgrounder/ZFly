"""Profile and subscription management, independent of application settings."""
import base64
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .controller import ControllerError
from .files import read_file, write_file
from .management import validate_profile_command
from .ui import (EmptyState, SectionPage, button, entry, switch,
                 modal_window, primary_button, show_error)


class ProfilesPanel(SectionPage):
    def __init__(self, window):
        super().__init__(title="配置与订阅")
        self.window = window
        self.profiles_key = None
        self.expanded_profiles = set()
        self.bridge_note = Adw.PreferencesGroup(description="添加订阅、导入 YAML，或创建自己的配置。")
        self.add(self.bridge_note)
        self.profile_actions = Adw.PreferencesGroup()
        self.add(self.profile_actions)
        actions = Adw.ActionRow(title="配置管理")
        actions.add_suffix(primary_button(button("添加订阅", lambda: self.profile_properties())))
        actions.add_suffix(button("导入 YAML", self.import_file))
        self.profile_actions.add(actions)
        custom = Adw.ActionRow(title="自定义配置")
        custom.add_suffix(button("创建", self.create_profile))
        self.profile_actions.add(custom)
        self.profile_items = Adw.PreferencesGroup()
        self.add(self.profile_items)
        self.profile_empty = EmptyState("添加第一份配置", "支持 Clash 格式订阅，也可导入本地 YAML。", "document-new-symbolic", ("添加订阅", self.profile_properties))
        self.add(self.profile_empty)

    def reset(self):
        self.profiles_key = None
        self.expanded_profiles.clear()

    def receive(self):
        state = self.window.management_state
        self.profile_actions.set_sensitive(state is not None)
        self.bridge_note.set_description("离线预览 · 所有操作仅修改演示数据" if self.window.preview else
                                         self.window.management_status if "过期" in self.window.management_status else "添加订阅、导入 YAML，或创建自己的配置。" if state else self.window.management_status)
        if state is None:
            self.profiles_key = None
            self._render_profiles([])
            return
        key = (state["currentProfileID"], repr(state["profiles"]), repr(state.get("updateErrors")))
        if key != self.profiles_key:
            self._render_profiles(state["profiles"])
            self.profiles_key = key

    def _render_profiles(self, profiles):
        self.remove(self.profile_items)
        group = self.profile_items = Adw.PreferencesGroup()
        self.add(group)
        self.profile_empty.set_visible(not profiles)
        for profile in profiles:
            active = self.window.management_state["currentProfileID"] == profile["id"]
            title = profile["label"]
            row = Adw.ExpanderRow(title=title, subtitle={"url": "订阅", "file": "本地 YAML", "custom": "自定义配置"}.get(profile["type"], profile["type"]))
            row.set_use_markup(False)
            row.add_prefix(Gtk.Image(icon_name="object-select-symbolic" if active else "document-new-symbolic"))
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
            delete = button("删除", lambda p=profile: self.delete_profile(p))
            delete.add_css_class("flat")
            actions.add_suffix(delete)
            row.add_row(actions)
            if profile["type"] == "url":
                info = Adw.ActionRow(title="订阅", subtitle=f"自动更新：{'每 ' + str(profile['updateHours']) + ' 小时' if profile['autoUpdate'] else '关闭'} · {profile.get('updated') or '尚未更新'}")
                info.set_use_markup(False)
                info.add_suffix(button("更新订阅", lambda p=profile: self.window.manage({"action": "profile.update", "id": p["id"]})))
                row.add_row(info)
                error = self.window.management_state.get("updateErrors", {}).get(profile["id"])
                if error:
                    status = Adw.ActionRow(title="更新失败", subtitle=error)
                    status.set_use_markup(False)
                    row.add_row(status)
                if profile.get("total"):
                    used = (profile.get("upload") or 0) + (profile.get("download") or 0)
                    usage = Adw.ActionRow(title="订阅用量", subtitle=f"{used:,} / {profile['total']:,} B")
                    row.add_row(usage)
                if profile.get("expire"):
                    from datetime import datetime
                    expiry = Adw.ActionRow(title="到期", subtitle=datetime.fromtimestamp(profile["expire"]).isoformat(sep=" ", timespec="minutes"))
                    row.add_row(expiry)
            files = Adw.ActionRow(title="配置文件", subtitle="节点与代理组在自定义资源管理，规则在规则与高级管理。" if profile["type"] == "custom" else "本地文件可编辑，订阅只读。")
            files.add_suffix(button("查看 / 编辑" if profile["type"] == "file" else "查看", lambda p=profile: self.read_profile(p)))
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
        revision = [profile.get("revision") if profile else None]
        def reload():
            def receive(state):
                latest = next((item for item in state["profiles"] if item["id"] == profile["id"]), None)
                if latest:
                    label.set_text(latest["label"])
                    revision[0] = latest.get("revision")
                    if url:
                        url.set_text(latest["url"]); auto.set_active(latest["autoUpdate"]); hours.set_text(str(latest["updateHours"]))
                    show_error(dialog, "")
            self.window._submit(self.window.management_client.snapshot, receive, action=True, on_error=lambda message: show_error(dialog, message))
        if profile:
            content.append(button("重新载入", reload))
        def save():
            show_error(dialog, "")
            interval = hours.get_text() if hours and auto.get_active() else str(profile["updateHours"] if profile else 24)
            if not interval.isdecimal() or not 1 <= int(interval) <= 8760:
                show_error(dialog, "自动更新间隔应为 1–8760 小时。", hours)
                return
            command = {"action": "profile.edit" if profile else "profile.add", "label": label.get_text().strip(), "url": url.get_text().strip() if url else profile["url"], "autoUpdate": auto.get_active() if auto else profile["autoUpdate"], "updateHours": int(interval)}
            if profile:
                command["id"] = profile["id"]
                command["revision"] = revision[0]
            if key:
                command["ageSecretKey"] = key.get_text()
            try:
                validate_profile_command(command, profile)
            except ControllerError as failure:
                show_error(dialog, str(failure), url if url else label)
                return
            self.window.manage(command, lambda _result: dialog.close(), pending=dialog, on_error=lambda message: show_error(dialog, message))
        save_button = primary_button(button("保存", save))
        save_button.set_halign(Gtk.Align.END)
        content.append(save_button)
        dialog.present()

    def create_profile(self):
        dialog, content, group, error = self.form_window("创建自定义配置")
        label = entry(group, "名称")
        def create():
            name = label.get_text().strip()
            if not name:
                show_error(dialog, "请填写名称。", label)
                return
            self.window.manage({"action": "profile.create", "label": name}, lambda _result: dialog.close(), pending=dialog, on_error=lambda message: show_error(dialog, message))
        create_button = primary_button(button("创建", create))
        create_button.set_halign(Gtk.Align.END)
        content.append(create_button)
        dialog.present()

    def form_window(self, title):
        dialog, content, _header = modal_window(self.window, title)
        group = Adw.PreferencesGroup()
        content.append(group)
        error = dialog.error_label
        content.append(error)
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
            target = dialog.get_file() if result == Gtk.ResponseType.ACCEPT else None
            dialog.destroy()
            if result != Gtk.ResponseType.ACCEPT:
                return
            if target is None:
                self.window.toast("请选择要导入的 YAML 文件。")
                return
            cancellable = Gio.Cancellable()
            self.window.file_cancellables.add(cancellable)
            def read():
                data = read_file(target, 4 * 1024 * 1024, "配置文件", cancellable)
                name = target.get_basename() or "导入配置.yaml"
                return {"action": "profile.import", "label": Path(name).stem,
                        "data": base64.b64encode(data).decode()}
            self.window._submit(read, lambda command: self.window.manage(command), action=True, done=lambda: self.window.file_cancellables.discard(cancellable))
            self.window.toast("正在读取文件，可关闭窗口取消。")
        chooser.connect("response", response)
        chooser.show()

    def read_profile(self, profile, export=False):
        def work():
            result = self.window.management_client.perform({"action": "profile.read", "id": profile["id"]})
            file = result["file"]
            data = base64.b64decode(file["data"], validate=True)
            try:
                text = None if export else data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ControllerError("文件不是 UTF-8 文本，请导出后使用外部编辑器。") from None
            return result, file, data, text
        def receive(value):
            result, file, data, text = value
            self.window.set_management_state(result["state"])
            if export:
                self.export_file(file["name"], data)
            else:
                self.edit_file(profile, file, text)
        self.window._submit(work, receive, action=True)

    def export_file(self, name, data):
        chooser = Gtk.FileChooserNative(title="导出 YAML", transient_for=self.window, action=Gtk.FileChooserAction.SAVE, accept_label="导出", cancel_label="取消")
        chooser.set_current_name(Path(name).name)
        def response(dialog, result):
            try:
                if result == Gtk.ResponseType.ACCEPT:
                    target = dialog.get_file()
                    if target:
                        write_file(target, data)
            except Exception:
                self.window.toast("导出失败，请检查目标文件权限。")
            finally:
                dialog.destroy()
        chooser.connect("response", response)
        chooser.show()

    def edit_file(self, profile, file, text):
        dialog, content, _header = modal_window(self.window, profile["label"], 760, 620, scroll=False)
        content.append(Gtk.Label(label="保存前由核心校验。订阅只读；加密文件请使用外部编辑器。", wrap=True, xalign=0))
        editor = Gtk.TextView(monospace=True, editable=profile["type"] == "file")
        buffer = editor.get_buffer()
        loading, closed = [False], [False]
        revision = [file["revision"]]
        def fill(text):
            if len(text) <= 65536:
                buffer.set_text(text); buffer.set_modified(False)
                return
            loading[0] = True
            buffer.set_text("")
            editor.set_sensitive(False)
            offset = [0]
            def chunk():
                if closed[0]:
                    return GLib.SOURCE_REMOVE
                buffer.insert(buffer.get_end_iter(), text[offset[0]:offset[0] + 65536])
                offset[0] += 65536
                buffer.set_modified(False)
                if offset[0] < len(text):
                    return GLib.SOURCE_CONTINUE
                loading[0] = False
                editor.set_sensitive(True)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(chunk)
        fill(text)
        def close_request(_dialog):
            if loading[0] or not buffer.get_modified():
                closed[0] = True
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
        content.append(Gtk.ScrolledWindow(child=editor, vexpand=True, min_content_height=240))
        content.append(dialog.error_label)
        actions = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        actions.append(button("关闭", dialog.close))
        if profile["type"] == "file":
            def saved(_result):
                buffer.set_modified(False)
                dialog.close()
            def save():
                if loading[0]:
                    return
                data = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).encode()
                show_error(dialog, "")
                self.window.manage({"action": "profile.write", "id": profile["id"], "revision": revision[0], "data": base64.b64encode(data).decode()}, saved, pending=dialog, on_error=lambda message: show_error(dialog, message))
            def reload():
                def receive(result):
                    current = result["file"]
                    def decode():
                        return base64.b64decode(current["data"], validate=True).decode("utf-8-sig")
                    def apply(value):
                        fill(value); revision[0] = current["revision"]; show_error(dialog, "")
                    self.window._submit(decode, apply, action=True)
                self.window.manage({"action": "profile.read", "id": profile["id"]}, receive, on_error=lambda message: show_error(dialog, message))
            actions.append(button("重新载入", reload))
            actions.append(primary_button(button("校验并保存", save)))
        content.append(actions)
        dialog.present()
