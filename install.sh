#!/usr/bin/env bash
set -euo pipefail

# imsg-bridge installer
# Works without pip, homebrew, or uv — just needs Python 3.12+ and git.

INSTALL_DIR="${IMSG_BRIDGE_DIR:-$HOME/.imsg-bridge}"
REPO="https://github.com/heyfinal/imsg-bridge.git"

echo "imsg-bridge installer"
echo "====================="
echo ""

# Check Python version
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is required but not found."
    echo "Install it from https://www.python.org/downloads/ or via Xcode command line tools:"
    echo "  xcode-select --install"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 12 ]]; }; then
    echo "ERROR: Python 3.12+ is required (found $PY_VERSION)."
    exit 1
fi

echo "Found Python $PY_VERSION"

# Check for imsg
if ! command -v imsg &>/dev/null; then
    echo ""
    echo "WARNING: 'imsg' CLI not found in PATH."
    echo "imsg-bridge requires the imsg CLI to function."
    echo "Install it from: https://github.com/nickthecook/imsg"
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Clone or update
if [[ -d "$INSTALL_DIR" ]]; then
    echo "Updating existing installation at $INSTALL_DIR..."
    cd "$INSTALL_DIR"
    git pull --ff-only
else
    echo "Cloning to $INSTALL_DIR..."
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Create venv and install dependencies
echo "Setting up virtual environment..."
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e "$INSTALL_DIR"

echo ""
echo "Installation complete."
echo ""
echo "Next steps:"
echo "  cd $INSTALL_DIR"
echo "  ./setup.sh        # Generate auth token + install as launchd service"
echo ""
echo "Or run manually:"
echo "  $INSTALL_DIR/.venv/bin/python3 -m uvicorn imsg_bridge.bridge:app --host 127.0.0.1 --port 5100"
