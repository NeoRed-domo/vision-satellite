#!/bin/bash
# Vision Audio Satellite — Installation one-shot
# Compatible: Debian, Ubuntu, JetPack (Jetson), Raspberry Pi OS
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/NeoRed/vision-audio-satellite/main/install.sh | bash -s -- --host 192.168.1.100
#   # ou
#   git clone https://github.com/NeoRed/vision-audio-satellite.git && cd vision-audio-satellite && ./install.sh --host 192.168.1.100

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
 ║  Vision Audio Satellite — Installation        ║
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
    sudo curl -sSL "https://raw.githubusercontent.com/NeoRed/vision-audio-satellite/main/vision_satellite.py" \
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

# 6. Create systemd service
echo -e "${CYAN}▶ Création du service systemd...${NC}"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null << SERVICEEOF
[Unit]
Description=Vision Audio Satellite
Documentation=https://github.com/NeoRed/vision-audio-satellite
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$INSTALL_DIR/config.env
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/vision_satellite.py --host \${VISION_HOST} --port \${VISION_PORT} \${DEVICE_ARG}
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
