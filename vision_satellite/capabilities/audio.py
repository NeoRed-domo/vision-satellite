"""Détection et capture audio via arecord/ALSA."""
from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import List, Optional, Tuple

log = logging.getLogger("vision.satellite.audio")

CHANNELS = 1
CHUNK_MS = 80  # chunk duration (80ms → latence capture)
BYTES_PER_SAMPLE = 2  # S16_LE = 2 octets

# Rates préférés — on retient le premier que le mic supporte en natif.
# 16 kHz = pipeline Vision (zéro resample serveur). 48 kHz = natif le
# plus courant chez les USB mics cheap.
PREFERRED_RATES = [16000, 32000, 44100, 48000]

# Durée du test de détection — si un rate est instable (EIO après
# quelques chunks), ce délai doit être long assez pour le révéler.
DETECT_DURATION_S = 3


def chunk_samples(rate: int) -> int:
    return rate * CHUNK_MS // 1000


def chunk_bytes(rate: int) -> int:
    return chunk_samples(rate) * BYTES_PER_SAMPLE


# ============================================================
# Détection des cartes et rates supportés
# ============================================================

def enumerate_cards() -> List[Tuple[int, str, bool]]:
    """
    Parse /proc/asound/cards → [(index, description, is_usb), ...].
    """
    cards = []
    try:
        with open("/proc/asound/cards") as f:
            lines = f.read().splitlines()
    except OSError:
        return cards

    i = 0
    while i < len(lines):
        header = re.match(r"\s*(\d+)\s*\[([^\]]+)\]\s*:\s*(\S+)\s*-\s*(.*)", lines[i])
        if not header:
            i += 1
            continue
        idx = int(header.group(1))
        shortname = header.group(2).strip()
        driver = header.group(3).strip()
        longname = header.group(4).strip()
        extra = lines[i + 1].strip() if i + 1 < len(lines) else ""
        combined = " ".join([shortname, driver, longname, extra]).lower()
        is_usb = ("usb-audio" in combined) or ("usb" in extra.lower())
        desc = "{} [{}]".format(longname or shortname, shortname)
        cards.append((idx, desc, is_usb))
        i += 2
    return cards


def _card_index_from_device(device: str) -> Optional[int]:
    """'hw:N,X' / 'plughw:N,X' → N"""
    match = re.match(r"(?:plug)?hw:(\d+)", device)
    return int(match.group(1)) if match else None


def _test_arecord_capture(device: str, rate: int, duration_s: int = DETECT_DURATION_S) -> bool:
    """
    Lance `arecord` en test : capture `duration_s` secondes à `rate` Hz,
    vérifie que le subprocess sort 0 ET que les octets capturés ont du
    signal (pas juste du silence, sinon ça retient une carte loopback).
    """
    cmd = [
        "arecord",
        "-D", device,
        "-r", str(rate),
        "-c", str(CHANNELS),
        "-f", "S16_LE",
        "-t", "raw",
        "-d", str(duration_s),
        "-q",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=duration_s + 5,
        )
    except subprocess.TimeoutExpired:
        log.warning("  %s @ %dHz: timeout test (arecord bloqué)", device, rate)
        return False
    except Exception as exc:
        log.warning("  %s @ %dHz: exception: %s", device, rate, exc)
        return False

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", "replace").strip().replace("\n", " | ")
        log.warning("  %s @ %dHz ko (code=%d): %s",
                    device, rate, result.returncode, err or "(pas de stderr)")
        return False

    # Signal check: un device loopback/HDMI retourne souvent que des zéros.
    # Si on ne voit aucun octet non-nul sur toute la capture, on rejette.
    data = result.stdout
    expected = rate * BYTES_PER_SAMPLE * duration_s
    if len(data) < expected * 0.5:
        log.warning("  %s @ %dHz ko: trop peu d'octets (%d/%d)",
                    device, rate, len(data), expected)
        return False
    if not any(b != 0 for b in data[::1024]):
        log.warning("  %s @ %dHz ko: silence pur (probable loopback/HDMI sans mic)",
                    device, rate)
        return False
    return True


def _disable_usb_autosuspend(card_idx: int) -> None:
    """
    Empêche le kernel USB de suspendre le mic (EIO intermittents sur
    Jetson Nano, Pi). Best-effort : ignore si pas root ou pas USB.
    """
    try:
        with open("/sys/module/usbcore/parameters/autosuspend", "w") as f:
            f.write("-1\n")
    except OSError:
        pass

    try:
        path = os.path.realpath("/sys/class/sound/card{}".format(card_idx))
    except OSError:
        return
    while path and path != "/":
        if os.path.exists(os.path.join(path, "idVendor")):
            ctrl = os.path.join(path, "power", "control")
            try:
                with open(ctrl, "w") as f:
                    f.write("on\n")
                log.info("USB autosuspend désactivé pour card%d (%s)", card_idx, path)
            except OSError:
                pass
            return
        path = os.path.dirname(path)


def find_capture_device() -> Optional[Tuple[str, int]]:
    """
    Détecte la meilleure (device, rate) via arecord.
    Priorité USB > onboard. Pour chaque carte, teste les rates préférés
    avec un vrai arecord de quelques secondes (robuste).
    """
    cards = enumerate_cards()
    if not cards:
        log.error("Aucune carte son détectée (/proc/asound/cards vide)")
        return None

    log.info("Cartes détectées:")
    for idx, desc, is_usb in cards:
        log.info("  [%d] %s%s", idx, desc, " (USB)" if is_usb else "")

    usb_cards = [c for c in cards if c[2]]
    if not usb_cards:
        log.error("Aucune carte USB détectée. Un satellite doit avoir un micro USB.")
        log.error("Branche un micro USB, ou passe --device manuellement si tu es sûr.")
        return None

    for idx, desc, _ in usb_cards:
        _disable_usb_autosuspend(idx)
        device = "hw:{},0".format(idx)
        log.info("Test %s (USB) — %ds par rate...", desc, DETECT_DURATION_S)
        for rate in PREFERRED_RATES:
            if _test_arecord_capture(device, rate):
                log.info("Micro sélectionné: %s → %s @ %dHz", desc, device, rate)
                return (device, rate)
        log.warning("  card %d (%s) → aucun rate préféré utilisable", idx, desc)

    log.error("Aucun micro USB ne capture correctement. Pistes :")
    log.error("  - essayer un autre câble USB")
    log.error("  - brancher sur un autre port USB (ou hub alimenté)")
    log.error("  - vérifier que le micro marche sur un autre appareil")
    log.error("Pour forcer une carte onboard : --device hw:N,0")
    return None


def detect_audio() -> Optional[dict]:
    """
    Returns {"device": "hw:2,0", "native_rate": 16000, "description": "TONOR ..."} or None.
    Wrapper clean autour de find_capture_device() qui retourne un dict Pydantic-compatible.
    """
    result = find_capture_device()
    if result is None:
        return None
    device, rate = result
    # Trouver la desc via enumerate_cards
    idx = _card_index_from_device(device)
    desc = "unknown"
    if idx is not None:
        for c_idx, c_desc, _ in enumerate_cards():
            if c_idx == idx:
                desc = c_desc
                break
    return {"device": device, "native_rate": rate, "description": desc}
