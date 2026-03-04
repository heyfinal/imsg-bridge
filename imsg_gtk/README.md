# imsg-gtk

Native GTK4 iMessage client for Linux.

This app connects to `imsg-bridge` running on a macOS machine and lets you browse chats, view history, and send messages over the bridge's REST + WebSocket API.

## Install

Recommended: generate a deployment package from the Mac with:

```bash
./setup.sh --deploy
```

Then run the generated `install.sh` on the Linux machine.

## Configuration

Config lives at `~/.config/imsg-gtk/config.json`:

```json
{
  "host": "192.168.1.10",
  "port": 5100,
  "token": "<bridge-token>"
}
```

## Run

```bash
imsg-gtk
```
