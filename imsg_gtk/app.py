import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk

from imsg_gtk import config
from imsg_gtk.api import BridgeClient
from imsg_gtk.asyncbridge import AsyncBridge
from imsg_gtk.window import ImsgWindow


class ImsgApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.imsg.gtk")
        self._bridge = None
        self._client = None

    def do_activate(self):
        cfg = config.load()
        token = cfg.get("token")

        if not token:
            dialog = Adw.MessageDialog(
                heading="Not Configured",
                body="No API token found. Run the install script first to configure the iMessage bridge.",
            )
            dialog.add_response("quit", "Quit")
            dialog.connect("response", lambda d, r: self.quit())
            dialog.present()
            return

        self._bridge = AsyncBridge()
        self._bridge.start()

        self._client = BridgeClient(
            host=cfg.get("host", "127.0.0.1"),
            port=cfg.get("port", 5100),
            token=token,
        )

        self._load_css()

        win = ImsgWindow(
            application=self,
            async_bridge=self._bridge,
            client=self._client,
        )
        win.present()

    def do_shutdown(self):
        if self._bridge:
            self._bridge.stop()
        Adw.Application.do_shutdown(self)

    def _load_css(self):
        css_path = Path(__file__).parent / "style.css"
        if not css_path.exists():
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css_path))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def main():
    app = ImsgApp()
    app.run(sys.argv)
