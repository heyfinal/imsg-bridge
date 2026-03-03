import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from imsg_gtk.chatview import ChatView
from imsg_gtk.sidebar import ChatSidebar


class ImsgWindow(Adw.ApplicationWindow):
    def __init__(self, application, async_bridge, client):
        super().__init__(application=application, default_width=900, default_height=700)

        self._bridge = async_bridge
        self._client = client
        self._chats = {}
        self._current_chat_id = None

        self._sidebar = ChatSidebar()
        self._sidebar.set_on_chat_selected(self._on_chat_selected)
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

    def _on_chat_selected(self, chat_id):
        self._current_chat_id = chat_id
        chat = self._chats.get(chat_id, {})
        chat_name = chat.get("name") or chat.get("identifier", "")

        async def _fetch():
            messages = await self._client.get_history(chat_id)
            self._bridge.call_in_gtk(self._chatview.set_chat, chat_id, chat_name, messages)

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
            await self._client.send_message(to=identifier, text=text)

        self._bridge.run_coroutine(_do_send())

    def _start_ws(self):
        async def _connect():
            await self._client.connect_ws(self._on_ws_message)

        self._bridge.run_coroutine(_connect())

    def _on_ws_message(self, msg):
        self._bridge.call_in_gtk(self._handle_ws_message, msg)

    def _handle_ws_message(self, msg):
        chat_id = msg.get("chat_id")
        if chat_id == self._current_chat_id:
            self._chatview.append_message(msg)
        self._load_chats()
