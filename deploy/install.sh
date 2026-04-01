#!/bin/bash
# ============================================================
#  OpenClaw Diagnostic Dashboard — One-Click Installer
#  Usage: ./deploy/install.sh [--port 9090] [--api-key xxx] [--advanced]
# ============================================================
set -euo pipefail

# ======================== Defaults ========================
INSTALL_DIR="/opt/openclaw-diag"
SERVICE_NAME="openclaw-diag"
ENV_DIR="/etc/openclaw-diag"
ENV_FILE="${ENV_DIR}/openclaw-diag.env"

PORT="9090"
HOST="0.0.0.0"
API_KEY=""
ADVANCED=""
EXTRA_ARGS=""

# ======================== Colors ========================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()   { err "$*"; exit 1; }

# ======================== Usage ========================
usage() {
    cat <<EOF
${BOLD}OpenClaw Diagnostic Dashboard — Installer${NC}

Usage:
  $(basename "$0") [options]

Options:
  --port PORT       Listen port (default: 9090)
  --host HOST       Bind address (default: 0.0.0.0)
  --api-key KEY     Remote collector auth key
  --advanced        Enable advanced diagnostics mode
  --uninstall       Remove installation and service
  --help            Show this help

Examples:
  sudo ./deploy/install.sh
  sudo ./deploy/install.sh --port 8765 --advanced
  sudo ./deploy/install.sh --api-key my-secret --port 9090
  sudo ./deploy/install.sh --uninstall
EOF
    exit 0
}

# ======================== Uninstall ========================
uninstall() {
    info "Uninstalling ${SERVICE_NAME}..."

    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        info "Stopping service..."
        systemctl stop "${SERVICE_NAME}"
    fi

    if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
        info "Disabling service..."
        systemctl disable "${SERVICE_NAME}"
    fi

    if [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
        rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
        systemctl daemon-reload
        ok "Service file removed"
    fi

    if [[ -d "${INSTALL_DIR}" ]]; then
        rm -rf "${INSTALL_DIR}"
        ok "Installation directory removed: ${INSTALL_DIR}"
    fi

    # Keep env file (user config)
    if [[ -f "${ENV_FILE}" ]]; then
        warn "Keeping config file: ${ENV_FILE} (remove manually if needed)"
    fi

    ok "Uninstall complete"
    exit 0
}

# ======================== Parse Args ========================
UNINSTALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            [[ -z "${2:-}" ]] && die "--port requires a value"
            PORT="$2"; shift 2 ;;
        --host)
            [[ -z "${2:-}" ]] && die "--host requires a value"
            HOST="$2"; shift 2 ;;
        --api-key)
            [[ -z "${2:-}" ]] && die "--api-key requires a value"
            API_KEY="$2"; shift 2 ;;
        --advanced)
            ADVANCED="--advanced"; shift ;;
        --uninstall)
            UNINSTALL=true; shift ;;
        --help|-h)
            usage ;;
        *)
            die "Unknown option: $1 (use --help)" ;;
    esac
done

[[ "$UNINSTALL" == true ]] && uninstall

# ======================== Pre-checks ========================
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  📊 OpenClaw Diagnostic Dashboard Installer${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Root check
if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root (use sudo)"
fi

# Python check
info "Step 1/5: Checking Python..."
if ! command -v python3 &>/dev/null; then
    die "Python 3 not found. Please install Python 3.7+"
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)" 2>/dev/null; then
    ok "Python ${PYVER}"
else
    die "Python ${PYVER} is too old. Requires 3.7+"
fi

# systemd check
if ! command -v systemctl &>/dev/null; then
    die "systemd not found. This installer requires systemd."
fi
ok "systemd available"

# Source files check
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

if [[ ! -f "${PROJECT_DIR}/openclaw-dashboard.py" ]]; then
    die "Cannot find openclaw-dashboard.py in ${PROJECT_DIR}"
fi
ok "Source files found"

# ======================== Install ========================

# Step 2: Copy files
info "Step 2/5: Installing files to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp "${PROJECT_DIR}/openclaw-dashboard.py" "${INSTALL_DIR}/"
cp -r "${PROJECT_DIR}/static" "${INSTALL_DIR}/"
chmod +x "${INSTALL_DIR}/openclaw-dashboard.py"
ok "Files installed to ${INSTALL_DIR}"

# Step 3: Generate env config
info "Step 3/5: Generating configuration..."
mkdir -p "${ENV_DIR}"

if [[ -f "${ENV_FILE}" ]]; then
    warn "Config file exists: ${ENV_FILE}"
    warn "Backing up to ${ENV_FILE}.bak"
    cp "${ENV_FILE}" "${ENV_FILE}.bak"
fi

# Build extra args
EXTRA_ARGS="${ADVANCED}"
if [[ -n "${API_KEY}" ]]; then
    EXTRA_ARGS="${EXTRA_ARGS} --api-key ${API_KEY}"
fi

cat > "${ENV_FILE}" <<EOF
# OpenClaw Diagnostic Dashboard Configuration
# Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')

OC_DIAG_PORT=${PORT}
OC_DIAG_HOST=${HOST}
OC_DIAG_EXTRA_ARGS=${EXTRA_ARGS}
EOF

chmod 600 "${ENV_FILE}"
ok "Config written to ${ENV_FILE}"

# Step 4: Install systemd service
info "Step 4/5: Installing systemd service..."

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=OpenClaw Diagnostic Dashboard
Documentation=https://github.com/nicepkg/openclaw
After=network.target
Wants=network.target

[Service]
Type=simple
EnvironmentFile=-${ENV_FILE}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/openclaw-dashboard.py \\
    --port \${OC_DIAG_PORT} \\
    --host \${OC_DIAG_HOST} \\
    --no-browser \\
    \${OC_DIAG_EXTRA_ARGS}
Restart=on-failure
RestartSec=5
WatchdogSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=openclaw-diag

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/tmp/openclaw
PrivateTmp=false

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
ok "Service installed"

# Step 5: Enable and start
info "Step 5/5: Starting service..."
systemctl enable "${SERVICE_NAME}" --quiet
systemctl start "${SERVICE_NAME}"

# Wait briefly for service to start
sleep 2

# ======================== Status ========================
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    ok "Service is running! ✅"
else
    STATUS=$(systemctl status "${SERVICE_NAME}" 2>&1 | tail -5)
    err "Service failed to start:"
    echo "${STATUS}"
    echo ""
    echo "Check logs: journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
fi

# Get IP for access URL
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

echo ""
echo -e "  ${BOLD}Installation Complete${NC}"
echo ""
echo -e "  📊 Dashboard:  ${GREEN}http://${LOCAL_IP}:${PORT}${NC}"
echo -e "  📊 Localhost:   ${GREEN}http://127.0.0.1:${PORT}${NC}"
echo ""
echo -e "  ${BOLD}Service Management:${NC}"
echo -e "    Status:   systemctl status ${SERVICE_NAME}"
echo -e "    Logs:     journalctl -u ${SERVICE_NAME} -f"
echo -e "    Stop:     systemctl stop ${SERVICE_NAME}"
echo -e "    Restart:  systemctl restart ${SERVICE_NAME}"
echo ""
echo -e "  ${BOLD}Configuration:${NC}"
echo -e "    Config:   ${ENV_FILE}"
echo -e "    Install:  ${INSTALL_DIR}"
echo ""
echo -e "  ${BOLD}Uninstall:${NC}"
echo -e "    sudo ${SCRIPT_DIR}/install.sh --uninstall"
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
