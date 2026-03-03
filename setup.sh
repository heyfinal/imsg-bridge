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
IMSG_BIN="/opt/homebrew/bin/imsg"

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

probe_chatdb_access() {
    local probe_err_var="${1:-}"
    local probe_err=""
    local probe_rc=0

    if [[ ! -x "$IMSG_BIN" ]]; then
        [[ -n "$probe_err_var" ]] && printf -v "$probe_err_var" ""
        return 0
    fi

    set +e
    probe_err=$("$IMSG_BIN" chats --json 2>&1 >/dev/null)
    probe_rc=$?
    set -e

    if [[ -n "$probe_err_var" ]]; then
        printf -v "$probe_err_var" "%s" "$probe_err"
    fi

    if [[ $probe_rc -eq 0 ]]; then
        return 0
    fi

    if echo "$probe_err" | grep -qi "permissionDenied"; then
        return 2
    fi

    return 1
}

service_recent_permission_denied() {
    local err_log="$LOG_DIR/imessage-bridge.err"
    if [[ ! -f "$err_log" ]]; then
        return 1
    fi

    tail -n 120 "$err_log" | grep -qi "permissionDenied(path: .*Library/Messages/chat.db"
}

open_full_disk_access_settings() {
    local opened=1
    local urls=(
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_AllFiles"
        "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
        "x-apple.systempreferences:com.apple.preference.security?Privacy"
    )

    if command -v open &>/dev/null; then
        local url
        for url in "${urls[@]}"; do
            if open "$url" >/dev/null 2>&1; then
                opened=0
                break
            fi
        done
    fi

    if [[ $opened -ne 0 ]] && command -v osascript &>/dev/null; then
        osascript >/dev/null 2>&1 <<'APPLESCRIPT'
try
    tell application "System Settings" to activate
end try
APPLESCRIPT
    fi
}

print_full_disk_access_steps() {
    echo ""
    echo -e "${BOLD}Enable Full Disk Access (required)${RESET}"
    echo "1) In System Settings, open: Privacy & Security > Full Disk Access"
    echo "2) Unlock changes (click the lock, use password/Touch ID)"
    echo "3) Enable your terminal app (Terminal / iTerm / Warp)"
    if [[ -x "$PYTHON" ]]; then
        echo "4) Add and enable Python used by the bridge:"
        echo "   $PYTHON"
    fi
    if [[ -x "$IMSG_BIN" ]]; then
        echo "5) Ensure imsg is enabled too (if listed):"
        echo "   $IMSG_BIN"
    fi
    echo "6) Return here and press Enter to re-check"
    echo ""
}

guide_full_disk_access() {
    local choice recheck

    echo ""
    read -rp "Open Full Disk Access settings now? [Y/n]: " choice
    if [[ ! "$choice" =~ ^[Nn]$ ]]; then
        open_full_disk_access_settings
        if command -v open &>/dev/null; then
            [[ -x "$PYTHON" ]] && open -R "$PYTHON" >/dev/null 2>&1 || true
            [[ -x "$IMSG_BIN" ]] && open -R "$IMSG_BIN" >/dev/null 2>&1 || true
        fi
    fi

    print_full_disk_access_steps
    read -rp "Press Enter to re-check, or type 'skip' to continue: " recheck
    if [[ "$recheck" =~ ^[Ss][Kk][Ii][Pp]$ ]]; then
        echo "Skipped Full Disk Access setup."
        return
    fi

    local probe_err=""
    local probe_result=0

    launchctl kickstart -k "$GUI_DOMAIN/$SERVICE_LABEL" >/dev/null 2>&1 || true
    sleep 2

    probe_chatdb_access probe_err
    probe_result=$?

    if [[ $probe_result -eq 0 ]]; then
        echo ""
        echo -e "${GREEN}Full Disk Access check passed.${RESET}"
        return
    fi

    if [[ $probe_result -eq 2 ]] || service_recent_permission_denied; then
        echo ""
        echo -e "${YELLOW}Still blocked:${RESET} Full Disk Access has not been fully enabled yet."
        echo "After enabling access, restart the agent with:"
        echo "  launchctl kickstart -k $GUI_DOMAIN/$SERVICE_LABEL"
        echo ""
        return
    fi

    echo ""
    echo -e "${YELLOW}imsg probe still failing:${RESET}"
    if [[ -n "$probe_err" ]]; then
        echo "$probe_err" | sed -n '1,3p'
    fi
    echo "Check logs for details:"
    echo "  tail -f ~/Library/Logs/imessage-bridge.err"
    echo ""
}

