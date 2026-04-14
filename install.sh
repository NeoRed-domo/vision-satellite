#!/bin/bash
# Vision Satellite — Installation one-shot
# Compatible: Debian, Ubuntu, JetPack (Jetson), Raspberry Pi OS
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/NeoRed-domo/vision-satellite/main/install.sh | bash -s -- --host 192.168.1.100
#   # ou
#   git clone https://github.com/NeoRed-domo/vision-satellite.git && cd vision-satellite && ./install.sh --host 192.168.1.100

set -euo pipefail

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

INSTALL_DIR="/opt/vision-satellite"
SERVICE_NAME="vision-satellite"
VISION_HOST=""
VISION_PORT="9999"
DEVICE=""

usage() {
    echo "Usage: $0 --host <VISION_SERVER_IP> [--port 9999] [--device hw:1,0]"
    echo ""
    echo "  --host    IP du serveur Vision (requis)"
    echo "  --port    Port TCP (défaut: 9999)"
    echo "  --device  Device ALSA (auto-détecté si omis)"
    exit 1
}

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) VISION_HOST="$2"; shift 2 ;;
        --port) VISION_PORT="$2"; shift 2 ;;
        --device) DEVICE="$2"; shift 2 ;;
        *) usage ;;
    esac
done

if [ -z "$VISION_HOST" ]; then
    echo -e "${RED}Erreur: --host requis${NC}"
    usage
fi

echo -e "${CYAN}"
cat << 'EOF'
 ╔═══════════════════════════════════════════════╗
 ║  Vision Satellite — Installation              ║
 ╚═══════════════════════════════════════════════╝
EOF
echo -e "${NC}"

# 1. Detect OS
echo -e "${CYAN}▶ Détection de l'OS...${NC}"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "  OS: $PRETTY_NAME"
else
    echo "  OS: inconnu (on continue quand même)"
fi

# Detect if Jetson
if [ -f /etc/nv_tegra_release ]; then
    echo "  Platform: NVIDIA Jetson"
elif [ -f /sys/firmware/devicetree/base/model ]; then
    MODEL=$(cat /sys/firmware/devicetree/base/model 2>/dev/null | tr -d '\0')
    echo "  Platform: $MODEL"
else
    echo "  Platform: Generic Linux $(uname -m)"
fi

# 2. Install system dependencies
echo -e "${CYAN}▶ Installation des dépendances système...${NC}"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv libasound2-dev alsa-utils

# 3. Create install directory
echo -e "${CYAN}▶ Installation dans $INSTALL_DIR...${NC}"
sudo mkdir -p "$INSTALL_DIR"

# Copy script (from local dir if available, otherwise download)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/vision_satellite.py" ]; then
    sudo cp "$SCRIPT_DIR/vision_satellite.py" "$INSTALL_DIR/"
else
    sudo curl -sSL "https://raw.githubusercontent.com/NeoRed-domo/vision-satellite/main/vision_satellite.py" \
        -o "$INSTALL_DIR/vision_satellite.py"
fi
sudo chmod +x "$INSTALL_DIR/vision_satellite.py"

# 4. Create venv and install Python deps
echo -e "${CYAN}▶ Création du venv Python...${NC}"
sudo python3 -m venv "$INSTALL_DIR/venv"
sudo "$INSTALL_DIR/venv/bin/pip" install --quiet pyalsaaudio

# 5. Create config file
echo -e "${CYAN}▶ Configuration...${NC}"
DEVICE_ARG=""
if [ -n "$DEVICE" ]; then
    DEVICE_ARG="--device $DEVICE"
fi

sudo tee "$INSTALL_DIR/config.env" > /dev/null << ENVEOF
VISION_HOST=$VISION_HOST
VISION_PORT=$VISION_PORT
DEVICE_ARG=$DEVICE_ARG
ENVEOF

