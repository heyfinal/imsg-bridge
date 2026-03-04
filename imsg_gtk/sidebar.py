import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk


class ChatSidebar(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._on_chat_selected = None
        self._on_compose_requested = None
        self._on_refresh_requested = None
        self._on_clear_chat_requested = None
        self._on_clear_all_requested = None
        self._on_pin_toggled = None
        self._selected_chat_id: int | None = None
        self._filter_text = ""
        self._context_row = None
        self._rows_by_chat_id = {}
        self._chats_by_chat_id = {}
        self._pinned_chat_ids: list[int] = []
        self._pinned_buttons_by_chat_id: dict[int, Gtk.Button] = {}
        self._unread_by_chat_id: dict[int, int] = {}

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Messages", subtitle=""))
        compose_btn = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        compose_btn.set_tooltip_text("New message")
        compose_btn.add_css_class("flat")
        compose_btn.connect("clicked", self._on_compose_clicked)
        header.pack_end(compose_btn)
        self.append(header)

        self._search = Gtk.SearchEntry(placeholder_text="Search")
        self._search.set_margin_start(8)
        self._search.set_margin_end(8)
        self._search.set_margin_bottom(4)
        self._search.connect("search-changed", self._on_search_changed)
        self.append(self._search)

        self._pinned_revealer = Gtk.Revealer()
        self._pinned_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._pinned_revealer.set_reveal_child(False)

        pinned_scrolled = Gtk.ScrolledWindow()
        pinned_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        pinned_scrolled.set_overlay_scrolling(True)
        pinned_scrolled.set_margin_start(8)
        pinned_scrolled.set_margin_end(8)
        pinned_scrolled.set_margin_bottom(6)
        pinned_scrolled.add_css_class("pinned-strip")

        self._pinned_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._pinned_box.set_margin_top(6)
        self._pinned_box.set_margin_bottom(6)
        pinned_scrolled.set_child(self._pinned_box)
        self._pinned_revealer.set_child(pinned_scrolled)
        self.append(self._pinned_revealer)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._listbox.set_filter_func(self._filter_func)
        self._listbox.connect("row-selected", self._on_row_selected)

        right_click = Gtk.GestureClick()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_right_click)
        self._listbox.add_controller(right_click)

        scrolled.set_child(self._listbox)
        self.append(scrolled)

        self._context_popover = self._build_context_popover()

    def set_on_chat_selected(self, callback):
        self._on_chat_selected = callback

    def set_on_compose_requested(self, callback):
        self._on_compose_requested = callback

    def set_on_refresh_requested(self, callback):
        self._on_refresh_requested = callback

    def set_on_clear_chat_requested(self, callback):
        self._on_clear_chat_requested = callback

    def set_on_clear_all_requested(self, callback):
        self._on_clear_all_requested = callback

    def set_on_pin_toggled(self, callback):
        self._on_pin_toggled = callback

    def set_pinned_chat_ids(self, pinned_chat_ids: list[int]):
        self._pinned_chat_ids = [int(x) for x in pinned_chat_ids if x is not None]
        self._render_pinned()

    def set_selected_chat_id(self, chat_id: int | None):
        self._selected_chat_id = int(chat_id) if chat_id is not None else None
        self._render_pinned()

    def set_chat_unread(self, chat_id: int, unread_count: int):
        chat_id = int(chat_id)
        unread = max(int(unread_count), 0)
        if unread:
            self._unread_by_chat_id[chat_id] = unread
        else:
            self._unread_by_chat_id.pop(chat_id, None)

        row = self._rows_by_chat_id.get(chat_id)
        if row is not None and hasattr(row, "unread_badge"):
            row.unread_badge.set_visible(unread > 0)
            row.unread_badge.set_label(str(unread) if unread < 100 else "99+")

        pinned = self._pinned_buttons_by_chat_id.get(chat_id)
        if pinned is not None and hasattr(pinned, "unread_badge"):
            pinned.unread_badge.set_visible(unread > 0)
            pinned.unread_badge.set_label(str(unread) if unread < 100 else "99+")

    def set_chats(self, chats_list):
        self._rows_by_chat_id = {}
        self._chats_by_chat_id = {}
        while True:
            row = self._listbox.get_row_at_index(0)
            if row is None:
                break
            self._listbox.remove(row)

        for chat in chats_list:
            chat_id = chat.get("id")
            if chat_id is not None:
                self._chats_by_chat_id[int(chat_id)] = chat
            row = self._make_row(chat)
            self._listbox.append(row)
        self._render_pinned()

    def get_selected_chat_id(self):
        row = self._listbox.get_selected_row()
        if row and hasattr(row, "chat_id"):
            return row.chat_id
        return None

    def clear_selection(self):
        self._listbox.unselect_all()
        self.set_selected_chat_id(None)

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

    def set_chat_avatar(self, chat_id, image_bytes):
        row = self._rows_by_chat_id.get(chat_id)
        if row is None or not image_bytes:
            return

        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(image_bytes))
        except Exception:
            return

        row.avatar_image.set_from_paintable(texture)
        row.avatar_stack.set_visible_child_name("image")
        pinned = self._pinned_buttons_by_chat_id.get(chat_id)
        if pinned is not None and hasattr(pinned, "avatar_image") and hasattr(pinned, "avatar_stack"):
            pinned.avatar_image.set_from_paintable(texture)
            pinned.avatar_stack.set_visible_child_name("image")

    def set_chat_display_name(self, chat_id, name):
        row = self._rows_by_chat_id.get(chat_id)
        if row is None or not name:
            return
        row.chat_name = name
        if hasattr(row, "name_label"):
            row.name_label.set_label(name)
        if hasattr(row, "avatar_initials_label"):
            row.avatar_initials_label.set_label(self._initials(name))
        pinned = self._pinned_buttons_by_chat_id.get(chat_id)
        if pinned is not None and hasattr(pinned, "name_label"):
            pinned.name_label.set_label(name)
        if pinned is not None and hasattr(pinned, "avatar_initials_label"):
            pinned.avatar_initials_label.set_label(self._initials(name))

    def _make_row(self, chat):
        row = Gtk.ListBoxRow()
        row.chat_id = chat.get("id")
        row.chat_name = chat.get("name") or chat.get("identifier", "")
        row.chat_identifier = chat.get("identifier", "")
        self._rows_by_chat_id[row.chat_id] = row

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row_box.set_margin_top(8)
        row_box.set_margin_bottom(8)
        row_box.set_margin_start(12)
        row_box.set_margin_end(12)
        row.add_css_class("chat-row")

        avatar_stack = Gtk.Stack()
        avatar_stack.set_size_request(36, 36)
        avatar_stack.set_halign(Gtk.Align.CENTER)
        avatar_stack.set_valign(Gtk.Align.CENTER)
        avatar_stack.add_css_class("chat-avatar")

        avatar_initials = Gtk.Label(label=self._initials(row.chat_name))
        avatar_initials.add_css_class("chat-avatar-initials")
        row.avatar_initials_label = avatar_initials
        avatar_image = Gtk.Image()

        avatar_stack.add_named(avatar_initials, "fallback")
        avatar_stack.add_named(avatar_image, "image")
        avatar_stack.set_visible_child_name("fallback")
        row.avatar_stack = avatar_stack
        row.avatar_image = avatar_image
        row_box.append(avatar_stack)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        name_label = Gtk.Label(label=row.chat_name, xalign=0, hexpand=True)
        name_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        name_label.add_css_class("chat-row-name")
        row.name_label = name_label
        top_row.append(name_label)

        unread_badge = Gtk.Label(label="")
        unread_badge.add_css_class("unread-badge")
        unread_badge.set_visible(False)
        row.unread_badge = unread_badge
        top_row.append(unread_badge)

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

        row_box.append(box)
        row.set_child(row_box)

        unread = self._unread_by_chat_id.get(int(row.chat_id)) if row.chat_id is not None else 0
        if unread:
            unread_badge.set_visible(True)
            unread_badge.set_label(str(unread) if unread < 100 else "99+")
        return row

    def _filter_func(self, row):
        if not self._filter_text:
            return True
        needle = self._filter_text.lower().strip()
        name = (getattr(row, "chat_name", "") or "").lower()
        ident = (getattr(row, "chat_identifier", "") or "").lower()
        return needle in name or needle in ident

    def _on_search_changed(self, entry):
        self._filter_text = entry.get_text()
        self._listbox.invalidate_filter()
        self._render_pinned()

    def _on_row_selected(self, listbox, row):
        if row and self._on_chat_selected and hasattr(row, "chat_id"):
            self._on_chat_selected(row.chat_id)

    def _on_compose_clicked(self, button):
        if self._on_compose_requested:
            self._on_compose_requested()

    def _build_context_popover(self):
        popover = Gtk.Popover()
        popover.set_has_arrow(True)
        popover.set_autohide(True)
        popover.set_parent(self._listbox)

        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu.set_margin_top(4)
        menu.set_margin_bottom(4)
        menu.set_margin_start(4)
        menu.set_margin_end(4)

        self._menu_buttons = {}
        actions = [
            ("open", "Open Conversation"),
            ("pin_toggle", "Pin"),
            ("refresh", "Refresh Conversations"),
            ("copy", "Copy Contact"),
            ("clear", "Clear Conversation"),
            ("clear_all", "Clear All Messages"),
        ]
        for action, label in actions:
            button = Gtk.Button(label=label, halign=Gtk.Align.FILL, hexpand=True)
            button.add_css_class("flat")
            button.connect("clicked", self._on_context_action, action)
            menu.append(button)
            self._menu_buttons[action] = button

        popover.set_child(menu)
        return popover

    def _on_right_click(self, gesture, n_press, x, y):
        row = self._listbox.get_row_at_y(int(y))
        self._context_row = row
        if row is not None:
            self._listbox.select_row(row)

        has_row = row is not None
        self._menu_buttons["open"].set_sensitive(has_row)
        self._menu_buttons["pin_toggle"].set_sensitive(has_row)
        self._menu_buttons["copy"].set_sensitive(has_row)
        self._menu_buttons["clear"].set_sensitive(has_row)

        if has_row:
            pinned = row.chat_id in self._pinned_chat_ids
            self._menu_buttons["pin_toggle"].set_label("Unpin" if pinned else "Pin")

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self._context_popover.set_pointing_to(rect)
        self._context_popover.popup()

    def _on_context_action(self, button, action):
        row = self._context_row or self._listbox.get_selected_row()
        if action == "open" and row is not None and self._on_chat_selected:
            self._on_chat_selected(row.chat_id)
        elif action == "pin_toggle" and row is not None and self._on_pin_toggled:
            self._on_pin_toggled(row.chat_id)
        elif action == "refresh" and self._on_refresh_requested:
            self._on_refresh_requested()
        elif action == "copy" and row is not None:
            display = Gdk.Display.get_default()
            if display:
                display.get_clipboard().set(row.chat_identifier or row.chat_name)
        elif action == "clear" and row is not None and self._on_clear_chat_requested:
            self._on_clear_chat_requested(row.chat_id)
        elif action == "clear_all" and self._on_clear_all_requested:
            self._on_clear_all_requested()

        self._context_popover.popdown()

    def _render_pinned(self):
        while True:
            child = self._pinned_box.get_first_child()
            if child is None:
                break
            self._pinned_box.remove(child)
        self._pinned_buttons_by_chat_id = {}

        needle = (self._filter_text or "").lower().strip()
        visible = 0

        for chat_id in self._pinned_chat_ids:
            chat = self._chats_by_chat_id.get(chat_id)
            if not chat:
                continue

            name = chat.get("display_name") or chat.get("name") or chat.get("identifier", "")
            identifier = chat.get("identifier", "")
            haystack = f"{name} {identifier}".lower()
            if needle and needle not in haystack:
                continue

            btn = Gtk.Button()
            btn.set_can_focus(False)
            btn.add_css_class("pinned-button")
            if self._selected_chat_id is not None and chat_id == self._selected_chat_id:
                btn.add_css_class("pinned-selected")
            btn.connect("clicked", self._on_pinned_clicked, chat_id)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            content.set_halign(Gtk.Align.CENTER)

            overlay = Gtk.Overlay()
            overlay.set_size_request(48, 48)

            avatar_stack = Gtk.Stack()
            avatar_stack.set_size_request(48, 48)
            avatar_stack.set_halign(Gtk.Align.CENTER)
            avatar_stack.set_valign(Gtk.Align.CENTER)
            avatar_stack.add_css_class("chat-avatar")
            avatar_stack.add_css_class("pinned-avatar")

            avatar_initials = Gtk.Label(label=self._initials(name))
            avatar_initials.add_css_class("chat-avatar-initials")
            btn.avatar_initials_label = avatar_initials
            avatar_image = Gtk.Image()

            avatar_stack.add_named(avatar_initials, "fallback")
            avatar_stack.add_named(avatar_image, "image")
            avatar_stack.set_visible_child_name("fallback")
            btn.avatar_stack = avatar_stack
            btn.avatar_image = avatar_image

            overlay.set_child(avatar_stack)
            badge = Gtk.Label(label="")
            badge.add_css_class("unread-badge")
            badge.add_css_class("pinned-badge")
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.START)
            badge.set_visible(False)
            overlay.add_overlay(badge)
            btn.unread_badge = badge

            label = Gtk.Label(label=name, xalign=0.5)
            label.set_ellipsize(3)
            label.set_max_width_chars(10)
            label.add_css_class("pinned-label")
            btn.name_label = label

            content.append(overlay)
            content.append(label)
            btn.set_child(content)

            self._pinned_buttons_by_chat_id[chat_id] = btn
            self._pinned_box.append(btn)
            visible += 1

            unread = self._unread_by_chat_id.get(chat_id, 0)
            if unread:
                badge.set_visible(True)
                badge.set_label(str(unread) if unread < 100 else "99+")

        self._pinned_revealer.set_reveal_child(visible > 0)

    def _on_pinned_clicked(self, button, chat_id):
        row = self._rows_by_chat_id.get(chat_id)
        if row is not None:
            self._listbox.select_row(row)
        if self._on_chat_selected:
            self._on_chat_selected(chat_id)
