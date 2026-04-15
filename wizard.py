#!/usr/bin/env python3
"""Wizard TUI pour installation interactive du satellite Vision.

Lancé par install.sh en mode interactif. Utilise whiptail pour les écrans.
Orchestre : détection hardware → confirmation → network → enrollment URI →
récap → install (invoque python3 -m vision_satellite.main --enroll).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# On met le worktree/install dir dans le path pour importer le package
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vision_satellite import qr_parse
from vision_satellite.main import detect_all_capabilities


_WHIPTAIL = "whiptail"


def _whiptail_yesno(message: str, title: str = "Vision Satellite") -> bool:
    """Affiche un yes/no. Returns True si yes, False sinon."""
    result = subprocess.run(
        [_WHIPTAIL, "--title", title, "--yesno", message, "15", "70"],
        stderr=subprocess.PIPE, text=True,
    )
    return result.returncode == 0


def _whiptail_msgbox(message: str, title: str = "Vision Satellite") -> None:
    subprocess.run(
        [_WHIPTAIL, "--title", title, "--msgbox", message, "15", "70"],
        stderr=subprocess.PIPE, text=True,
    )


def _whiptail_inputbox(prompt: str, default: str = "", title: str = "Vision Satellite") -> str | None:
    """Saisie texte. Returns None si annulé."""
    result = subprocess.run(
        [_WHIPTAIL, "--title", title, "--inputbox", prompt, "12", "70", default],
        stderr=subprocess.PIPE, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stderr.strip()  # whiptail écrit le résultat sur stderr


def _whiptail_passwordbox(prompt: str, title: str = "Vision Satellite") -> str | None:
    result = subprocess.run(
        [_WHIPTAIL, "--title", title, "--passwordbox", prompt, "12", "70"],
        stderr=subprocess.PIPE, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stderr.strip()


def _whiptail_checklist(title: str, prompt: str, items: list[tuple[str, str, bool]]) -> list[str] | None:
    """
    items : liste de (tag, description, preselected).
    Returns liste des tags sélectionnés, ou None si annulé.
    """
    args = [_WHIPTAIL, "--title", title, "--checklist", prompt, "20", "70", str(len(items))]
    for tag, desc, preselect in items:
        args += [tag, desc, "ON" if preselect else "OFF"]
    result = subprocess.run(args, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return None
    # whiptail returns "tag1" "tag2" "tag3" format
    return [t.strip('"') for t in result.stderr.strip().split()]


def screen_welcome() -> bool:
    return _whiptail_yesno(
        "Bienvenue dans l'assistant Vision Satellite.\n\n"
        "Cet assistant va :\n"
        "  1. Détecter vos périphériques (micro, caméra, dongles)\n"
        "  2. Configurer le réseau si nécessaire\n"
        "  3. Enroller ce satellite auprès de votre serveur Vision\n\n"
        "Durée estimée : 3-5 minutes\n\n"
        "Continuer ?"
    )


def screen_os_check() -> bool:
    try:
        os_release = Path("/etc/os-release").read_text()
    except OSError:
        os_release = "(inconnu)"
    # Juste informatif — on continue sauf si user cancel
    _whiptail_msgbox(f"OS détecté :\n\n{os_release[:800]}")
    return True


def screen_detect_capabilities() -> dict:
    """Détecte tout + affiche une checklist avec présélection selon détection."""
    caps = detect_all_capabilities()
    items = [
        ("audio", _format_cap("Micro", caps.get("audio")), caps.get("audio") is not None),
        ("camera", _format_cap("Caméra", caps.get("camera")), caps.get("camera") is not None),
        ("bluetooth", _format_cap("Bluetooth", caps.get("bluetooth")), caps.get("bluetooth") is not None),
        ("zigbee", _format_cap("Zigbee", caps.get("zigbee")), caps.get("zigbee") is not None),
        ("zwave", _format_cap("Z-Wave", caps.get("zwave")), caps.get("zwave") is not None),
    ]
    selected = _whiptail_checklist(
        "Vision Satellite — Détection",
        "Hardware détecté. Sélectionnez ce que vous souhaitez activer :",
        items,
    )
    if selected is None:
        return {}
    return {k: v for k, v in caps.items() if k in selected}


def _format_cap(label: str, cap: dict | None) -> str:
    if cap is None:
        return f"{label} (non détecté)"
    desc = cap.get("description") or cap.get("model") or cap.get("address") or ""
    return f"{label} : {desc}"[:60]


def screen_network() -> bool:
    """Test de connectivité. Retourne True si OK, False si user abort."""
    try:
        result = subprocess.run(
            ["nc", "-zv", "-w", "3", "8.8.8.8", "53"],
            capture_output=True, text=True, timeout=6,
        )
        ok = result.returncode == 0
    except Exception:
        ok = False
    if ok:
        return _whiptail_yesno(
            "Connectivité Internet OK.\n\nPasser à l'enrollment ?\n"
            "(choisir 'Non' pour configurer le WiFi)"
        )
    else:
        _whiptail_msgbox(
            "⚠ Pas de connectivité Internet.\n\n"
            "La configuration WiFi sera proposée ensuite."
        )
        return False  # False = passer à l'écran wifi


def screen_wifi() -> bool:
    """nmcli rescan + choix SSID + saisie password. Returns True si connecté."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _whiptail_msgbox("nmcli absent ou timeout. Configurez le WiFi manuellement.")
        return False
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        _whiptail_msgbox("Aucun réseau WiFi détecté.")
        return False

    # Parse nmcli output (SSID:SIGNAL:SECURITY)
    options = []
    for line in lines[:15]:  # max 15 pour UX
        parts = line.split(":")
        if len(parts) >= 3 and parts[0].strip():
            ssid = parts[0]
            signal = parts[1]
            security = parts[2] or "open"
            options.append((ssid, f"{security} signal {signal}"))
    if not options:
        return False

    # Menu radiolist
    args = [_WHIPTAIL, "--title", "Vision Satellite — WiFi",
            "--radiolist", "Sélectionnez un réseau :", "20", "70", str(len(options))]
    for i, (ssid, info) in enumerate(options):
        args += [ssid, info, "ON" if i == 0 else "OFF"]
    result = subprocess.run(args, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return False
    ssid = result.stderr.strip().strip('"')
    password = _whiptail_passwordbox(f"Mot de passe pour {ssid} :")
    if not password:
        return False

    # Connect
    try:
        c = subprocess.run(
            ["nmcli", "dev", "wifi", "connect", ssid, "password", password],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        _whiptail_msgbox("Connexion WiFi timeout.")
        return False
    if c.returncode != 0:
        _whiptail_msgbox(f"Échec connexion WiFi :\n{c.stderr[:300]}")
        return False
    _whiptail_msgbox(f"✓ Connecté à {ssid}")
    return True


def screen_enroll_uri() -> tuple[str, dict] | None:
    """Demande à l'utilisateur le QR code (copy-paste). Returns (uri, parsed) ou None."""
    while True:
        uri = _whiptail_inputbox(
            "Collez le QR code depuis Vision Admin :\n\n(Format : vision-enroll://...)",
        )
        if uri is None:
            return None
        try:
            parsed = qr_parse.parse_enroll_uri(uri.strip())
        except ValueError as exc:
            retry = _whiptail_yesno(
                f"URI invalide : {exc}\n\nRéessayer ?"
            )
            if not retry:
                return None
            continue
        return uri.strip(), parsed


def screen_recap(capabilities: dict, parsed_uri: dict) -> bool:
    lines = [
        f"Satellite : {parsed_uri.get('name') or '(sans nom)'}",
        f"Serveur   : {parsed_uri['host']}:{parsed_uri['port']}",
        "",
        "Capabilities activées :",
    ]
    for cap_name, cap_data in capabilities.items():
        desc = _format_cap(cap_name.capitalize(), cap_data)
        lines.append(f"  • {desc}")
    lines.append("")
    lines.append("Lancer l'installation ?")
    return _whiptail_yesno("\n".join(lines))


_INSTALL_LOG_PATH = Path("/var/log/vision-satellite-install.log")


def screen_install(uri: str) -> bool:
    """Invoque `python3 -m vision_satellite.main --enroll <uri>`.

    Capture stdout+stderr et les écrit dans /var/log/vision-satellite-install.log
    pour qu'on puisse débugger en cas d'échec (le wizard tourne en TUI, donc
    on ne peut pas afficher 50 lignes de stack trace).
    """
    result = subprocess.run(
        ["python3", "-m", "vision_satellite.main", "--enroll", uri],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parent),
    )
    try:
        with _INSTALL_LOG_PATH.open("w") as f:
            f.write(f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n")
            f.write(f"--- returncode: {result.returncode} ---\n")
    except OSError:
        pass  # /var/log non-writable — pas bloquant
    return result.returncode == 0


def screen_final(success: bool, satellite_name: str = "") -> None:
    if success:
        msg = (
            f"✓ Satellite {satellite_name or ''} enrollé avec succès !\n\n"
            "Prochaine étape : dans Vision Admin > Maison,\n"
            "placez ce satellite sur la carte."
        )
    else:
        msg = (
            "✗ Échec de l'enrollment.\n\n"
            f"Trace complète : cat {_INSTALL_LOG_PATH}\n"
            "Ou relance en CLI pour voir l'erreur :\n"
            "  cd /opt/vision-satellite && \\\n"
            "  sudo python3 -m vision_satellite.main --enroll '<uri>'"
        )
    _whiptail_msgbox(msg)


def main() -> int:
    if not screen_welcome():
        return 1
    if not screen_os_check():
        return 1

    capabilities = screen_detect_capabilities()
    if not capabilities:
        _whiptail_msgbox("Aucune capability sélectionnée. Abandon.")
        return 1

    net_ok = screen_network()
    if not net_ok:
        screen_wifi()  # on laisse passer même si échec

    result = screen_enroll_uri()
    if result is None:
        return 1
    uri, parsed = result

    if not screen_recap(capabilities, parsed):
        return 1

    success = screen_install(uri)
    screen_final(success, satellite_name=parsed.get("name", ""))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
