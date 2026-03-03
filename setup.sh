#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.imsg-bridge.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
BRIDGE_DIR="$HOME/.imessage-bridge"
LOG_DIR="$HOME/Library/Logs"
SERVICE_LABEL="com.imsg-bridge"
CURRENT_USER="$(whoami)"
BIND_HOST="0.0.0.0"
BIND_PORT="5100"

# Detect Python — prefer the project venv, fall back to system
if [[ -x "$SCRIPT_DIR/.venv/bin/python3" ]]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
elif command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
else
    echo "ERROR: Python 3 not found. Install Python 3.12+ or run 'uv sync' first."
    exit 1
fi

get_lan_ip() {
    # Get primary LAN IP on macOS
    local ip
    ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
    if [[ -z "$ip" ]]; then
        ip=$(ifconfig | awk '/inet / && !/127.0.0.1/ {print $2; exit}')
    fi
    echo "$ip"
}

get_token() {
    security find-generic-password -a "$CURRENT_USER" -s imessage-bridge -w 2>/dev/null || true
}

generate_token() {
    local token
    token=$(openssl rand -hex 32)
    if security find-generic-password -a "$CURRENT_USER" -s imessage-bridge &>/dev/null; then
        security delete-generic-password -a "$CURRENT_USER" -s imessage-bridge
    fi
    security add-generic-password -a "$CURRENT_USER" -s imessage-bridge -w "$token"
    echo "$token"
}

deploy_package() {
    local dest="$1"
    local token="$2"
    local lan_ip="$3"

    local pkg_dir="$dest/imsg-gtk-install"
    rm -rf "$pkg_dir"
    mkdir -p "$pkg_dir"

    # Copy GTK client source
    cp -r "$SCRIPT_DIR/imsg_gtk" "$pkg_dir/imsg_gtk"
    cp "$SCRIPT_DIR/imsg_gtk/pyproject.toml" "$pkg_dir/pyproject.toml" 2>/dev/null || true

    # If pyproject.toml is inside imsg_gtk/, copy from there
    if [[ -f "$SCRIPT_DIR/imsg_gtk/pyproject.toml" ]]; then
        cp "$SCRIPT_DIR/imsg_gtk/pyproject.toml" "$pkg_dir/pyproject.toml"
    fi

    # Copy install script
    cp "$SCRIPT_DIR/imsg_gtk/install-linux.sh" "$pkg_dir/install.sh"
    chmod +x "$pkg_dir/install.sh"

    # Write pre-filled config
    cat > "$pkg_dir/config.json" <<EOF
{
    "host": "$lan_ip",
    "port": $BIND_PORT,
    "token": "$token"
}
EOF

    echo "$pkg_dir"
}

do_deploy_usb() {
    local usb_path="$1"
    if [[ ! -d "$usb_path" ]]; then
        echo "ERROR: Directory does not exist: $usb_path"
        exit 1
    fi

    local token lan_ip pkg_dir
    token=$(get_token)
    if [[ -z "$token" ]]; then
        echo "No token found. Run setup.sh first (without flags) to generate one."
        exit 1
    fi
    lan_ip=$(get_lan_ip)
    if [[ -z "$lan_ip" ]]; then
        echo "ERROR: Could not detect LAN IP. Are you connected to a network?"
        exit 1
    fi

    pkg_dir=$(deploy_package "$usb_path" "$token" "$lan_ip")

    echo ""
    echo "Deployment package created at: $pkg_dir"
    echo "  Config: host=$lan_ip port=$BIND_PORT"
    echo ""
    echo "On the Linux machine:"
    echo "  cd $pkg_dir"
    echo "  ./install.sh"
    echo ""
}

do_deploy_ssh() {
    local target="$1"  # user@host
    local token lan_ip pkg_dir tmp_dir

    token=$(get_token)
    if [[ -z "$token" ]]; then
        echo "No token found. Run setup.sh first (without flags) to generate one."
        exit 1
    fi
    lan_ip=$(get_lan_ip)
    if [[ -z "$lan_ip" ]]; then
        echo "ERROR: Could not detect LAN IP."
        exit 1
    fi

    tmp_dir=$(mktemp -d)
    pkg_dir=$(deploy_package "$tmp_dir" "$token" "$lan_ip")

    echo "Packaging for SSH deploy..."
    tar -czf "$tmp_dir/imsg-gtk-install.tar.gz" -C "$tmp_dir" imsg-gtk-install

    echo "Copying to $target..."
    scp "$tmp_dir/imsg-gtk-install.tar.gz" "$target:/tmp/imsg-gtk-install.tar.gz"

    echo "Installing on remote host..."
    ssh "$target" "cd /tmp && tar xzf imsg-gtk-install.tar.gz && cd imsg-gtk-install && chmod +x install.sh && ./install.sh"

    rm -rf "$tmp_dir"
    echo ""
    echo "Deployment complete. Run 'imsg-gtk' on the Linux machine to launch."
    echo ""
}

do_setup() {
    # Generate or update auth token
    TOKEN=$(generate_token)

    echo ""
    echo "Auth token stored in Keychain."
    echo "Use this token to authenticate with the bridge:"
    echo ""
    echo "  $TOKEN"
    echo ""

    # Create directories
    mkdir -p "$BRIDGE_DIR"
    mkdir -p "$LOG_DIR"

    # Generate plist
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
        <string>${BIND_HOST}</string>
        <string>--port</string>
        <string>${BIND_PORT}</string>
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

    local lan_ip
    lan_ip=$(get_lan_ip)
    echo ""
    echo "Bridge listening on ${BIND_HOST}:${BIND_PORT}"
    if [[ -n "$lan_ip" ]]; then
        echo "LAN address: http://${lan_ip}:${BIND_PORT}"
    fi
    echo ""
    echo "Deploy to Linux:"
    echo "  ./setup.sh --deploy-usb /Volumes/USB"
    echo "  ./setup.sh --deploy-ssh user@linux-ip"
}

# --- Main ---

case "${1:-}" in
    --deploy-usb)
        if [[ -z "${2:-}" ]]; then
            echo "Usage: ./setup.sh --deploy-usb <path>"
            exit 1
        fi
        do_deploy_usb "$2"
        ;;
    --deploy-ssh)
        if [[ -z "${2:-}" ]]; then
            echo "Usage: ./setup.sh --deploy-ssh user@host"
            exit 1
        fi
        do_deploy_ssh "$2"
        ;;
    "")
        do_setup
        ;;
    *)
        echo "Usage: ./setup.sh [--deploy-usb <path>] [--deploy-ssh user@host]"
        exit 1
        ;;
esac
