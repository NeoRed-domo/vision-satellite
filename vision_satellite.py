#!/usr/bin/env python3
"""
Vision Audio Satellite — Stream mic audio to Vision server via TCP.

Captures from a USB microphone at 16kHz mono int16 using ALSA directly,
and streams raw PCM chunks to the Vision server. No resample, no codec,
minimal latency (~80ms chunk + <1ms TCP).

Protocol:
  1. Connect TCP to Vision server
  2. Send 8-byte header: sample_rate (uint32 LE) + chunk_size (uint32 LE)
  3. Stream raw int16 PCM chunks continuously

Usage:
  python3 vision_satellite.py --host 192.168.1.100 --port 9999
  python3 vision_satellite.py --host 192.168.1.100 --device hw:1,0
  python3 vision_satellite.py --host 192.168.1.100 --list-devices
"""
import argparse
import logging
import os
import re
import socket
import struct
import sys
import time
from typing import List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vision.satellite")

# Audio parameters (the server resamples to its pipeline rate if needed)
CHANNELS = 1
CHUNK_MS = 80  # chunk duration — determines latency
FORMAT_BITS = 16

# Preferred native rates, in order. We pick the first one the mic
# supports natively. 16 kHz is the pipeline rate → zero resample on
# the server. 48 kHz is the most common native rate for USB mics.
PREFERRED_RATES = [16000, 32000, 44100, 48000]

# Reconnect settings
RECONNECT_DELAY_S = 2
MAX_RECONNECT_DELAY_S = 30


def chunk_size(rate: int) -> int:
    return rate * CHUNK_MS // 1000


def enumerate_cards() -> List[Tuple[int, str, bool]]:
    """
    Parse /proc/asound/cards and return [(index, description, is_usb), ...].

    Each entry in the file looks like:
      2 [Device         ]: USB-Audio - TONOR TM20 Audio Device
                            TONOR TM20 Audio Device at usb-...
    We keep both shortname and longname, and mark USB cards via the
    driver field (USB-Audio) or explicit 'usb' in the longname.
    """
    cards = []
    try:
        with open("/proc/asound/cards") as f:
            lines = f.read().splitlines()
    except OSError:
        return cards

    i = 0
    while i < len(lines):
        # First line: " N [shortname      ]: driver - longname"
        header = re.match(r"\s*(\d+)\s*\[([^\]]+)\]\s*:\s*(\S+)\s*-\s*(.*)", lines[i])
        if not header:
            i += 1
            continue
        idx = int(header.group(1))
        shortname = header.group(2).strip()
        driver = header.group(3).strip()
        longname = header.group(4).strip()
        # Second line: usually a full hardware path (may contain "usb-...")
        extra = lines[i + 1].strip() if i + 1 < len(lines) else ""
        combined = " ".join([shortname, driver, longname, extra]).lower()
        is_usb = ("usb-audio" in combined) or ("usb" in extra.lower())
        desc = "{} [{}]".format(longname or shortname, shortname)
        cards.append((idx, desc, is_usb))
        i += 2
    return cards


def _disable_usb_autosuspend(card_idx: int) -> None:
    """
    Empêche le kernel USB de mettre en veille le micro (cause
    classique des EIO toutes les ~10s sur Jetson Nano / Pi).

    - désactive le timer global d'autosuspend (/sys/module/usbcore)
    - force power/control=on sur le device USB derrière card{N}

    Les deux opérations sont best-effort : on log à debug si elles
    échouent (permissions, chemin absent, device non USB...).
    """
    try:
        with open("/sys/module/usbcore/parameters/autosuspend", "w") as f:
            f.write("-1\n")
        log.debug("USB autosuspend global désactivé")
    except OSError as exc:
        log.debug("autosuspend global non modifiable: %s", exc)

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
            except OSError as exc:
                log.warning("Impossible de forcer power/control=on (%s): %s", ctrl, exc)
            return
        path = os.path.dirname(path)
    log.debug("card%d n'est pas derrière un device USB (pas d'autosuspend à gérer)", card_idx)


def _card_index_from_device(device: str) -> Optional[int]:
    """Extract card index from 'hw:N,X' / 'plughw:N,X' strings."""
    match = re.match(r"(?:plug)?hw:(\d+)", device)
    return int(match.group(1)) if match else None


