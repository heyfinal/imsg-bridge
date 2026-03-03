#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.imsg-bridge.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
BRIDGE_DIR="$HOME/.imessage-bridge"
LOG_DIR="$HOME/Library/Logs"
SERVICE_LABEL="com.imsg-bridge"
CURRENT_USER="$(whoami)"
BIND_HOST="${IMSG_BIND_HOST:-0.0.0.0}"
BIND_PORT="${IMSG_BIND_PORT:-5100}"

BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RESET="\033[0m"

resolve_python() {
    if [[ -x "$SCRIPT_DIR/.venv/bin/python3" ]]; then
        PYTHON="$SCRIPT_DIR/.venv/bin/python3"
    elif command -v python3 &>/dev/null; then
        PYTHON="$(command -v python3)"
    else
        echo "ERROR: Python 3 not found. Install Python 3.12+ or run 'uv sync' first."
        exit 1
    fi
}

resolve_python

GUI_DOMAIN="gui/$(id -u)"

get_lan_ip() {
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
        security delete-generic-password -a "$CURRENT_USER" -s imessage-bridge &>/dev/null
    fi
    security add-generic-password -a "$CURRENT_USER" -s imessage-bridge -w "$token" &>/dev/null
    echo "$token"
}

deploy_package() {
    local dest="$1"
    local token="$2"
    local lan_ip="$3"

    local pkg_dir="$dest/imsg-gtk-install"
    rm -rf "$pkg_dir"
    mkdir -p "$pkg_dir"

    cp -r "$SCRIPT_DIR/imsg_gtk" "$pkg_dir/imsg_gtk"
    if [[ -f "$SCRIPT_DIR/imsg_gtk/pyproject.toml" ]]; then
        cp "$SCRIPT_DIR/imsg_gtk/pyproject.toml" "$pkg_dir/pyproject.toml"
    fi

    cp "$SCRIPT_DIR/imsg_gtk/install-linux.sh" "$pkg_dir/install.sh"
    chmod +x "$pkg_dir/install.sh"

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
    echo -e "${GREEN}Deployment package created at:${RESET} $pkg_dir"
    echo -e "  Config: host=${CYAN}$lan_ip${RESET} port=${CYAN}$BIND_PORT${RESET}"
    echo ""
    echo "On the Linux machine:"
    echo "  cd $pkg_dir && ./install.sh"
    echo ""
}

do_deploy_ssh() {
    local target="$1"
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

    echo -e "Copying to ${CYAN}$target${RESET}..."
    scp "$tmp_dir/imsg-gtk-install.tar.gz" "$target:/tmp/imsg-gtk-install.tar.gz"

    echo "Installing on remote host..."
    ssh "$target" "cd /tmp && tar xzf imsg-gtk-install.tar.gz && cd imsg-gtk-install && chmod +x install.sh && ./install.sh"

    rm -rf "$tmp_dir"
    echo ""
    echo -e "${GREEN}Deployment complete.${RESET} Run 'imsg-gtk' on the Linux machine to launch."
    echo ""
}

