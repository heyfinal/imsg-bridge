import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from imsg_gtk.bubble import MessageBubble


class ChatView(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._on_send = None
        self._chat_id = None
        self._chat_name = None
        self._last_bubble = None

        self._banner = Adw.Banner.new("Reconnecting to iMessage bridge...")
        self._banner.set_revealed(False)
        self.append(self._banner)

        self._header = Adw.HeaderBar()
        self._title = Adw.WindowTitle(title="Messages", subtitle="")
        self._header.set_title_widget(self._title)
        self.append(self._header)

        self._scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._listbox.set_valign(Gtk.Align.END)
        self._scrolled.set_child(self._listbox)
        self.append(self._scrolled)

        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        compose.add_css_class("compose-bar")

        self._entry = Gtk.Entry(hexpand=True, placeholder_text="iMessage")
        self._entry.add_css_class("compose-entry")
        self._entry.connect("activate", self._on_entry_activate)
        compose.append(self._entry)

        send_btn = Gtk.Button(label="Send")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", self._on_send_clicked)
        compose.append(send_btn)

        self.append(compose)

    def set_on_send(self, callback):
        self._on_send = callback

    def set_chat_name(self, name):
        self._chat_name = name
        self._title.set_title(name or "Messages")

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

    def set_chat(self, chat_id, chat_name, messages):
        self._chat_id = chat_id
        self._last_bubble = None
        self.set_chat_name(chat_name)
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
        self._scroll_to_bottom()

    def append_message(self, msg_dict):
        bubble = MessageBubble(
            text=msg_dict.get("text", ""),
            is_from_me=msg_dict.get("is_from_me", False),
            timestamp=msg_dict.get("created_at", ""),
            sender=msg_dict.get("sender"),
            attachments=msg_dict.get("attachments"),
        )
        self._listbox.append(bubble)
        self._last_bubble = bubble
        self._scroll_to_bottom()
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
            adj.set_value(adj.get_upper())
            return False

        GLib.idle_add(_do_scroll)

    def _send_text(self):
        text = self._entry.get_text().strip()
        if text and self._on_send:
            self._on_send(text)
            self._entry.set_text("")

    def _on_entry_activate(self, entry):
        self._send_text()

    def _on_send_clicked(self, button):
        self._send_text()