def _try_capture(device: str, rate: int, attempts: int = 5) -> bool:
    """
    Try to open device at exactly `rate` (native, no resample) and read
    a chunk. Returns True only if setrate() confirms the rate and we
    get at least one valid chunk.
    """
    import alsaaudio
    pcm = None
    try:
        pcm = alsaaudio.PCM(
            type=alsaaudio.PCM_CAPTURE,
            mode=alsaaudio.PCM_NORMAL,
            device=device,
        )
        actual_rate = pcm.setrate(rate)
        if actual_rate != rate:
            log.debug("%s refuse %dHz (renvoie %dHz)", device, rate, actual_rate)
            return False
        pcm.setchannels(CHANNELS)
        pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        pcm.setperiodsize(chunk_size(rate))
        for _ in range(attempts):
            try:
                length, data = pcm.read()
                if length > 0 and data:
                    return True
            except Exception:
                pass
        return False
    except Exception as exc:
        log.debug("%s @ %dHz non utilisable: %s", device, rate, exc)
        return False
    finally:
        if pcm is not None:
            try:
                pcm.close()
            except Exception:
                pass


def find_capture_device() -> Optional[Tuple[str, int]]:
    """
    Auto-detect the best working capture device and its native rate.

    Strategy:
      1. Enumerate ALSA cards (USB-tagged from /proc/asound/cards)
      2. Prefer USB cards, then onboard
      3. For each card, try our preferred rates in order and pick the
         first one that actually captures (no resample, native hw:)
      4. Returns (device, rate) or None
    """
    cards = enumerate_cards()
    if not cards:
        log.error("Aucune carte son détectée (/proc/asound/cards vide ou illisible)")
        return None

    log.info("Cartes détectées:")
    for idx, desc, is_usb in cards:
        log.info("  [%d] %s%s", idx, desc, " (USB)" if is_usb else "")

    candidates = sorted(cards, key=lambda c: (0 if c[2] else 1, c[0]))

    for idx, desc, is_usb in candidates:
        kind = "USB" if is_usb else "onboard"
        if is_usb:
            # Désactive l'autosuspend AVANT le test — sinon le premier
            # read peut tomber en EIO sur un device déjà suspendu.
            _disable_usb_autosuspend(idx)
        device = "hw:{},0".format(idx)
        for rate in PREFERRED_RATES:
            if _try_capture(device, rate):
                log.info("Micro sélectionné: %s (%s) → %s @ %dHz",
                         desc, kind, device, rate)
                return (device, rate)
        log.warning("  card %d (%s) → aucun rate préféré utilisable en capture", idx, desc)

    log.error("Aucune carte son capable de capturer en mono int16 trouvée.")
    log.error("Rates testés: %s", PREFERRED_RATES)
    log.error("Branche un micro USB ou passe --device manuellement.")
    return None


def list_devices():
    """List available ALSA cards with USB tagging and capture capability."""
    try:
        import alsaaudio  # noqa: F401
    except ImportError:
        print("ERREUR: pyalsaaudio non installé. Lancez: pip3 install pyalsaaudio")
        sys.exit(1)

    cards = enumerate_cards()
    if not cards:
        print("Aucune carte son détectée.")
        return

    print("Cartes ALSA (rate natif retenu = premier qui marche) :")
    for idx, desc, is_usb in cards:
        tag = " (USB)" if is_usb else ""
        device = "hw:{},0".format(idx)
        working_rate = next((r for r in PREFERRED_RATES if _try_capture(device, r)), None)
        status = "OK @ {}Hz".format(working_rate) if working_rate else "KO"
        print("  [{}] {}{}  → {}  [{}]".format(idx, desc, tag, device, status))


def open_capture(device: str, rate: int):
    """
    Open ALSA capture device at the given native rate, mono int16.
    Returns alsaaudio.PCM instance.
    """
    import alsaaudio

    pcm = alsaaudio.PCM(
        type=alsaaudio.PCM_CAPTURE,
        mode=alsaaudio.PCM_NORMAL,
        device=device,
    )
    actual_rate = pcm.setrate(rate)
    if actual_rate != rate:
        log.warning("setrate(%d) a renvoyé %d — on utilise %d",
                    rate, actual_rate, actual_rate)
        rate = actual_rate
    pcm.setchannels(CHANNELS)
    pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
    chunk = chunk_size(rate)
    pcm.setperiodsize(chunk)

    log.info("Capture ouverte: %s (%dHz, %dch, int16, chunk=%d)",
             device, rate, CHANNELS, chunk)
    return pcm


