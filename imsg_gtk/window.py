import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw

from imsg_gtk.chatview import ChatView
from imsg_gtk import config
from imsg_gtk.sidebar import ChatSidebar


class ImsgWindow(Adw.ApplicationWindow):
    def __init__(self, application, async_bridge, client):
        super().__init__(application=application, default_width=900, default_height=700)

        self._app = application
        self._bridge = async_bridge
        self._client = client
        self._chats = {}
        self._current_chat_id = None
        self._avatars_by_chat_id: dict[int, bytes] = {}

        self._config = config.load()
        self._pinned_chat_ids: list[int] = [
            int(x) for x in (self._config.get("pinned_chat_ids") or []) if x is not None
        ]

        self._sidebar = ChatSidebar()
        self._sidebar.set_on_chat_selected(self._on_chat_selected)
        self._sidebar.set_on_refresh_requested(self._load_chats)
        self._sidebar.set_on_clear_chat_requested(self._clear_conversation)
        self._sidebar.set_on_clear_all_requested(self._confirm_clear_all_messages)
        self._sidebar.set_on_pin_toggled(self._toggle_pin)
        self._sidebar.set_pinned_chat_ids(self._pinned_chat_ids)
        self._sidebar.set_size_request(280, -1)

        self._chatview = ChatView()
        self._chatview.set_on_send(self._on_send)

        sidebar_page = Adw.NavigationPage(title="Messages", child=self._sidebar)
        content_page = Adw.NavigationPage(title="Conversation", child=self._chatview)

        split_view = Adw.NavigationSplitView()
        split_view.set_sidebar(sidebar_page)
        split_view.set_content(content_page)

        self.set_content(split_view)
        self.connect("map", self._on_map)

    def _on_map(self, widget):
        self._load_chats()
        self._start_ws()

    def _load_chats(self):
        async def _fetch():
            chats = await self._client.get_chats()
            self._bridge.call_in_gtk(self._populate_chats, chats)

        self._bridge.run_coroutine(_fetch())

    def _populate_chats(self, chats):
        self._chats = {}
        for chat in chats:
            self._chats[chat["id"]] = chat
        self._sidebar.set_chats(chats)
        self._sidebar.set_pinned_chat_ids(self._pinned_chat_ids)
        for chat in chats:
            self._load_avatar(chat)
            self._load_contact_name(chat)

    def _on_chat_selected(self, chat_id):
        self._current_chat_id = chat_id
        chat = self._chats.get(chat_id, {})
        chat_name = chat.get("display_name") or chat.get("name") or chat.get("identifier", "")
        avatar = self._avatars_by_chat_id.get(int(chat_id)) if chat_id is not None else None

        async def _fetch():
            messages = await self._client.get_history(chat_id)
            self._bridge.call_in_gtk(self._chatview.set_chat, chat_id, chat_name, messages, avatar)

        self._bridge.run_coroutine(_fetch())

    def _on_send(self, text):
        if self._current_chat_id is None:
            return
        chat = self._chats.get(self._current_chat_id, {})
        identifier = chat.get("identifier", "")

        self._chatview.append_message({
            "text": text,
            "is_from_me": True,
            "created_at": "",
            "sender": None,
        })

        async def _do_send():
            try:
                await self._client.send_message(to=identifier, text=text)
            except Exception:
                self._bridge.call_in_gtk(self._chatview.mark_last_bubble_failed)

        self._bridge.run_coroutine(_do_send())

    def _start_ws(self):
        async def _connect():
            await self._client.connect_ws(self._on_ws_message, self._on_ws_status)

        self._bridge.run_coroutine(_connect())

    def _on_ws_message(self, msg):
        self._bridge.call_in_gtk(self._handle_ws_message, msg)

    def _on_ws_status(self, ws_status):
        self._bridge.call_in_gtk(self._chatview.set_connection_status, ws_status)

    def _handle_ws_message(self, msg):
        chat_id = msg.get("chat_id")
        if chat_id == self._current_chat_id:
            self._chatview.append_message(msg)

        if not msg.get("is_from_me", False):
            sender = msg.get("sender") or ""
            chat = self._chats.get(chat_id, {})
            display = chat.get("display_name") or chat.get("name") or sender
            if hasattr(self._app, "send_notification_message"):
                self._app.send_notification_message(display, msg.get("text", ""))

        self._load_chats()

    def _clear_conversation(self, chat_id):
        if chat_id is None:
            return

        self._chats.pop(chat_id, None)
        self._sidebar.set_chats(list(self._chats.values()))

        if self._current_chat_id == chat_id:
            self._current_chat_id = None
            self._chatview.set_chat(None, "Messages", [])

    def _confirm_clear_all_messages(self):
        dialog = Adw.MessageDialog(
            heading="Clear All Messages?",
            body=(
                "This clears the conversation list and open chat view in this Linux app only. "
                "It does not delete messages from iMessage on the Mac."
            ),
        )
        dialog.set_transient_for(self)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear All")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_clear_all_response)
        dialog.present()

    def _on_clear_all_response(self, dialog, response):
        if response == "clear":
            self._chats = {}
            self._current_chat_id = None
            self._sidebar.set_chats([])
            self._chatview.set_chat(None, "Messages", [])
        dialog.close()

    def _load_avatar(self, chat):
        identifier = (chat.get("identifier") or "").strip()
        chat_id = chat.get("id")
        if not identifier or chat_id is None:
            return

        async def _fetch():
            try:
                avatar = await self._client.get_avatar(identifier)
            except Exception:
                return
            if avatar:
                self._bridge.call_in_gtk(self._apply_avatar, chat_id, avatar)

        self._bridge.run_coroutine(_fetch())

    def _apply_avatar(self, chat_id, avatar):
        if chat_id is not None and avatar:
            self._avatars_by_chat_id[int(chat_id)] = avatar
        self._sidebar.set_chat_avatar(chat_id, avatar)
        if chat_id == self._current_chat_id:
            chat = self._chats.get(chat_id, {})
            chat_name = chat.get("display_name") or chat.get("name") or chat.get("identifier", "")
            self._chatview.set_chat_header(chat_name, avatar_bytes=avatar)

    def _load_contact_name(self, chat):
        identifier = (chat.get("identifier") or "").strip()
        chat_id = chat.get("id")
        if not identifier or chat_id is None:
            return
        if chat.get("display_name"):
            return

        async def _fetch():
            try:
                name = await self._client.get_contact_name(identifier)
            except Exception:
                return
            if name:
                self._bridge.call_in_gtk(self._apply_contact_name, chat_id, name)

        self._bridge.run_coroutine(_fetch())

    def _apply_contact_name(self, chat_id, name):
        chat = self._chats.get(chat_id)
        if chat:
            chat["display_name"] = name
            self._sidebar.set_chat_display_name(chat_id, name)

    def _toggle_pin(self, chat_id):
        if chat_id is None:
            return
        chat_id = int(chat_id)
        if chat_id in self._pinned_chat_ids:
            self._pinned_chat_ids = [x for x in self._pinned_chat_ids if x != chat_id]
        else:
            self._pinned_chat_ids = [chat_id] + [x for x in self._pinned_chat_ids if x != chat_id]

        self._config["pinned_chat_ids"] = list(self._pinned_chat_ids)
        config.save(self._config)
        self._sidebar.set_pinned_chat_ids(self._pinned_chat_ids)
