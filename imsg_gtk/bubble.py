import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Pango


class MessageBubble(Gtk.Box):
    def __init__(self, text, is_from_me, timestamp, sender=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        if is_from_me:
            self.set_halign(Gtk.Align.END)
            self.add_css_class("message-bubble")
            self.add_css_class("message-outgoing")
        else:
            self.set_halign(Gtk.Align.START)
            self.add_css_class("message-bubble")
            self.add_css_class("message-incoming")

        if not is_from_me and sender:
            sender_label = Gtk.Label(label=sender, xalign=0)
            sender_label.add_css_class("chat-row-name")
            self.append(sender_label)

        text_label = Gtk.Label(label=text, xalign=0, selectable=True)
        text_label.set_wrap(True)
        text_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text_label.set_max_width_chars(40)
        text_label.add_css_class("message-text")
        self.append(text_label)

        time_label = Gtk.Label(label=timestamp, xalign=1 if is_from_me else 0)
        time_label.add_css_class("message-time")
        self.append(time_label)