def connect(host: str, port: int, rate: int, chunk: int) -> socket.socket:
    """
    Connect to Vision server and send the audio header.
    Returns connected socket with TCP_NODELAY enabled.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)
    sock.settimeout(10)
    sock.connect((host, port))

    header = struct.pack('<II', rate, chunk)
    sock.sendall(header)

    log.info("Connecté à %s:%d — header envoyé (rate=%d, chunk=%d)",
             host, port, rate, chunk)
    sock.settimeout(None)
    return sock


def _close_pcm(pcm):
    if pcm is None:
        return
    try:
        pcm.close()
    except Exception:
        pass


def stream(host: str, port: int, device: str, rate: int):
    """
    Main streaming loop. ALSA and network errors are handled
    separately so a USB glitch doesn't reconnect the socket (and
    vice versa).
    """
    pcm = open_capture(device, rate)
    chunk = chunk_size(rate)
    sock = None
    reconnect_delay = RECONNECT_DELAY_S

    while True:
        # (Re)connect socket
        if sock is None:
            try:
                sock = connect(host, port, rate, chunk)
                reconnect_delay = RECONNECT_DELAY_S
                log.info("Streaming audio...")
            except (ConnectionRefusedError, OSError) as exc:
                log.warning("Connexion échouée (%s), retry dans %ds...", exc, reconnect_delay)
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY_S)
                continue

        # Capture
        try:
            length, data = pcm.read()
        except Exception as exc:
            # ALSA fault (USB unplug, overrun unhandled, etc.)
            log.warning("ALSA erreur (%s) — réouverture de la capture", exc)
            _close_pcm(pcm)
            time.sleep(0.2)
            try:
                pcm = open_capture(device, rate)
            except Exception as reopen_exc:
                log.error("Réouverture impossible: %s — retry dans 2s", reopen_exc)
                time.sleep(2)
            continue

        if length <= 0 or not data:
            if length < 0:
                log.debug("ALSA overrun (length=%d)", length)
            continue

        # Send
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


def main():
    parser = argparse.ArgumentParser(
        description="Vision Audio Satellite — Stream mic to Vision server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  %(prog)s --host 192.168.1.100
  %(prog)s --host 192.168.1.100 --device hw:1,0
  %(prog)s --list-devices
        """,
    )
    parser.add_argument("--host", required="--list-devices" not in sys.argv,
                        help="Adresse IP du serveur Vision")
    parser.add_argument("--port", type=int, default=9999,
                        help="Port TCP du serveur Vision (défaut: 9999)")
    parser.add_argument("--device", default=None,
                        help="Device ALSA (ex: hw:1,0). Auto-détecté si omis.")
    parser.add_argument("--list-devices", action="store_true",
                        help="Liste les devices audio disponibles")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Logs détaillés")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_devices:
        list_devices()
        return

    # Auto-detect device + rate if not specified
    if args.device is None:
        found = find_capture_device()
        if found is None:
            log.error("Aucun micro USB utilisable. --list-devices pour diagnostiquer.")
            sys.exit(1)
        device, rate = found
    else:
        device = args.device
        idx = _card_index_from_device(device)
        if idx is not None:
            _disable_usb_autosuspend(idx)
        # Manual device: probe rates to find a native one that works
        rate = next((r for r in PREFERRED_RATES if _try_capture(device, r)), None)
        if rate is None:
            log.error("%s ne supporte aucun des rates %s en natif", device, PREFERRED_RATES)
            sys.exit(1)
        log.info("%s retenu @ %dHz (natif)", device, rate)

    chunk = chunk_size(rate)
    log.info("Vision Satellite")
    log.info("  Serveur: %s:%d", args.host, args.port)
    log.info("  Device:  %s", device)
    log.info("  Format:  %dHz, %dch, int16, chunk=%d (%dms)",
             rate, CHANNELS, chunk, CHUNK_MS)

    try:
        stream(args.host, args.port, device, rate)
    except KeyboardInterrupt:
        log.info("Arrêt (Ctrl+C)")


if __name__ == "__main__":
    main()
