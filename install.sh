#!/bin/bash
# Vision Satellite — Installation
# Compatible: Debian, Ubuntu, JetPack (Jetson), Raspberry Pi OS
#
# Modes:
#   1. Interactif (défaut)      : sudo ./install.sh
#                                 → lance le wizard TUI (whiptail)
#   2. Non-interactif enrollment: sudo ./install.sh --enroll '<uri>'
#                                 → skip wizard, enroll direct avec l'URI QR
#   3. Re-enrollment            : sudo ./install.sh --reenroll '<uri>'
#                                 → régénère keypair + nouveau cert
#
# URI : vision-enroll://HOST:PORT?token=XXX&fp=YYY&name=ZZZ&v=1

set -euo pipefail

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="/opt/vision-satellite"
SERVICE_NAME="vision-satellite"
ENROLL_URI=""
REENROLL=false
ASSUME_YES=false

usage() {
    cat <<EOF
Usage :
  sudo $0                               # wizard interactif
  sudo $0 --enroll '<vision-enroll URI>'  # non-interactif
  sudo $0 --reenroll '<uri>'            # regen keypair + nouveau cert
  sudo $0 --yes --enroll '<uri>'        # full scripté (zéro prompt)

Options :
  --enroll URI     URI d'enrollment (QR code) du serveur Vision
  --reenroll URI   Idem mais remplace une installation existante
  --yes            Skip le wizard en toutes circonstances
  -h, --help       Affiche cette aide
EOF
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --enroll) ENROLL_URI="$2"; shift 2 ;;
        --reenroll) ENROLL_URI="$2"; REENROLL=true; shift 2 ;;
        --yes|-y) ASSUME_YES=true; shift ;;
        -h|--help) usage 0 ;;
        *) echo -e "${RED}Option inconnue: $1${NC}" >&2; usage 1 ;;
    esac
done

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Ce script doit être lancé avec sudo.${NC}" >&2
    exit 1
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
fi
if [ -f /etc/nv_tegra_release ]; then
    echo "  Platform: NVIDIA Jetson"
elif [ -f /sys/firmware/devicetree/base/model ]; then
    MODEL=$(tr -d '\0' < /sys/firmware/devicetree/base/model 2>/dev/null || true)
    echo "  Platform: $MODEL"
else
    echo "  Platform: Generic Linux $(uname -m)"
fi

# 2. Install system deps
echo -e "${CYAN}▶ Installation des dépendances système...${NC}"
apt-get update -qq
PACKAGES="python3 python3-pip python3-venv alsa-utils v4l-utils bluez-tools usbutils nmap"
if [ -z "$ENROLL_URI" ] || [ "$ASSUME_YES" = false ]; then
    PACKAGES="$PACKAGES whiptail"
fi
apt-get install -y -qq $PACKAGES

# cryptography (Python lib) pour keygen + fingerprint pinning
pip3 install --quiet --break-system-packages cryptography 2>/dev/null \
    || pip3 install --quiet cryptography

# 3. Install files
echo -e "${CYAN}▶ Installation dans $INSTALL_DIR...${NC}"
mkdir -p "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Copie le package + wizard.py
cp -r "$SCRIPT_DIR/vision_satellite" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/wizard.py" "$INSTALL_DIR/wizard.py"

# 4. Neutraliser PulseAudio (cf. issues connues Jetson Nano)
echo -e "${CYAN}▶ Neutralisation PulseAudio (évite conflit sur card USB)...${NC}"
if [ -f /etc/pulse/client.conf ]; then
    if ! grep -q "^autospawn\s*=\s*no" /etc/pulse/client.conf; then
        if grep -qE "^\s*;?\s*autospawn\s*=" /etc/pulse/client.conf; then
            sed -i 's/^\s*;*\s*autospawn\s*=.*/autospawn = no/' /etc/pulse/client.conf
        else
            echo "autospawn = no" >> /etc/pulse/client.conf
        fi
    fi
fi
systemctl --global mask pulseaudio.service pulseaudio.socket 2>/dev/null || true
for gdm in gdm gdm3; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${gdm}.service"; then
        systemctl stop "$gdm" 2>/dev/null || true
        systemctl disable "$gdm" 2>/dev/null || true
    fi
