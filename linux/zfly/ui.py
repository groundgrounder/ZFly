"""Shared, theme-aware GTK layouts and dialog conventions."""

from gi.repository import Adw, Gdk, Gtk


STYLES = """
.zfly-page { padding: 24px; }
.zfly-hero { padding: 22px; border-radius: 18px;
  background: linear-gradient(135deg, alpha(@accent_bg_color, .14), alpha(@accent_bg_color, .04));
  border: 1px solid alpha(@accent_bg_color, .16); }
.zfly-status-icon { padding: 12px; border-radius: 14px;
  color: @accent_color; background: alpha(@accent_bg_color, .12); }
.zfly-status-icon.stopped { color: @window_fg_color; background: alpha(@window_fg_color, .07); }
.zfly-metric { padding: 18px; border-radius: 14px; }
.zfly-metric-value { font-size: 1.65em; font-weight: 700; font-feature-settings: "tnum"; }
.zfly-mode { padding: 4px; border-radius: 12px; background: alpha(@window_fg_color, .06); }
.zfly-mode button { border-radius: 9px; }
.zfly-mode button:checked { background: @accent_bg_color; color: @accent_fg_color; }
.zfly-preview { border-radius: 8px; padding: 4px 8px;
  color: @accent_color; background: alpha(@accent_bg_color, .1); }
.zfly-empty { padding: 28px 16px; border-radius: 14px; background: alpha(@window_fg_color, .025); }
.zfly-editor { border-radius: 12px; }
"""


def install_styles():
    provider = Gtk.CssProvider()
    provider.load_from_data(STYLES.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def label(text="", style=None, **kwargs):
    item = Gtk.Label(label=text, xalign=0, wrap=True, **kwargs)
    if style:
        item.add_css_class(style)
    return item


def page_box(spacing=24):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, vexpand=True)
    box.add_css_class("zfly-page")
    return box


def page(content, scroll=True):
    clamp = Adw.Clamp(child=content, maximum_size=900, tightening_threshold=680, vexpand=True)
    if not scroll:
        return clamp
    return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)


class SectionPage(Gtk.ScrolledWindow):
    """Preferences groups with the same readable width as the other pages."""

    def __init__(self, title):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.title = title
        self.body = page_box()
        self.body.append(label(title, "title-1"))
        self.set_child(page(self.body, scroll=False))

    def add(self, child):
        self.body.append(child)

    def remove(self, child):
        self.body.remove(child)

    def get_title(self):
        return self.title


class EmptyState(Gtk.Box):
    def __init__(self, title, description, icon="folder-symbolic", action=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        self.add_css_class("zfly-empty")
        self.append(Gtk.Image(icon_name=icon, pixel_size=32))
        self.title = label(title, "heading", justify=Gtk.Justification.CENTER)
        self.title.set_xalign(.5)
        self.append(self.title)
        self.description = label(description, "dim-label", justify=Gtk.Justification.CENTER)
        self.description.set_xalign(.5)
        self.append(self.description)
        if action:
            self.action = Gtk.Button(label=action[0], halign=Gtk.Align.CENTER)
            self.action.connect("clicked", lambda _button: action[1]())
            self.append(self.action)

    def update(self, title, description):
        self.title.set_label(title)
        self.description.set_label(description)


def modal_window(parent, title, width=560, height=520):
    dialog = Adw.Window(
        title=title, transient_for=parent, modal=True,
        default_width=max(300, min(width, parent.get_width() - 32)),
        default_height=max(320, min(height, parent.get_height() - 64)),
    )
    content = page_box(16)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=title), show_end_title_buttons=False)
    cancel = Gtk.Button(label="取消")
    cancel.connect("clicked", lambda _button: dialog.close())
    header.pack_start(cancel)
    toolbar.add_top_bar(header)
    toolbar.set_content(Gtk.ScrolledWindow(child=content, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True))
    dialog.set_content(toolbar)
    # set_deletable(False) only hides window-manager controls; close() and
    # keyboard shortcuts must also wait for the mutation to finish.
    dialog.connect("close-request", lambda window: bool(getattr(window, "operation_pending", False)))
    shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
    shortcuts.add_shortcut(Gtk.Shortcut.new(
        Gtk.ShortcutTrigger.parse_string("Escape"),
        Gtk.CallbackAction.new(lambda *_args: (dialog.close(), True)[1]),
    ))
    dialog.add_controller(shortcuts)
    dialog.error_label = label("", "error")
    dialog.error_label.set_visible(False)
    return dialog, content, header


def show_error(dialog, message, field=None):
    dialog.error_label.set_label(message)
    dialog.error_label.set_visible(bool(message))
    if field is not None:
        field.add_css_class("error")
        field.grab_focus()


def primary_button(button):
    button.add_css_class("suggested-action")
    return button
