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
import socket
import struct
import sys
import time

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


def find_usb_mic(preferred_names: list[str] | None = None) -> str | None:
    """
    Auto-detect USB microphone ALSA device.

    Scans /proc/asound/cards for USB audio devices.
    Returns ALSA device string (e.g., 'hw:1,0') or None.
    """
    preferred = preferred_names or ["TONOR", "TM20", "USB", "Microphone", "Mic"]

    try:
        import alsaaudio
        cards = alsaaudio.cards()
        log.debug("ALSA cards: %s", cards)

        # Try preferred names first
        for i, card in enumerate(cards):
            for name in preferred:
                if name.lower() in card.lower():
                    device = f"hw:{i},0"
                    log.info("Micro détecté: '%s' → %s", card, device)
                    return device

        # Fallback: first non-default card (usually USB)
        if len(cards) > 1:
            device = f"hw:{1},0"
            log.info("Micro fallback: '%s' → %s", cards[1], device)
            return device

        # Last resort: default device
        if cards:
            log.info("Micro par défaut: '%s' → default", cards[0])
            return "default"

    except Exception as e:
        log.warning("Détection auto échouée: %s", e)

    return None


def list_devices():
    """List available ALSA capture devices."""
    try:
        import alsaaudio
        print("Cartes ALSA disponibles:")
        for i, card in enumerate(alsaaudio.cards()):
            print(f"  [{i}] {card} → hw:{i},0")
        print()
        print("Devices PCM capture:")
        for pcm in alsaaudio.pcms(alsaaudio.PCM_CAPTURE):
            print(f"  {pcm}")
    except ImportError:
        print("ERREUR: pyalsaaudio non installé. Lancez: pip3 install pyalsaaudio")
        sys.exit(1)


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