done
pkill -9 -f pulseaudio 2>/dev/null || true

# 5. USB autosuspend off (Jetson/Pi USB mic stability)
echo -e "${CYAN}▶ Désactivation USB autosuspend...${NC}"
if [ -f /sys/module/usbcore/parameters/autosuspend ]; then
    echo -1 > /sys/module/usbcore/parameters/autosuspend || true
fi
cat > /etc/udev/rules.d/90-vision-satellite-usb-audio.rules <<'UDEVEOF'
SUBSYSTEM=="usb", ATTR{bInterfaceClass}=="01", TEST=="power/control", ATTR{power/control}="on"
SUBSYSTEM=="usb", DRIVERS=="usb", ATTRS{bDeviceClass}=="00", TEST=="power/control", ATTR{power/control}="on"
UDEVEOF
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

# 6. Decide flow: wizard OR scripted
if [ -n "$ENROLL_URI" ]; then
    MODE="scripted"
elif [ "$ASSUME_YES" = true ]; then
    echo -e "${RED}--yes sans --enroll : impossible, l'URI est requise.${NC}"
    exit 1
elif [ ! -t 0 ]; then
    echo -e "${RED}Pas de TTY et pas d'URI fournie : lancer avec --enroll '<uri>'${NC}"
    exit 1
else
    MODE="wizard"
fi

# 7. Re-enrollment cleanup
if [ "$REENROLL" = true ]; then
    echo -e "${YELLOW}▶ Re-enrollment : suppression de l'ancienne identité...${NC}"
    rm -f "$INSTALL_DIR/device.key" "$INSTALL_DIR/device.crt" "$INSTALL_DIR/vision-ca.crt"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
fi

# 8. Enrollment
if [ "$MODE" = "wizard" ]; then
    echo -e "${CYAN}▶ Lancement du wizard TUI...${NC}"
    cd "$INSTALL_DIR"
    # Le wizard invoque lui-même python3 -m vision_satellite.main --enroll
    python3 wizard.py
    WIZARD_RC=$?
    if [ $WIZARD_RC -ne 0 ]; then
        echo -e "${RED}Wizard annulé ou échoué (code=$WIZARD_RC).${NC}"
        exit $WIZARD_RC
    fi
else
    echo -e "${CYAN}▶ Enrollment non-interactif...${NC}"
    cd "$INSTALL_DIR"
    python3 -m vision_satellite.main --enroll "$ENROLL_URI" \
        --key-path "$INSTALL_DIR/device.key" \
        --cert-path "$INSTALL_DIR/device.crt" \
        --ca-path "$INSTALL_DIR/vision-ca.crt"
fi

# 9. Create/update systemd service (invoque runtime — Phase C côté serveur finalisera le mTLS)
echo -e "${CYAN}▶ Création du service systemd...${NC}"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICEEOF
[Unit]
Description=Vision Satellite (multi-capability streamer)
Documentation=https://github.com/NeoRed-domo/vision-satellite
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment="HOME=$INSTALL_DIR"
ExecStart=/usr/bin/python3 -m vision_satellite.main --runtime
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
SupplementaryGroups=audio video dialout plugdev bluetooth

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
# Ne démarre PAS le service tant que runtime mTLS n'est pas livré (Phase C serveur)
# systemctl start "$SERVICE_NAME"

# 10. Verify
echo ""
if [ -f "$INSTALL_DIR/device.crt" ]; then
    echo -e "${GREEN}✓ Vision Satellite enrollé${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo "  Certs     : $INSTALL_DIR/device.crt, $INSTALL_DIR/vision-ca.crt"
    echo "  Service   : $SERVICE_NAME (enabled, sera démarré quand le runtime mTLS sera livré)"
    echo "  Prochaine étape : dans Vision Admin > Maison, placer ce satellite sur la carte."
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
else
    echo -e "${RED}✗ Enrollment a échoué — cert non trouvé.${NC}"
    exit 1
fi
