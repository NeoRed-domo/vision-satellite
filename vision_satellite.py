#!/usr/bin/env python3
"""
Vision Satellite — Stream mic audio to Vision server via TCP.

Capture via arecord subprocess (approche Wyoming Satellite) : plus
robuste que pyalsaaudio sur les USB mics, gère mieux les EIO
intermittents, utilise les buffers par défaut bien tunés d'ALSA.

Protocole TCP:
  1. Connect au serveur
  2. Header 8 octets : sample_rate (uint32 LE) + chunk_size (uint32 LE)
  3. Stream continu de chunks raw int16 PCM little-endian

Usage:
  python3 vision_satellite.py --host 192.168.1.100
  python3 vision_satellite.py --host 192.168.1.100 --device hw:2,0
  python3 vision_satellite.py --list-devices
"""
import argparse
import logging
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from typing import List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vision.satellite")

CHANNELS = 1
CHUNK_MS = 80  # chunk duration (80ms → latence capture)
BYTES_PER_SAMPLE = 2  # S16_LE = 2 octets

# Rates préférés — on retient le premier que le mic supporte en natif.
# 16 kHz = pipeline Vision (zéro resample serveur). 48 kHz = natif le
# plus courant chez les USB mics cheap.
PREFERRED_RATES = [16000, 32000, 44100, 48000]

RECONNECT_DELAY_S = 2
MAX_RECONNECT_DELAY_S = 30

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


def list_devices():
    """Liste les cartes avec leur rate retenu (détection complète)."""
    cards = enumerate_cards()
    if not cards:
        print("Aucune carte son détectée.")
        return
    print("Cartes ALSA (rate retenu = premier testé OK via arecord) :")
    for idx, desc, is_usb in cards:
        tag = " (USB)" if is_usb else ""
        device = "hw:{},0".format(idx)
        rate = next((r for r in PREFERRED_RATES if _test_arecord_capture(device, r, duration_s=2)), None)
        status = "OK @ {}Hz".format(rate) if rate else "KO"
        print("  [{}] {}{}  → {}  [{}]".format(idx, desc, tag, device, status))


# ============================================================
# Streaming (arecord subprocess → TCP)
# ============================================================

def spawn_arecord(device: str, rate: int) -> subprocess.Popen:
    """Lance arecord en streaming continu sur stdout (raw S16_LE)."""
    cmd = [
        "arecord",
        "-D", device,
        "-r", str(rate),
        "-c", str(CHANNELS),
        "-f", "S16_LE",
        "-t", "raw",
        "-q",
    ]
    log.info("arecord: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )


def connect(host: str, port: int, rate: int, chunk: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)
    sock.settimeout(10)
    sock.connect((host, port))
    sock.sendall(struct.pack('<II', rate, chunk))
    log.info("Connecté à %s:%d — header envoyé (rate=%d, chunk=%d)",
             host, port, rate, chunk)
    sock.settimeout(None)
    return sock


def _kill_proc(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass


def stream(host: str, port: int, device: str, rate: int):
    """
    Boucle principale : arecord en subprocess, on lit des chunks de
    stdout et on les envoie en TCP. Erreurs ALSA (arecord meurt) et
    erreurs réseau (socket cassée) sont traitées indépendamment.
    """
    chunk_smp = chunk_samples(rate)
    chunk_b = chunk_bytes(rate)
    proc: Optional[subprocess.Popen] = None
    sock: Optional[socket.socket] = None
    reconnect_delay = RECONNECT_DELAY_S

    while True:
        # (Re)lance arecord si mort / jamais lancé
        if proc is None or proc.poll() is not None:
            if proc is not None:
                err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
                log.warning("arecord a quitté (code=%s): %s", proc.returncode, err or "(pas de stderr)")
                _kill_proc(proc)
                time.sleep(0.5)
            proc = spawn_arecord(device, rate)

        # (Re)connect socket
        if sock is None:
            try:
                sock = connect(host, port, rate, chunk_smp)
                reconnect_delay = RECONNECT_DELAY_S
                log.info("Streaming audio...")
            except (ConnectionRefusedError, OSError) as exc:
                log.warning("Connexion échouée (%s), retry dans %ds...", exc, reconnect_delay)
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY_S)
                continue

        # Lit un chunk depuis arecord
        try:
            data = proc.stdout.read(chunk_b)
        except Exception as exc:
            log.warning("Lecture arecord impossible (%s), relance subprocess", exc)
            _kill_proc(proc)
            proc = None
            continue

        if not data:
            # arecord a fermé stdout → il est mort
            log.warning("arecord stdout EOF — subprocess mort, relance")
            _kill_proc(proc)
            proc = None
            continue

        # Envoie sur la socket
        try:
            sock.sendall(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            log.warning("Socket perdue (%s), reconnexion...", exc.__class__.__name__)
            try:
                sock.close()
            except Exception:
                pass
            sock = None
        except OSError as exc:
            log.warning("Erreur réseau: %s, reconnexion...", exc)
            try:
                sock.close()
            except Exception:
                pass
            sock = None


# ============================================================
# Entry point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Vision Satellite — Stream mic to Vision server via TCP",
    )
    parser.add_argument("--host", required="--list-devices" not in sys.argv,
                        help="IP du serveur Vision")
    parser.add_argument("--port", type=int, default=9999,
                        help="Port TCP (défaut: 9999)")
    parser.add_argument("--device", default=None,
                        help="Device ALSA (ex: hw:2,0). Auto-détecté si omis.")
    parser.add_argument("--list-devices", action="store_true",
                        help="Liste les devices avec leur rate natif")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Logs détaillés")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Vérifie la présence d'arecord (alsa-utils)
    if shutil.which("arecord") is None:
        log.error("arecord introuvable. Installe alsa-utils : sudo apt install alsa-utils")
        sys.exit(1)

    if args.list_devices:
        list_devices()
        return

    if args.device is None:
        found = find_capture_device()
        if found is None:
            log.error("Détection auto échouée. --list-devices pour diagnostic.")
            sys.exit(1)
        device, rate = found
    else:
        device = args.device
        idx = _card_index_from_device(device)
        if idx is not None:
            _disable_usb_autosuspend(idx)
        rate = next((r for r in PREFERRED_RATES if _test_arecord_capture(device, r)), None)
        if rate is None:
            log.error("%s ne supporte aucun rate préféré: %s",
                      device, PREFERRED_RATES)
            sys.exit(1)
        log.info("%s retenu @ %dHz (natif)", device, rate)

    chunk_smp = chunk_samples(rate)
    log.info("Vision Satellite")
    log.info("  Serveur: %s:%d", args.host, args.port)
    log.info("  Device:  %s", device)
    log.info("  Format:  %dHz, %dch, int16, chunk=%d (%dms)",
             rate, CHANNELS, chunk_smp, CHUNK_MS)

    try:
        stream(args.host, args.port, device, rate)
    except KeyboardInterrupt:
        log.info("Arrêt (Ctrl+C)")


if __name__ == "__main__":
    main()
