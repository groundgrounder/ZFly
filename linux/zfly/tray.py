"""StatusNotifierItem and DBusMenu integration without a GTK 3 dependency."""
import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib

from .i18n import tr
from .icons import tray_icon_file

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM = "org.kde.StatusNotifierItem"
MENU = "com.canonical.dbusmenu"

ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
  <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="WindowId" type="u" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionMovieName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewToolTip"/>
  <signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
  <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
    <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
  <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
    <arg type="a(ia{sv})" direction="out"/></method>
  <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
  <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
    <arg type="u" direction="in"/></method>
  <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
  <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
  <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
  <property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
  <property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>"""


def icon_pixmaps(running):
    path = tray_icon_file(running)
    if path is not None:
        try:
            image = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 32, 32, True)
            pixels = image.get_pixels()
            width, height = image.get_width(), image.get_height()
            channels, stride = image.get_n_channels(), image.get_rowstride()
            data = bytearray()
            for y in range(height):
                for x in range(width):
                    offset = y * stride + x * channels
                    data.extend((pixels[offset + 3] if image.get_has_alpha() else 255,
                                 pixels[offset], pixels[offset + 1], pixels[offset + 2]))
            return [(width, height, bytes(data))]
        except GLib.Error:
            pass
    return []


class StatusNotifierTray:
    def __init__(self, application, connection):
        self.application = application
        self.connection = connection
        self.path = application.get_dbus_object_path() + "/StatusNotifierItem"
        self.menu_path = self.path + "/Menu"
        self.available = False
        self.checking = True
        self.closed = False
        self.owner = None
        self.registered = False
        self.host_registered = False
        self.revision = 1
        self.description = tr("正在启动 ZFly…")
        self.entries = {}
        self.icon_name = ""
        self.icon_theme_path = ""
        self.pixmaps = []
        self.icon_state = None
        self.objects = []
        self.signals = []
        self.watch = 0
        try:
            for path, xml in ((self.path, ITEM_XML), (self.menu_path, MENU_XML)):
                interface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
                self.objects.append(connection.register_object(path, interface, self._method, self._property, None))
            self.signals.append(connection.signal_subscribe(
                WATCHER, "org.freedesktop.DBus.Properties", "PropertiesChanged", WATCHER_PATH,
                WATCHER, Gio.DBusSignalFlags.NONE, self._host_changed))
            for signal in ("StatusNotifierHostRegistered", "StatusNotifierHostUnregistered"):
                self.signals.append(connection.signal_subscribe(
                    WATCHER, WATCHER, signal, WATCHER_PATH, None,
                    Gio.DBusSignalFlags.NONE, self._host_signal))
            self.watch = Gio.bus_watch_name_on_connection(
                connection, WATCHER, Gio.BusNameWatcherFlags.NONE, self._appeared, self._vanished)
            self.update()
        except Exception:
            self.close()
            raise

    def _availability(self):
        available = self.registered and self.host_registered and not self.closed
        self.available = available
        self.application.tray_changed()

    def _appeared(self, _connection, _name, owner):
        self.owner = owner
        self.registered = False
        self.host_registered = False
        self.checking = True
        self.connection.call(owner, WATCHER_PATH, WATCHER, "RegisterStatusNotifierItem",
                             GLib.Variant("(s)", (self.path,)), None,
                             Gio.DBusCallFlags.NONE, 1500, None, self._registered, owner)

    def _registered(self, connection, result, owner):
        try:
            connection.call_finish(result)
        except GLib.Error:
            if owner == self.owner and not self.closed:
                self.checking = False
                self._availability()
            return
        if owner != self.owner or self.closed:
            return
        self.registered = True
        connection.call(owner, WATCHER_PATH, "org.freedesktop.DBus.Properties", "Get",
                        GLib.Variant("(ss)", (WATCHER, "IsStatusNotifierHostRegistered")), None,
                        Gio.DBusCallFlags.NONE, 1500, None, self._host_ready, owner)

    def _host_ready(self, connection, result, owner):
        try:
            host = connection.call_finish(result).unpack()[0]
        except GLib.Error:
            host = False
        if owner == self.owner and not self.closed:
            self.checking = False
            self.host_registered = host is True
            self._availability()

    def _vanished(self, *_args):
        self.owner = None
        self.registered = self.host_registered = self.checking = False
        if not self.closed:
            self._availability()

    def _host_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        _, changed, _ = parameters.unpack()
        if "IsStatusNotifierHostRegistered" in changed:
            self.host_registered = changed["IsStatusNotifierHostRegistered"] is True
            self._availability()

    def _host_signal(self, _connection, _sender, _path, _interface, signal, _parameters):
        self.host_registered = signal == "StatusNotifierHostRegistered"
        self._availability()

    def _property(self, _connection, _sender, _path, interface, name):
        if interface == MENU:
            return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                    "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(name)
        strings = {"Category": "ApplicationStatus", "Id": "zfly", "Title": "ZFly", "Status": "Active",
                   "IconName": self.icon_name, "IconThemePath": self.icon_theme_path, "OverlayIconName": "",
                   "AttentionIconName": "", "AttentionMovieName": ""}
        if name in strings:
            return GLib.Variant("s", strings[name])
        if name == "IconPixmap":
            return GLib.Variant("a(iiay)", self.pixmaps)
        if name in ("OverlayIconPixmap", "AttentionIconPixmap"):
            return GLib.Variant("a(iiay)", [])
        if name == "ToolTip":
            return GLib.Variant("(sa(iiay)ss)", (self.icon_name, self.pixmaps, "ZFly", self.description))
        if name == "Menu":
            return GLib.Variant("o", self.menu_path)
        if name == "ItemIsMenu":
            return GLib.Variant("b", False)
        if name == "WindowId":
            return GLib.Variant("u", 0)
        return None

    def _properties(self, identity, names):
        values = self.entries[identity]
        return {key: GLib.Variant("b" if type(value) is bool else "s", value)
                for key, value in values.items() if not names or key in names}

    def _layout(self, identity, depth, names):
        children = []
        if identity == 0 and depth != 0:
            children = [GLib.Variant("(ia{sv}av)", self._layout(child, depth - 1, names))
                        for child in self.entries if child != 0]
        return identity, self._properties(identity, names), children

    def _event(self, identity, event):
        entry = self.entries.get(identity)
        if entry is None:
            return False
        if event == "clicked" and entry.get("enabled", True):
            callback = {2: self.application.present_window, 3: self.application.toggle_proxy,
                        5: self.application.request_exit}.get(identity)
            if callback:
                # Reply before an action starts to shut down the application.
                GLib.idle_add(lambda: (callback(), GLib.SOURCE_REMOVE)[1])
        return True

    def _method(self, _connection, _sender, _path, interface, method, parameters, invocation):
        args = parameters.unpack()
        if interface == ITEM:
            if method in ("Activate", "SecondaryActivate"):
                self.application.present_window()
            invocation.return_value(None)
            return
        try:
            if method == "GetLayout":
                value = GLib.Variant("(u(ia{sv}av))", (self.revision, self._layout(*args)))
            elif method == "GetGroupProperties":
                identities, names = args
                value = GLib.Variant("(a(ia{sv}))", ([(identity, self._properties(identity, names))
                                      for identity in identities or self.entries if identity in self.entries],))
            elif method == "GetProperty":
                identity, name = args
                value = GLib.Variant("(v)", (self._properties(identity, [name])[name],))
            elif method == "Event":
                if not self._event(args[0], args[1]):
                    raise KeyError(args[0])
                value = None
            elif method == "EventGroup":
                value = GLib.Variant("(ai)", ([item[0] for item in args[0] if not self._event(item[0], item[1])],))
            elif method == "AboutToShow":
                value = GLib.Variant("(b)", (False,))
            elif method == "AboutToShowGroup":
                value = GLib.Variant("(aiai)", ([], [identity for identity in args[0] if identity not in self.entries]))
            else:
                invocation.return_dbus_error(MENU + ".Error.NotImplemented", "Unsupported menu method")
                return
            invocation.return_value(value)
        except KeyError:
            invocation.return_dbus_error(MENU + ".Error.InvalidMenuItem", "Unknown menu item or property")

    def update(self):
        if self.closed:
            return
        window = self.application.window
        state = window.management_state if window else None
        running = bool(state and state["running"])
        if self.icon_state != running:
            path = tray_icon_file(running)
            self.icon_name = f"zfly-tray-{'running' if running else 'stopped'}-symbolic"
            self.icon_theme_path = str(path.parent) if path else ""
            self.pixmaps = icon_pixmaps(running)
            self.icon_state = running
            self.connection.emit_signal(None, self.path, ITEM, "NewIcon", None)
        if window and window.closed:
            description = tr("正在还原系统代理并退出…")
        elif not state:
            description = tr("正在启动 ZFly…")
        elif running and window and not window.connected:
            description = tr("核心连接中断")
        else:
            description = tr("代理运行中") if running else tr("代理已停止")
        if self.application.preview:
            description = tr("离线预览 · {description}", description=description)
        entries = {0: {"children-display": "submenu"},
                   1: {"label": description, "enabled": False},
                   2: {"label": tr("打开 ZFly"), "enabled": True},
                   3: {"label": tr("停止代理") if running else tr("启动代理"),
                       "enabled": bool(state and state["currentProfileID"] and window and not window.busy and not window.closed)},
                   4: {"type": "separator"},
                   5: {"label": tr("退出 ZFly"), "enabled": not bool(window and window.closed)}}
        if entries != self.entries:
            self.entries = entries
            self.revision += 1
            self.connection.emit_signal(None, self.menu_path, MENU, "LayoutUpdated", GLib.Variant("(ui)", (self.revision, 0)))
        if description != self.description:
            self.description = description
            self.connection.emit_signal(None, self.path, ITEM, "NewToolTip", None)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.available = self.checking = False
        if self.watch:
            Gio.bus_unwatch_name(self.watch)
            self.watch = 0
        for subscription in self.signals:
            self.connection.signal_unsubscribe(subscription)
        for registration in self.objects:
            self.connection.unregister_object(registration)
        self.signals.clear()
        self.objects.clear()
