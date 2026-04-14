"""Client enrollment — POST /api/satellites/enroll avec TLS cert pinning."""
from __future__ import annotations

import hashlib
import json as _json
import logging
import socket
import ssl
from http.client import HTTPSConnection
from typing import Protocol
from urllib.parse import urlparse

log = logging.getLogger("vision.satellite.enrollment")

PROTOCOL_VERSION = 1


class EnrollmentError(Exception):
    """Levée si l'enrollment échoue (fingerprint mismatch, 403, réseau, etc.)."""


class _Response(Protocol):
    status_code: int
    text: str

    def json(self) -> dict: ...


class _Transport(Protocol):
    def get_peer_fingerprint(self, host: str, port: int) -> str: ...
    def post(self, url: str, json: dict) -> _Response: ...


class _HttpsTransport:
    """Transport réel : HTTPSConnection + récupération du peer cert."""

    def get_peer_fingerprint(self, host: str, port: int) -> str:
        ctx = ssl._create_unverified_context()  # noqa: S323 — on fait notre propre pinning
        sock = socket.create_connection((host, port), timeout=10)
        try:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                der = ssock.getpeercert(binary_form=True)
        finally:
            try:
                sock.close()
            except Exception:
                pass
        return hashlib.sha256(der).hexdigest()

    def post(self, url: str, json: dict):
        parsed = urlparse(url)
        conn = HTTPSConnection(
            parsed.hostname, parsed.port or 443,
            context=ssl._create_unverified_context(),  # pinning déjà fait
            timeout=15,
        )
        try:
            body = _json.dumps(json).encode("utf-8")
            conn.request("POST", parsed.path, body=body, headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            status = resp.status
        finally:
            conn.close()

        class _R:
            status_code = status
            text = raw

            def json(self):
                return _json.loads(raw) if raw else {}

        return _R()


def enroll(
    host: str,
    port: int,
    token: str,
    expected_fingerprint: str,
    pubkey_pem: str,
    hostname: str,
    mac_addresses: list,
    capabilities: dict,
    satellite_version: str,
    *,
    verify_tls: bool = True,
    _transport=None,
) -> dict:
    """
    POST /api/satellites/enroll avec cert pinning.
    Returns {satellite_id, device_cert_pem, vision_ca_pem, runtime_uri, cert_expires_at}
    Raises EnrollmentError on failure.
    """
    transport = _transport or _HttpsTransport()

    # 1. Pinning : vérifier le fp du cert TLS du serveur avant tout échange
    if verify_tls:
        actual_fp = transport.get_peer_fingerprint(host, port)
        if actual_fp != expected_fingerprint:
            log.error(
                "fingerprint mismatch: expected=%s actual=%s",
                expected_fingerprint,
                actual_fp,
            )
            raise EnrollmentError(
                f"MITM detected: fingerprint mismatch "
                f"(expected={expected_fingerprint}, got={actual_fp})"
            )

    # 2. POST payload
    payload = {
        "token": token,
        "hostname": hostname,
        "mac_addresses": list(mac_addresses),
        "pubkey_pem": pubkey_pem,
        "capabilities": capabilities,
        "protocol_version": PROTOCOL_VERSION,
        "satellite_version": satellite_version,
    }
    url = f"https://{host}:{port}/api/satellites/enroll"
    resp = transport.post(url, json=payload)

    if resp.status_code != 200:
        raise EnrollmentError(
            f"enrollment refusé (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    return resp.json()
