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

# Audio parameters (must match Vision's audio_tcp.py expectations)
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 1280  # 80ms at 16kHz
FORMAT_BITS = 16

# Reconnect settings
RECONNECT_DELAY_S = 2
MAX_RECONNECT_DELAY_S = 30


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


def _can_capture(device: str, attempts: int = 5) -> bool:
    """
    Open the device and try several reads. A real mic will deliver at
    least one valid chunk; a bogus device (e.g. Tegra ADMAIF loopback)
    will systematically fail with EPIPE / return empty data.
    """
    import alsaaudio
    pcm = None
    try:
        pcm = alsaaudio.PCM(
            type=alsaaudio.PCM_CAPTURE,
            mode=alsaaudio.PCM_NORMAL,
            device=device,
        )
        pcm.setrate(SAMPLE_RATE)
        pcm.setchannels(CHANNELS)
        pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        pcm.setperiodsize(CHUNK_SIZE)
        for _ in range(attempts):
            try:
                length, data = pcm.read()
                if length > 0 and data:
                    return True
            except Exception:
                pass
        return False
    except Exception as exc:
        log.debug("%s non utilisable: %s", device, exc)
        return False
    finally:
        if pcm is not None:
            try:
                pcm.close()
            except Exception:
                pass


def find_capture_device() -> Optional[str]:
    """
    Auto-detect the best working capture device.

    Strategy:
      1. Enumerate ALSA cards with USB tagging from /proc/asound/cards
      2. Try USB cards first (priority 0), then onboard (priority 1)
      3. For each, actually open + read a chunk to verify it works
      4. Return the first device that passes the read test
    """
    cards = enumerate_cards()
    if not cards:
        log.error("Aucune carte son détectée (/proc/asound/cards vide ou illisible)")
        return None

    log.info("Cartes détectées:")
    for idx, desc, is_usb in cards:
        log.info("  [%d] %s%s", idx, desc, " (USB)" if is_usb else "")

    candidates = sorted(cards, key=lambda c: (0 if c[2] else 1, c[0]))

    # plughw: lets ALSA resample/convert so we can ask 16kHz mono int16 even
    # when the device only supports e.g. 48kHz natively (common for USB mics).
    # We try plughw first, then fall back to hw as a last resort.
    for idx, desc, is_usb in candidates:
        kind = "USB" if is_usb else "onboard"
        for prefix in ("plughw", "hw"):
            device = "{}:{},0".format(prefix, idx)
            if _can_capture(device):
                log.info("Micro sélectionné: %s (%s) → %s", desc, kind, device)
                return device
        log.warning("  card %d (%s) → aucune capture utilisable (hw/plughw)", idx, desc)

    log.error("Aucune carte son capable de capturer en 16kHz mono int16 trouvée.")
    log.error("Branche un micro USB ou passe --device manuellement.")
    return None


# Backward-compatible alias
find_usb_mic = find_capture_device


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

    print("Cartes ALSA:")
    for idx, desc, is_usb in cards:
        tag = " (USB)" if is_usb else ""
        device = "hw:{},0".format(idx)
        works = _can_capture(device)
        status = "OK" if works else "KO"
        print("  [{}] {}{}  → {}  [{}]".format(idx, desc, tag, device, status))


def open_capture(device: str):
    """
    Open ALSA capture device at 16kHz mono int16.
    Returns alsaaudio.PCM instance.
    """
    import alsaaudio

    pcm = alsaaudio.PCM(
        type=alsaaudio.PCM_CAPTURE,
        mode=alsaaudio.PCM_NORMAL,
        device=device,
    )
    pcm.setrate(SAMPLE_RATE)
    pcm.setchannels(CHANNELS)
    pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
    pcm.setperiodsize(CHUNK_SIZE)

    log.info("Capture ouverte: %s (%dHz, %dch, int16, chunk=%d)",
             device, SAMPLE_RATE, CHANNELS, CHUNK_SIZE)
    return pcm


def connect(host: str, port: int) -> socket.socket:
    """
    Connect to Vision server and send the audio header.
    Returns connected socket with TCP_NODELAY enabled.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)
    sock.settimeout(10)
    sock.connect((host, port))

    # Send header: sample_rate (uint32) + chunk_size (uint32)
    header = struct.pack('<II', SAMPLE_RATE, CHUNK_SIZE)
    sock.sendall(header)

    log.info("Connecté à %s:%d — header envoyé (rate=%d, chunk=%d)",
             host, port, SAMPLE_RATE, CHUNK_SIZE)
    sock.settimeout(None)  # Blocking mode for streaming
    return sock


def stream(host: str, port: int, device: str):
    """
    Main streaming loop with auto-reconnect.
    Captures audio from ALSA and sends to Vision server.
    """
    pcm = open_capture(device)
    reconnect_delay = RECONNECT_DELAY_S
    sock = None

    while True:
        # Connect (with retry)
        if sock is None:
            try:
                sock = connect(host, port)
                reconnect_delay = RECONNECT_DELAY_S  # Reset backoff
                log.info("Streaming audio...")
            except (ConnectionRefusedError, OSError) as e:
                log.warning("Connexion échouée (%s), retry dans %ds...", e, reconnect_delay)
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY_S)
                continue

        # Capture + send
        try:
            length, data = pcm.read()
            if length > 0 and data:
                sock.sendall(data)
            elif length < 0:
                log.warning("ALSA overrun (length=%d), chunk perdu", length)
        except BrokenPipeError:
            log.warning("Connexion perdue (broken pipe), reconnexion...")
            sock = None
        except ConnectionResetError:
            log.warning("Connexion reset, reconnexion...")
            sock = None
        except OSError as e:
            log.warning("Erreur réseau: %s, reconnexion...", e)
            sock = None
        except Exception as e:
            log.error("Erreur inattendue: %s", e)
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None
            time.sleep(1)


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

    # Auto-detect device if not specified
    device = args.device
    if device is None:
        device = find_usb_mic()
        if device is None:
            log.error("Aucun micro USB détecté. Utilisez --device ou --list-devices.")
            sys.exit(1)

    log.info("Vision Audio Satellite")
    log.info("  Serveur: %s:%d", args.host, args.port)
    log.info("  Device:  %s", device)
    log.info("  Format:  %dHz, %dch, int16, chunk=%d (%dms)",
             SAMPLE_RATE, CHANNELS, CHUNK_SIZE, CHUNK_SIZE * 1000 // SAMPLE_RATE)

    try:
        stream(args.host, args.port, device)
    except KeyboardInterrupt:
        log.info("Arrêt (Ctrl+C)")


if __name__ == "__main__":
    main()