check_chatdb_access() {
    local probe_err=""
    local probe_result=0
    local needs_fda=0

    probe_chatdb_access probe_err
    probe_result=$?
    if [[ $probe_result -eq 2 ]]; then
        needs_fda=1
    fi
    if service_recent_permission_denied; then
        needs_fda=1
    fi

    if [[ $needs_fda -eq 0 ]]; then
        return
    fi

    echo ""
    echo -e "${YELLOW}WARNING:${RESET} imsg cannot read Messages database from current runtime."
    echo "The bridge needs Full Disk Access for Messages data."
    guide_full_disk_access
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

scan_lan() {
    SSH_HOSTS=()
    local my_ip subnet tmp_dir
    my_ip=$(get_lan_ip)
    [[ -z "$my_ip" ]] && return

    if ! command -v nc &>/dev/null; then
        echo -e "  ${YELLOW}Skipping LAN scan: 'nc' is not installed.${RESET}"
        return
    fi

    subnet="${my_ip%.*}"
    tmp_dir=$(mktemp -d)

    echo -e "  ${DIM}Scanning ${subnet}.0/24 for SSH hosts...${RESET}"

    local i ip
    for ((i=1; i<=254; i++)); do
        ip="${subnet}.${i}"
        [[ "$ip" == "$my_ip" ]] && continue
        (nc -z -G 1 "$ip" 22 >/dev/null 2>&1 && echo "$ip" >> "$tmp_dir/ssh_hosts") &
    done
    wait

    if [[ -f "$tmp_dir/ssh_hosts" ]]; then
        while IFS= read -r ip; do
            [[ -n "$ip" ]] && SSH_HOSTS+=("$ip")
        done < <(sort -t. -k1,1n -k2,2n -k3,3n -k4,4n "$tmp_dir/ssh_hosts" | uniq)
    fi

    rm -rf "$tmp_dir"
}

scan_usb_drives() {
    USB_DRIVES=()
    USB_SIZES=()

    local root_device
    root_device=$(df / 2>/dev/null | awk 'NR==2 {print $1}')

    local volumes=()
    shopt -s nullglob
    volumes=(/Volumes/*)
    shopt -u nullglob

    local vol name vol_device free_space info device_location protocol removable
    for vol in "${volumes[@]}"; do
        [[ -d "$vol" ]] || continue
        name=$(basename "$vol")

        # Skip system volumes
        [[ "$name" == "Macintosh HD" ]] && continue
        [[ "$name" == "Macintosh HD - Data" ]] && continue
        [[ "$name" == "Recovery" ]] && continue
        [[ "$name" == "Preboot" ]] && continue
        [[ "$name" == "VM" ]] && continue
        [[ "$name" == "Update" ]] && continue

        vol_device=$(df "$vol" 2>/dev/null | awk 'NR==2 {print $1}')
        [[ -z "$vol_device" ]] && continue
        [[ "$vol_device" == "$root_device" ]] && continue

        [[ ! -w "$vol" ]] && continue

        if command -v diskutil &>/dev/null; then
            info=$(diskutil info "$vol" 2>/dev/null || true)
            device_location=$(echo "$info" | awk -F': *' '/Device Location/ {print $2}')
            protocol=$(echo "$info" | awk -F': *' '/Protocol/ {print $2}')
            removable=$(echo "$info" | awk -F': *' '/Removable Media/ {print $2}')

            if [[ "$device_location" != "External" && "$protocol" != "USB" && "$removable" != "Removable" ]]; then
                continue
            fi
        fi

        free_space=$(df -h "$vol" 2>/dev/null | awk 'NR==2 {print $4}')

        USB_DRIVES+=("$vol")
        USB_SIZES+=("${free_space:-unknown}")
    done
}

is_valid_ipv4() {
    local ip="$1"
    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1

    local IFS=.
    local octet
    for octet in $ip; do
        [[ "$octet" =~ ^[0-9]+$ ]] || return 1
        (( octet >= 0 && octet <= 255 )) || return 1
    done
    return 0
}

prompt_ssh_target() {
    echo ""
    scan_lan

    local target_ip="" sel ssh_user ssh_pass
    local manual_option=1
    if [[ ${#SSH_HOSTS[@]} -gt 0 ]]; then
        echo ""
        echo -e "  ${BOLD}Detected SSH hosts (possible Linux machines):${RESET}"
        echo ""
        local i=1
        for ip in "${SSH_HOSTS[@]}"; do
            echo -e "  ${BOLD}$i)${RESET} $ip"
            ((i++))
        done
        manual_option=$i
        echo -e "  ${BOLD}$manual_option)${RESET} Enter IP manually"
        echo ""
        read -rp "Select target [1-$manual_option]: " sel

        if [[ "$sel" =~ ^[0-9]+$ ]] && (( sel >= 1 && sel <= ${#SSH_HOSTS[@]} )); then
            target_ip="${SSH_HOSTS[$((sel-1))]}"
        else
            read -rp "IP address: " target_ip
        fi
    else
        echo ""
        echo -e "  ${DIM}No SSH hosts found on LAN.${RESET}"
        echo ""
        read -rp "IP address: " target_ip
    fi

    if [[ -z "$target_ip" ]]; then
        echo "No target provided, skipping."
        return
    fi

    if ! is_valid_ipv4 "$target_ip"; then
        echo "ERROR: Invalid IP address: $target_ip"
        return
    fi

    echo ""
    read -rp "SSH username: " ssh_user
    if [[ -z "$ssh_user" ]]; then
        echo "No username provided, skipping."
        return
    fi

    read -rsp "SSH password (leave blank for key-based auth): " ssh_pass
    echo ""

    local ssh_target="${ssh_user}@${target_ip}"
    do_deploy_ssh "$ssh_target" "$ssh_pass"
}

prompt_usb_target() {
    scan_usb_drives

    local usb_path="" sel
    if [[ ${#USB_DRIVES[@]} -gt 0 ]]; then
        echo ""
        echo -e "  ${BOLD}Detected external drives:${RESET}"
        echo ""
        local i=1
        for idx in "${!USB_DRIVES[@]}"; do
            echo -e "  ${BOLD}$i)${RESET} ${USB_DRIVES[$idx]}  ${DIM}(${USB_SIZES[$idx]} free)${RESET}"
            ((i++))
        done
        echo -e "  ${BOLD}$i)${RESET} Enter path manually"
        echo ""
        read -rp "Select drive [1-$i]: " sel

        if [[ "$sel" =~ ^[0-9]+$ ]] && (( sel >= 1 && sel <= ${#USB_DRIVES[@]} )); then
            usb_path="${USB_DRIVES[$((sel-1))]}"
        elif [[ "$sel" == "$i" ]] || [[ -z "$sel" ]]; then
            read -rp "Mount path: " usb_path
        else
            read -rp "Mount path: " usb_path
        fi

        if [[ -z "$usb_path" ]]; then
            echo "No path provided, skipping."
            return
        fi

        do_deploy_usb "$usb_path"
    else
        echo ""
        echo -e "  ${DIM}No external drives detected.${RESET}"
        echo ""
        read -rp "Mount path (e.g. /Volumes/USB): " usb_path
        if [[ -z "$usb_path" ]]; then
            echo "No path provided, skipping."
            return
        fi
        do_deploy_usb "$usb_path"
    fi
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
    local ssh_password="${2:-}"
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

    local ssh_opts=(
        -o ConnectTimeout=8
        -o ServerAliveInterval=15
        -o StrictHostKeyChecking=accept-new
    )
    local remote_install_cmd="cd /tmp && tar xzf imsg-gtk-install.tar.gz && cd imsg-gtk-install && chmod +x install.sh && ./install.sh"
    local remote_install_cmd_with_sudo_pw="$remote_install_cmd"
    if [[ -n "$ssh_password" ]]; then
        local quoted_pw
        quoted_pw=$(printf '%q' "$ssh_password")
        remote_install_cmd_with_sudo_pw="export IMSG_SUDO_PASSWORD=${quoted_pw}; ${remote_install_cmd}"
    fi

    local deploy_rc=0
    set +e
    if [[ -n "$ssh_password" ]] && command -v sshpass &>/dev/null; then
        echo -e "Copying to ${CYAN}$target${RESET} with password auth..."
        exec 3<<<"$ssh_password"
        sshpass -d 3 scp "${ssh_opts[@]}" "$tmp_dir/imsg-gtk-install.tar.gz" "$target:/tmp/imsg-gtk-install.tar.gz"
        deploy_rc=$?
        exec 3<&-
        if [[ $deploy_rc -eq 0 ]]; then
            echo "Installing on remote host..."
            exec 4<<<"$ssh_password"
            sshpass -d 4 ssh "${ssh_opts[@]}" -tt "$target" "$remote_install_cmd_with_sudo_pw"
            deploy_rc=$?
            exec 4<&-
        fi
    else
        if [[ -n "$ssh_password" ]]; then
            echo -e "${YELLOW}sshpass not installed; falling back to interactive SSH prompts.${RESET}"
        fi
        echo -e "Copying to ${CYAN}$target${RESET}..."
        scp "${ssh_opts[@]}" "$tmp_dir/imsg-gtk-install.tar.gz" "$target:/tmp/imsg-gtk-install.tar.gz"
        deploy_rc=$?
        if [[ $deploy_rc -eq 0 ]]; then
            echo "Installing on remote host..."
            ssh "${ssh_opts[@]}" -tt "$target" "$remote_install_cmd_with_sudo_pw"
            deploy_rc=$?
        fi
    fi
    set -e
    unset ssh_password

    rm -rf "$tmp_dir"

    if [[ $deploy_rc -ne 0 ]]; then
        echo "ERROR: SSH deployment failed."
        exit 1
    fi

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

    check_chatdb_access
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
            prompt_ssh_target
            ;;
        2)
            prompt_usb_target
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
