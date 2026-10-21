"""Diagnostics, core maintenance and backups."""
import base64
from pathlib import Path

from gi.repository import Adw, Gio, Gtk

from .ui import button, entry, choice, chosen, label, primary_button
from .files import read_file, write_file


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
                if answer == Gtk.ResponseType.ACCEPT and target:
                    cancellable = Gio.Cancellable()
                    self.window.file_cancellables.add(cancellable)
                    def read():
                        return base64.b64encode(read_file(target, 40 * 1024 * 1024, "备份", cancellable)).decode()
                    def restore(data):
                        self.window.manage({**command, "data": data}, self._tool_result, on_error=self._show_result)
                    self.window._submit(read, restore, action=True, on_error=self._show_result, done=lambda: self.window.file_cancellables.discard(cancellable))
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
            if answer == Gtk.ResponseType.ACCEPT and target:
                def write():
                    write_file(target, base64.b64decode(archive["data"], validate=True))
                self.window._submit(write, lambda _value: self.window.toast("备份已保存。"), action=True)
        dialog.connect("response", response)
        dialog.show()
