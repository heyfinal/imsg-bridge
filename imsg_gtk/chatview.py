import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk

from imsg_gtk.bubble import MessageBubble


class ChatView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._on_send = None
        self._chat_id = None
        self._chat_name = None
        self._last_bubble = None
        self._new_messages_pending = False

        self._banner = Adw.Banner.new("Reconnecting to iMessage bridge...")
        self._banner.set_revealed(False)
        self.append(self._banner)

        self._header = Adw.HeaderBar()
        self._title_widget = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._title_widget.set_halign(Gtk.Align.CENTER)

        self._avatar_stack = Gtk.Stack()
        self._avatar_stack.set_size_request(32, 32)
        self._avatar_stack.set_halign(Gtk.Align.CENTER)
        self._avatar_stack.set_valign(Gtk.Align.CENTER)
        self._avatar_stack.add_css_class("chat-avatar")
        self._avatar_stack.add_css_class("header-avatar")

        self._avatar_initials = Gtk.Label(label="?")
        self._avatar_initials.add_css_class("chat-avatar-initials")
        self._avatar_image = Gtk.Image()
        self._avatar_stack.add_named(self._avatar_initials, "fallback")
        self._avatar_stack.add_named(self._avatar_image, "image")
        self._avatar_stack.set_visible_child_name("fallback")

        title_labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._title_label = Gtk.Label(label="Messages", xalign=0.0)
        self._title_label.add_css_class("header-name")
        self._subtitle_label = Gtk.Label(label="", xalign=0.0)
        self._subtitle_label.add_css_class("header-subtitle")
        title_labels.append(self._title_label)
        title_labels.append(self._subtitle_label)

        self._title_widget.append(self._avatar_stack)
        self._title_widget.append(title_labels)
        self._header.set_title_widget(self._title_widget)
        self.append(self._header)

        self._overlay = Gtk.Overlay(vexpand=True, hexpand=True)

        self._scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.add_css_class("message-scroller")

        self._listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._listbox.set_valign(Gtk.Align.END)
        self._listbox.add_css_class("message-list")
        self._scrolled.set_child(self._listbox)
        self._overlay.set_child(self._scrolled)

        self._new_messages_btn = Gtk.Button(label="New messages")
        self._new_messages_btn.set_halign(Gtk.Align.CENTER)
        self._new_messages_btn.set_valign(Gtk.Align.END)
        self._new_messages_btn.set_margin_bottom(12)
        self._new_messages_btn.add_css_class("suggested-action")
        self._new_messages_btn.add_css_class("pill")
        self._new_messages_btn.add_css_class("new-messages-pill")
        self._new_messages_btn.set_visible(False)
        self._new_messages_btn.connect("clicked", self._on_new_messages_clicked)
        self._overlay.add_overlay(self._new_messages_btn)

        adj = self._scrolled.get_vadjustment()
        adj.connect("notify::value", self._on_scroll_value_changed)

        self.append(self._overlay)

        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        compose.add_css_class("compose-bar")

        self._entry = Gtk.Entry(hexpand=True, placeholder_text="iMessage")
        self._entry.add_css_class("compose-entry")
        self._entry.connect("activate", self._on_entry_activate)
        compose.append(self._entry)

        send_btn = Gtk.Button.new_from_icon_name("mail-send-symbolic")
        send_btn.set_tooltip_text("Send")
        send_btn.add_css_class("suggested-action")
        send_btn.add_css_class("circular")
        send_btn.add_css_class("send-button")
        send_btn.connect("clicked", self._on_send_clicked)
        compose.append(send_btn)

        self.append(compose)

    def set_on_send(self, callback):
        self._on_send = callback

    def set_chat_name(self, name):
        self._chat_name = name
        self._title_label.set_label(name or "Messages")

    @staticmethod
    def _initials(name_or_identifier: str) -> str:
        text = (name_or_identifier or "").strip()
        if not text:
            return "?"
        cleaned = text.replace("@", " ").replace(".", " ").replace("_", " ")
        parts = [part for part in cleaned.split() if part]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return text[:2].upper()

    def set_chat_header(self, name: str | None, avatar_bytes: bytes | None = None, subtitle: str | None = None):
        title = name or "Messages"
        self.set_chat_name(title)
        self._subtitle_label.set_label(subtitle or "")
        self._avatar_initials.set_label(self._initials(title))

        if avatar_bytes:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(avatar_bytes))
            except Exception:
                self._avatar_stack.set_visible_child_name("fallback")
            else:
                self._avatar_image.set_from_paintable(texture)
                self._avatar_stack.set_visible_child_name("image")
        else:
            self._avatar_stack.set_visible_child_name("fallback")

    def set_connection_status(self, status):
        if status == "connected":
            self._banner.set_revealed(False)
        elif status == "reconnecting":
            self._banner.set_title("Reconnecting to iMessage bridge...")
            self._banner.set_revealed(True)
        elif status == "connecting":
            self._banner.set_title("Connecting to iMessage bridge...")
            self._banner.set_revealed(True)
        elif status == "disconnected":
            self._banner.set_title("Disconnected from iMessage bridge")
            self._banner.set_revealed(True)

    def set_chat(self, chat_id, chat_name, messages, avatar_bytes=None, subtitle=None):
        self._chat_id = chat_id
        self._last_bubble = None
        self.set_chat_header(chat_name, avatar_bytes=avatar_bytes, subtitle=subtitle)
        self.clear()
        for msg in messages:
            bubble = MessageBubble(
                text=msg.get("text", ""),
                is_from_me=msg.get("is_from_me", False),
                timestamp=msg.get("created_at", ""),
                sender=msg.get("sender"),
                attachments=msg.get("attachments"),
            )
            self._listbox.append(bubble)
        self._new_messages_pending = False
        self._new_messages_btn.set_visible(False)
        self._scroll_to_bottom()

    def append_message(self, msg_dict):
        at_bottom = self._is_at_bottom()
        bubble = MessageBubble(
            text=msg_dict.get("text", ""),
            is_from_me=msg_dict.get("is_from_me", False),
            timestamp=msg_dict.get("created_at", ""),
            sender=msg_dict.get("sender"),
            attachments=msg_dict.get("attachments"),
        )
        self._listbox.append(bubble)
        self._last_bubble = bubble
        if at_bottom:
            self._scroll_to_bottom()
            self._new_messages_pending = False
            self._new_messages_btn.set_visible(False)
        else:
            self._new_messages_pending = True
            self._new_messages_btn.set_visible(True)
        return bubble

    def mark_last_bubble_failed(self):
        if self._last_bubble:
            self._last_bubble.mark_failed()

    def clear(self):
        self._last_bubble = None
        while True:
            row = self._listbox.get_row_at_index(0)
            if row is None:
                break
            self._listbox.remove(row)

    def _scroll_to_bottom(self):
        def _do_scroll():
            adj = self._scrolled.get_vadjustment()
            target = max(adj.get_upper() - adj.get_page_size(), 0)
            adj.set_value(target)
            return False

        GLib.idle_add(_do_scroll)

    def _is_at_bottom(self) -> bool:
        adj = self._scrolled.get_vadjustment()
        return (adj.get_value() + adj.get_page_size()) >= (adj.get_upper() - 24)

    def _on_scroll_value_changed(self, adj, _pspec):
        if self._new_messages_pending and self._is_at_bottom():
            self._new_messages_pending = False
            self._new_messages_btn.set_visible(False)

    def _on_new_messages_clicked(self, button):
        self._new_messages_pending = False
        self._new_messages_btn.set_visible(False)
        self._scroll_to_bottom()

    def _send_text(self):
        text = self._entry.get_text().strip()
        if text and self._on_send:
            self._on_send(text)
            self._entry.set_text("")

    def _on_entry_activate(self, entry):
        self._send_text()

    def _on_send_clicked(self, button):
        self._send_text()