# 5a. Neutraliser PulseAudio (concurrence le mic USB sur certains setups,
#     p.ex. TONOR TM20 → EIO quand gdm lance pulse en parallèle).
#     Un satellite audio n'en a pas besoin.
echo -e "${CYAN}▶ Neutralisation PulseAudio...${NC}"
if [ -f /etc/pulse/client.conf ]; then
    if ! grep -q "^autospawn\s*=\s*no" /etc/pulse/client.conf; then
        # Remplace une ligne autospawn existante, sinon append
        if grep -qE "^\s*;?\s*autospawn\s*=" /etc/pulse/client.conf; then
            sudo sed -i 's/^\s*;*\s*autospawn\s*=.*/autospawn = no/' /etc/pulse/client.conf
        else
            echo "autospawn = no" | sudo tee -a /etc/pulse/client.conf > /dev/null
        fi
    fi
fi
sudo systemctl --global mask pulseaudio.service pulseaudio.socket 2>/dev/null || true
# GDM sur headless = consommateur pulse inutile
for gdm in gdm gdm3; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${gdm}.service"; then
        sudo systemctl stop "$gdm" 2>/dev/null || true
        sudo systemctl disable "$gdm" 2>/dev/null || true
    fi
done
sudo pkill -9 -f pulseaudio 2>/dev/null || true

# 5b. Disable USB autosuspend (fix USB mic drops after ~10s on Jetson/Pi)
echo -e "${CYAN}▶ Désactivation USB autosuspend (évite les déconnexions micro)...${NC}"
# Runtime: take effect immediately
if [ -f /sys/module/usbcore/parameters/autosuspend ]; then
    echo -1 | sudo tee /sys/module/usbcore/parameters/autosuspend > /dev/null || true
fi
# Persistent: udev rule (couvre tous les periphériques audio USB, présents ou futurs)
sudo tee /etc/udev/rules.d/90-vision-satellite-usb-audio.rules > /dev/null << 'UDEVEOF'
# Vision Satellite — garde les périphériques audio USB en permanence actifs
SUBSYSTEM=="usb", ATTR{bInterfaceClass}=="01", TEST=="power/control", ATTR{power/control}="on"
SUBSYSTEM=="usb", DRIVERS=="usb", ATTRS{bDeviceClass}=="00", TEST=="power/control", ATTR{power/control}="on"
UDEVEOF
sudo udevadm control --reload-rules 2>/dev/null || true
sudo udevadm trigger 2>/dev/null || true

# 6. Create systemd service
echo -e "${CYAN}▶ Création du service systemd...${NC}"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null << SERVICEEOF
[Unit]
Description=Vision Satellite (audio streamer)
Documentation=https://github.com/NeoRed-domo/vision-satellite
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$INSTALL_DIR/config.env
# HOME writable pour que le plugin ALSA→Pulse (chargé implicitement par
# libasound2-plugins) puisse créer son dossier sans warning.
Environment="HOME=$INSTALL_DIR"
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/vision_satellite.py --host \${VISION_HOST} --port \${VISION_PORT} \$DEVICE_ARG
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=vision-satellite

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_DIR
# Audio access
SupplementaryGroups=audio

[Install]
WantedBy=multi-user.target
SERVICEEOF

# 7. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"

# 8. Verify
echo ""
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "${GREEN}✓ Vision Audio Satellite installé et démarré${NC}"
else
    echo -e "${RED}✗ Le service n'a pas démarré. Check: journalctl -u $SERVICE_NAME -n 20${NC}"
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Serveur Vision: $VISION_HOST:$VISION_PORT"
echo "  Logs:           journalctl -u $SERVICE_NAME -f"
echo "  Status:         systemctl status $SERVICE_NAME"
echo "  Restart:        sudo systemctl restart $SERVICE_NAME"
echo "  Config:         $INSTALL_DIR/config.env"
echo "  Désinstaller:   sudo systemctl stop $SERVICE_NAME && sudo rm -rf $INSTALL_DIR /etc/systemd/system/${SERVICE_NAME}.service"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
