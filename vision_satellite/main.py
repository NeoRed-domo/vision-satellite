"""Entry point CLI pour Vision Satellite.

Modes :
  --list-capabilities : détecte et affiche le hardware (JSON)
  --enroll <URI> : flow d'enrollment complet (keygen + POST + store cert)
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from pathlib import Path

from vision_satellite import enrollment, identity, qr_parse
from vision_satellite.capabilities.audio import detect_audio
from vision_satellite.capabilities.bluetooth import detect_bluetooth
from vision_satellite.capabilities.camera import detect_camera
from vision_satellite.capabilities.zigbee import detect_zigbee
from vision_satellite.capabilities.zwave import detect_zwave

log = logging.getLogger("vision.satellite.main")

_SATELLITE_VERSION = "1.0.0"
_DEFAULT_KEY_PATH = Path("/opt/vision-satellite/device.key")
_DEFAULT_CERT_PATH = Path("/opt/vision-satellite/device.crt")
_DEFAULT_CA_PATH = Path("/opt/vision-satellite/vision-ca.crt")


def detect_all_capabilities() -> dict:
    return {
        "audio": detect_audio(),
        "camera": detect_camera(),
        "bluetooth": detect_bluetooth(),
        "zigbee": detect_zigbee(),
        "zwave": detect_zwave(),
    }


def list_capabilities() -> None:
    caps = detect_all_capabilities()
    print(json.dumps(caps, indent=2))


def _get_hostname() -> str:
    return socket.gethostname()


def _get_mac_addresses() -> list:
    """Lit /sys/class/net/*/address — filtre loopback et MAC nulles."""
    macs = []
    net_dir = Path("/sys/class/net")
    if not net_dir.exists():
        return macs
    for iface in sorted(net_dir.iterdir()):
        if iface.name == "lo":
            continue
        addr_file = iface / "address"
        if not addr_file.exists():
            continue
        try:
            addr = addr_file.read_text().strip().lower()
            if addr and addr != "00:00:00:00:00:00":
                macs.append(addr)
        except OSError:
            continue
    return macs


def do_enroll(
    uri: str,
    key_path: Path,
    cert_path: Path,
    ca_cert_path: Path,
    hostname: str | None = None,
    mac_addresses: list | None = None,
) -> int:
    """Flow complet. Returns 0 on success, nonzero on failure."""
    # 1. Parse URI
    try:
        parsed = qr_parse.parse_enroll_uri(uri)
    except ValueError as exc:
        log.error("URI d'enrollment invalide: %s", exc)
        return 2

    # 2. Génère keypair
    try:
        pubkey_pem = identity.generate_keypair(key_path)
    except Exception as exc:  # chmod, disk full, etc.
        log.error("keygen échoué: %s", exc)
        return 3

    # 3. Détecte capabilities
    capabilities = detect_all_capabilities()
    log.info("capabilities détectées: %s", {k: bool(v) for k, v in capabilities.items()})

    # 4. Hostname + MACs
    effective_hostname = hostname or _get_hostname()
    effective_macs = mac_addresses or _get_mac_addresses()
    if not effective_macs:
        log.error("aucune MAC address trouvée")
        return 4

    # 5. POST /enroll
    try:
        result = enrollment.enroll(
            host=parsed["host"],
            port=parsed["port"],
            token=parsed["token"],
            expected_fingerprint=parsed["fingerprint"],
            pubkey_pem=pubkey_pem,
            hostname=effective_hostname,
            mac_addresses=effective_macs,
            capabilities=capabilities,
            satellite_version=_SATELLITE_VERSION,
        )
    except enrollment.EnrollmentError as exc:
        log.error("enrollment refusé: %s", exc)
        return 5

    # 6. Store certs
    try:
        identity.write_cert(result["device_cert_pem"], cert_path)
        identity.write_cert(result["vision_ca_pem"], ca_cert_path)
    except OSError as exc:
        log.error("écriture certs échouée: %s", exc)
        return 6

    log.info(
        "enrollment OK — satellite_id=%s, runtime_uri=%s, cert_expires_at=%s",
        result["satellite_id"], result["runtime_uri"], result["cert_expires_at"],
    )
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vision-satellite",
        description="Vision Satellite — Stream audio/video/RF to Vision server",
    )
    parser.add_argument("--list-capabilities", action="store_true",
                        help="Détecte et affiche les capabilities (JSON)")
    parser.add_argument("--enroll", metavar="URI",
                        help="Flow d'enrollment (URI vision-enroll://...)")
    parser.add_argument("--key-path", default=str(_DEFAULT_KEY_PATH))
    parser.add_argument("--cert-path", default=str(_DEFAULT_CERT_PATH))
    parser.add_argument("--ca-path", default=str(_DEFAULT_CA_PATH))
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_capabilities:
        list_capabilities()
        return 0

    if args.enroll:
        return do_enroll(
            uri=args.enroll,
            key_path=Path(args.key_path),
            cert_path=Path(args.cert_path),
            ca_cert_path=Path(args.ca_path),
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
