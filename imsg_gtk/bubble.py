import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango


class MessageBubble(Gtk.Box):
    def __init__(self, text, is_from_me, timestamp, sender=None, attachments=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        self._failed = False
        self._text = text or ""
        self._sender = sender or ""

        if is_from_me:
            self.set_halign(Gtk.Align.END)
            self.add_css_class("message-bubble")
            self.add_css_class("message-outgoing")
        else:
            self.set_halign(Gtk.Align.START)
            self.add_css_class("message-bubble")
            self.add_css_class("message-incoming")

        right_click = Gtk.GestureClick()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)
        self._context_popover = self._build_context_popover()

        if not is_from_me and sender:
            sender_label = Gtk.Label(label=sender, xalign=0)
            sender_label.add_css_class("chat-row-name")
            self.append(sender_label)

        if attachments:
            for att in attachments:
                path = att.get("path") or att.get("file_path") or ""
                if path and _is_image_path(path):
                    picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN)
                    picture.set_size_request(200, -1)
                    picture.add_css_class("attachment-image")
                    self.append(picture)
                    threading.Thread(
                        target=_load_image_async, args=(picture, path), daemon=True
                    ).start()

        if text:
            text_label = Gtk.Label(label=text, xalign=0, selectable=True)
            text_label.set_wrap(True)
            text_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            text_label.set_max_width_chars(40)
            text_label.add_css_class("message-text")
            self.append(text_label)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        footer.set_halign(Gtk.Align.END if is_from_me else Gtk.Align.START)

        time_label = Gtk.Label(label=timestamp)
        time_label.add_css_class("message-time")
        footer.append(time_label)

        self._error_icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        self._error_icon.add_css_class("error")
        self._error_icon.set_visible(False)
        footer.append(self._error_icon)

        self.append(footer)

    def mark_failed(self):
        self._failed = True
        self.add_css_class("error-bubble")
        self._error_icon.set_visible(True)

    def mark_sent(self):
        self._failed = False
        self.remove_css_class("error-bubble")
        self._error_icon.set_visible(False)

    def _build_context_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        popover.set_has_arrow(True)
        popover.set_autohide(True)
        popover.set_parent(self)

        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu.set_margin_top(4)
        menu.set_margin_bottom(4)
        menu.set_margin_start(4)
        menu.set_margin_end(4)

        actions = [
            ("copy_text", "Copy Text"),
        ]
        if self._sender:
            actions.append(("copy_sender", "Copy Sender"))

        self._menu_buttons = {}
        for action, label in actions:
            button = Gtk.Button(label=label, halign=Gtk.Align.FILL, hexpand=True)
            button.add_css_class("flat")
            button.connect("clicked", self._on_context_action, action)
            menu.append(button)
            self._menu_buttons[action] = button

        popover.set_child(menu)
        return popover

    def _on_right_click(self, gesture, n_press, x, y):
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self._context_popover.set_pointing_to(rect)
        self._context_popover.popup()

    def _on_context_action(self, button, action):
        display = Gdk.Display.get_default()
        clipboard = display.get_clipboard() if display else None
        if clipboard is None:
            self._context_popover.popdown()
            return

        if action == "copy_text":
            clipboard.set(self._text)
        elif action == "copy_sender":
            clipboard.set(self._sender)

        self._context_popover.popdown()


def _is_image_path(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".tiff", ".heic", ".webp"))


def _load_image_async(picture: Gtk.Picture, path: str):
    try:
        file = Gio.File.new_for_path(path)
        texture = Gdk.Texture.new_from_file(file)
        GLib.idle_add(picture.set_paintable, texture)
    except Exception:
        pass
