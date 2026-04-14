"""Détection Zigbee dongles via /dev/serial/by-id/."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Liste des patterns connus (substring match, case-insensitive)
_KNOWN_ZIGBEE_DONGLES = [
    ("Sonoff_Zigbee_3.0_USB_Dongle_Plus", "Sonoff CC2652P"),
    ("dresden_elektronik_ConBee_II", "ConBee II"),
    ("Texas_Instruments_TI_CC2531", "Texas Instruments CC2531"),
    ("Electrolama_zig-a-zig-ah", "zig-a-zig-ah"),
]


def _resolve(serial_dir: str, name: str) -> str:
    """Résoud /dev/serial/by-id/NAME → /dev/ttyUSBx ou /dev/ttyACMx."""
    full = os.path.join(serial_dir, name)
    try:
        target = os.readlink(full)
    except OSError:
        return full
    # target est relatif (../../ttyUSB0)
    resolved = os.path.normpath(os.path.join(os.path.dirname(full), target))
    return resolved


def detect_zigbee() -> Optional[dict]:
    """Scan /dev/serial/by-id/ pour un dongle Zigbee connu."""
    serial_dir = "/dev/serial/by-id"
    try:
        with os.scandir(serial_dir) as entries:
            names = [e.name for e in entries]
    except (FileNotFoundError, PermissionError):
        return None
    for name in names:
        for pattern, model in _KNOWN_ZIGBEE_DONGLES:
            if pattern.lower() in name.lower():
                return {
                    "device_path": _resolve(serial_dir, name),
                    "model": model,
                }
    return None
