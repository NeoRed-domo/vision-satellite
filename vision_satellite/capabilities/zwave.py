"""Détection Z-Wave dongles via /dev/serial/by-id/."""
from __future__ import annotations

import os
from typing import Optional

_KNOWN_ZWAVE_DONGLES = [
    ("Aeotec_ZW090_Z-Stick_Gen5", "Aeotec Z-Stick Gen5"),
    ("Aeotec_Z-Stick_Gen7", "Aeotec Z-Stick Gen7"),
    ("Silicon_Labs_UZB1", "Silicon Labs UZB-1"),
    ("Zooz_S2_Stick_S700", "Zooz S700"),
]


def _resolve(serial_dir: str, name: str) -> str:
    full = os.path.join(serial_dir, name)
    try:
        target = os.readlink(full)
    except OSError:
        return full
    return os.path.normpath(os.path.join(os.path.dirname(full), target))


def detect_zwave() -> Optional[dict]:
    serial_dir = "/dev/serial/by-id"
    try:
        with os.scandir(serial_dir) as entries:
            names = [e.name for e in entries]
    except (FileNotFoundError, PermissionError):
        return None
    for name in names:
        for pattern, model in _KNOWN_ZWAVE_DONGLES:
            if pattern.lower() in name.lower():
                return {
                    "device_path": _resolve(serial_dir, name),
                    "model": model,
                }
    return None