do_setup() {
    TOKEN=$(generate_token)

    echo ""
    echo -e "${GREEN}Auth token stored in Keychain.${RESET}"
    echo "Use this token to authenticate with the bridge:"
    echo ""
    echo -e "  ${BOLD}$TOKEN${RESET}"
    echo ""

    # Ask about LAN binding if not set via env
    if [[ -z "${IMSG_BIND_HOST:-}" ]]; then
        echo -e "${BOLD}Network binding:${RESET}"
        echo ""
        echo -e "  ${BOLD}1)${RESET} LAN accessible  ${DIM}(0.0.0.0 — required for Linux client)${RESET} (Recommended)"
        echo -e "  ${BOLD}2)${RESET} Localhost only   ${DIM}(127.0.0.1 — local access only)${RESET}"
        echo ""
        read -rp "Choice [1/2] (default: 1): " bind_choice
        case "$bind_choice" in
            2)
                BIND_HOST="127.0.0.1"
                echo -e "Binding to ${CYAN}127.0.0.1:${BIND_PORT}${RESET} (localhost only)"
                ;;
            *)
                BIND_HOST="0.0.0.0"
                echo -e "Binding to ${CYAN}0.0.0.0:${BIND_PORT}${RESET} (LAN accessible)"
                ;;
        esac
        echo ""
    fi

    mkdir -p "$BRIDGE_DIR"
    mkdir -p "$LOG_DIR"

    # Ensure dependencies are installed
    if [[ ! -x "$SCRIPT_DIR/.venv/bin/python3" ]]; then
        echo "Creating venv and installing dependencies..."
        if command -v uv &>/dev/null; then
            (cd "$SCRIPT_DIR" && uv sync)
        else
            python3 -m venv "$SCRIPT_DIR/.venv"
            "$SCRIPT_DIR/.venv/bin/pip" install --quiet -e "$SCRIPT_DIR"
        fi
    fi
    # Re-resolve so PYTHON always points to the venv
    resolve_python

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

    # Remove existing service — try both old and new APIs
    launchctl bootout "$GUI_DOMAIN/$SERVICE_LABEL" 2>/dev/null || true
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    sleep 1

    # Bootstrap the service — fall back to load if bootstrap fails
    if launchctl bootstrap "$GUI_DOMAIN" "$PLIST_DEST" 2>/dev/null; then
        echo "Service bootstrapped."
    elif launchctl load "$PLIST_DEST" 2>/dev/null; then
        echo "Service loaded."
    else
        echo "WARNING: Could not start service. Try manually:"
        echo "  launchctl load $PLIST_DEST"
    fi

    sleep 2

    # Verify
    if launchctl print "$GUI_DOMAIN/$SERVICE_LABEL" &>/dev/null; then
        PID=$(launchctl print "$GUI_DOMAIN/$SERVICE_LABEL" 2>/dev/null | awk '/pid =/ {print $3}')
        if [[ -n "$PID" && "$PID" != "0" ]]; then
            echo -e "${GREEN}Service started successfully${RESET} (PID $PID)."
        else
            echo "Service loaded but not yet running. Check logs:"
            echo "  tail -f ~/Library/Logs/imessage-bridge.log"
            echo "  tail -f ~/Library/Logs/imessage-bridge.err"
        fi
    else
        echo "WARNING: Service failed to bootstrap. Check logs:"
        echo "  tail -f ~/Library/Logs/imessage-bridge.err"
        exit 1
    fi

    local lan_ip
    lan_ip=$(get_lan_ip)
    echo ""
    echo -e "${GREEN}Bridge listening on ${CYAN}${BIND_HOST}:${BIND_PORT}${RESET}"
    if [[ -n "$lan_ip" ]]; then
        echo -e "LAN address: ${CYAN}http://${lan_ip}:${BIND_PORT}${RESET}"
    fi

    prompt_deploy "$lan_ip"
}

prompt_deploy() {
    local lan_ip="${1:-}"

    echo ""
    echo -e "${BOLD}Deploy Linux client?${RESET}"
    echo ""
    echo -e "  ${BOLD}1)${RESET} Deploy via SSH       ${DIM}(push to a Linux machine over the network)${RESET}"
    echo -e "  ${BOLD}2)${RESET} Deploy to USB drive  ${DIM}(copy installer package to a mount point)${RESET}"
    echo -e "  ${BOLD}3)${RESET} Skip                 ${DIM}(deploy later with ./setup.sh --deploy-ssh or --deploy-usb)${RESET}"
    echo ""
    read -rp "Choice [1/2/3]: " choice

    case "$choice" in
        1)
            echo ""
            read -rp "SSH target (user@host): " ssh_target
            if [[ -z "$ssh_target" ]]; then
                echo "No target provided, skipping."
                return
            fi
            do_deploy_ssh "$ssh_target"
            ;;
        2)
            echo ""
            read -rp "USB mount path (e.g. /Volumes/USB): " usb_path
            if [[ -z "$usb_path" ]]; then
                echo "No path provided, skipping."
                return
            fi
            do_deploy_usb "$usb_path"
            ;;
        3|"")
            echo ""
            echo "Skipped. Deploy later:"
            echo -e "  ${DIM}./setup.sh --deploy-ssh user@host${RESET}"
            echo -e "  ${DIM}./setup.sh --deploy-usb /Volumes/USB${RESET}"
            echo ""
            ;;
        *)
            echo "Invalid choice, skipping deployment."
            ;;
    esac
}

do_deploy_menu() {
    local token lan_ip
    token=$(get_token)
    if [[ -z "$token" ]]; then
        echo "No token found. Run ./setup.sh first to install the bridge."
        exit 1
    fi
    lan_ip=$(get_lan_ip)

    echo ""
    echo -e "${BOLD}imsg-bridge — Deploy Linux Client${RESET}"
    echo ""
    if [[ -n "$lan_ip" ]]; then
        echo -e "  Bridge: ${CYAN}http://${lan_ip}:${BIND_PORT}${RESET}"
    fi
    prompt_deploy "$lan_ip"
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
    --deploy)
        do_deploy_menu
        ;;
    "")
        do_setup
        ;;
    *)
        echo -e "Usage: ./setup.sh ${DIM}[options]${RESET}"
        echo ""
        echo "  (no args)               Install bridge + optional deploy"
        echo "  --deploy                 Interactive deploy menu"
        echo "  --deploy-ssh user@host   Push client to Linux via SSH"
        echo "  --deploy-usb /path       Copy client package to USB"
        exit 1
        ;;
esac
