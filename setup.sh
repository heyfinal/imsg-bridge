#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.imsg-bridge.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
BRIDGE_DIR="$HOME/.imessage-bridge"
LOG_DIR="$HOME/Library/Logs"
SERVICE_LABEL="com.imsg-bridge"
CURRENT_USER="$(whoami)"

# Detect Python — prefer the project venv, fall back to system
if [[ -x "$SCRIPT_DIR/.venv/bin/python3" ]]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
elif command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
else
    echo "ERROR: Python 3 not found. Install Python 3.12+ or run 'uv sync' first."
    exit 1
fi

# Generate or update auth token in Keychain
TOKEN=$(openssl rand -hex 32)

if security find-generic-password -a "$CURRENT_USER" -s imessage-bridge &>/dev/null; then
    security delete-generic-password -a "$CURRENT_USER" -s imessage-bridge
fi
security add-generic-password -a "$CURRENT_USER" -s imessage-bridge -w "$TOKEN"

echo ""
echo "Auth token stored in Keychain."
echo "Use this token to authenticate with the bridge:"
echo ""
echo "  $TOKEN"
echo ""

# Create directories
mkdir -p "$BRIDGE_DIR"
mkdir -p "$LOG_DIR"

# Generate plist from template
cat > "$PLIST_DEST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${SERVICE_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>imsg_bridge.bridge:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>5100</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/imessage-bridge.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/imessage-bridge.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST

echo "Plist generated at $PLIST_DEST"

# Unload existing service if loaded
if launchctl list | grep -q "$SERVICE_LABEL"; then
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Load service
launchctl load "$PLIST_DEST"
echo "Service loaded."

# Give it a moment to start
sleep 2

# Check if the service is running
if launchctl list | grep -q "$SERVICE_LABEL"; then
    PID=$(launchctl list | awk -v label="$SERVICE_LABEL" '$3 == label {print $1}')
    if [[ "$PID" != "-" && -n "$PID" ]]; then
        echo "Service started successfully (PID $PID)."
    else
        echo "Service loaded but not yet running. Check logs:"
        echo "  tail -f ~/Library/Logs/imessage-bridge.log"
        echo "  tail -f ~/Library/Logs/imessage-bridge.err"
    fi
else
    echo "WARNING: Service does not appear in launchctl list. Check logs:"
    echo "  tail -f ~/Library/Logs/imessage-bridge.err"
    exit 1
fi
