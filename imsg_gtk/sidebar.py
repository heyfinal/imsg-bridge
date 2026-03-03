import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk


class ChatSidebar(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._on_chat_selected = None
        self._filter_text = ""

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Messages", subtitle=""))
        self.append(header)

        self._search = Gtk.SearchEntry(placeholder_text="Search")
        self._search.set_margin_start(8)
        self._search.set_margin_end(8)
        self._search.set_margin_bottom(4)
        self._search.connect("search-changed", self._on_search_changed)
        self.append(self._search)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._listbox.set_filter_func(self._filter_func)
        self._listbox.connect("row-selected", self._on_row_selected)
        scrolled.set_child(self._listbox)
        self.append(scrolled)

    def set_on_chat_selected(self, callback):
        self._on_chat_selected = callback

    def set_chats(self, chats_list):
        while True:
            row = self._listbox.get_row_at_index(0)
            if row is None:
                break
            self._listbox.remove(row)

        for chat in chats_list:
            row = self._make_row(chat)
            self._listbox.append(row)

    def get_selected_chat_id(self):
        row = self._listbox.get_selected_row()
        if row and hasattr(row, "chat_id"):
            return row.chat_id
        return None

    def _make_row(self, chat):
        row = Gtk.ListBoxRow()
        row.chat_id = chat.get("id")
        row.chat_name = chat.get("name") or chat.get("identifier", "")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        name_label = Gtk.Label(label=row.chat_name, xalign=0, hexpand=True)
        name_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        name_label.add_css_class("chat-row-name")
        top_row.append(name_label)

        time_str = chat.get("last_message_at", "")
        if time_str:
            time_label = Gtk.Label(label=time_str)
            time_label.add_css_class("chat-row-time")
            top_row.append(time_label)

        box.append(top_row)

        preview = chat.get("last_message", "")
        if preview:
            preview_label = Gtk.Label(label=preview, xalign=0)
            preview_label.set_ellipsize(3)
            preview_label.set_max_width_chars(30)
            preview_label.add_css_class("chat-row-preview")
            box.append(preview_label)

        row.set_child(box)
        return row

    def _filter_func(self, row):
        if not self._filter_text:
            return True
        name = getattr(row, "chat_name", "")
        return self._filter_text.lower() in name.lower()

    def _on_search_changed(self, entry):
        self._filter_text = entry.get_text()
        self._listbox.invalidate_filter()

    def _on_row_selected(self, listbox, row):
        if row and self._on_chat_selected and hasattr(row, "chat_id"):
            self._on_chat_selected(row.chat_id)
