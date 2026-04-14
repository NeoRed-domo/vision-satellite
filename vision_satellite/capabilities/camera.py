"""Détection caméra via v4l2-ctl."""
from __future__ import annotations

import glob
import re
import subprocess
from typing import Optional

_SIZES_RE = re.compile(r"Sizes available:\s*([\d x]+)")
_CARD_RE = re.compile(r"Card type\s*:\s*(.+)")


def _parse_v4l2_output(out: str) -> dict:
    """Extract card type + max resolution from v4l2-ctl --all output."""
    info = {}
    m = _CARD_RE.search(out)
    if m:
        info["description"] = m.group(1).strip()
    sizes_m = _SIZES_RE.search(out)
    if sizes_m:
        sizes = sizes_m.group(1).strip().split()
        # Pick the biggest WxH by total pixels
        best = None
        best_px = 0
        for s in sizes:
            if "x" in s:
                try:
                    w, h = s.split("x")
                    px = int(w) * int(h)
                    if px > best_px:
                        best = s
                        best_px = px
                except ValueError:
                    pass
        if best:
            info["max_resolution"] = best
    return info


def _probe(device: str) -> Optional[dict]:
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--all"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    info = _parse_v4l2_output(result.stdout)
    if "max_resolution" not in info:
        return None
    return {
        "device": device,
        "description": info.get("description", "unknown"),
        "max_resolution": info["max_resolution"],
        "codecs": [],  # v0 : pas implémenté
    }


def detect_camera() -> Optional[dict]:
    """Scan /dev/video*, retient la caméra avec la plus grande résolution."""
    devices = sorted(glob.glob("/dev/video*"))
    if not devices:
        return None
    candidates = []
    for dev in devices:
        info = _probe(dev)
        if info:
            candidates.append(info)
    if not candidates:
        return None
    # Pick the one with largest max_resolution
    def _px(info: dict) -> int:
        try:
            w, h = info["max_resolution"].split("x")
            return int(w) * int(h)
        except (ValueError, KeyError):
            return 0
    candidates.sort(key=_px, reverse=True)
    return candidates[0]
