"""Détection Bluetooth via hciconfig."""
from __future__ import annotations

import re
import subprocess
from typing import Optional

_ADAPTER_RE = re.compile(r"^(hci\d+):", re.MULTILINE)
_ADDR_RE = re.compile(r"BD Address:\s*([0-9A-Fa-f:]{17})")


def detect_bluetooth() -> Optional[dict]:
    """Retourne adaptateur BT primaire s'il est UP avec BDADDR non-nulle."""
    try:
        result = subprocess.run(
            ["hciconfig"], capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None

    adapter_m = _ADAPTER_RE.search(result.stdout)
    addr_m = _ADDR_RE.search(result.stdout)
    if not adapter_m or not addr_m:
        return None

    addr = addr_m.group(1)
    if addr.upper() == "00:00:00:00:00:00":
        return None

    return {
        "adapter": adapter_m.group(1),
        "address": addr.upper(),
    }
