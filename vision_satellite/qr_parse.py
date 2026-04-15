"""Parser des URI vision-enroll://HOST:PORT?token=XXX&fp=YYY&name=ZZZ&v=1"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_FP_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_PORT = 9443  # POST /enroll passe par Caddy (9443 = HTTPS public côté Vision)


def parse_enroll_uri(uri: str) -> dict:
    """Parse une URI vision-enroll://. Lève ValueError si invalide."""
    parsed = urlparse(uri)
    if parsed.scheme != "vision-enroll":
        raise ValueError(f"schéma invalide: {parsed.scheme!r} (attendu vision-enroll)")

    if not parsed.hostname:
        raise ValueError("host manquant dans l'URI")

    qs = parse_qs(parsed.query)
    token = qs.get("token", [None])[0]
    fp = qs.get("fp", [None])[0]
    name = qs.get("name", [""])[0]
    version_str = qs.get("v", ["1"])[0]

    if not token:
        raise ValueError("token manquant dans l'URI")
    if not fp or not _FP_RE.match(fp):
        raise ValueError(f"fingerprint invalide (attendu 64 hex, got {fp!r})")

    try:
        version = int(version_str)
    except ValueError:
        raise ValueError(f"version invalide: {version_str!r}")

    return {
        "host": parsed.hostname,
        "port": parsed.port or _DEFAULT_PORT,
        "token": token,
        "fingerprint": fp,
        "name": name,
        "version": version,
    }
