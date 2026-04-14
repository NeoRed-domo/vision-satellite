"""Satellite identity — ECDSA P-256 keypair + cert storage."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def generate_keypair(key_path: Path) -> str:
    """
    Génère une paire ECDSA P-256, écrit la clé privée dans key_path
    (chmod 600), crée le dossier parent si nécessaire, retourne la
    pubkey PEM (str).
    """
    key_path = Path(key_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    # S'assurer que le dossier parent est protégé (0700) seulement si on vient de le créer
    # — sinon ne pas toucher aux perms existantes
    try:
        if not any(key_path.parent.iterdir()):  # dossier vide = on vient de le créer
            os.chmod(key_path.parent, 0o700)
    except (OSError, PermissionError):
        pass

    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(priv_pem)
    os.chmod(key_path, 0o600)

    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return pub_pem


def load_pubkey(key_path: Path) -> str:
    """Charge la clé privée depuis key_path, retourne la pubkey PEM."""
    priv = serialization.load_pem_private_key(
        Path(key_path).read_bytes(), password=None
    )
    if not isinstance(priv, ec.EllipticCurvePrivateKey):
        raise TypeError(f"{key_path} n'est pas une clé ECDSA")
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def write_cert(cert_pem: str, cert_path: Path) -> None:
    """Écrit un cert PEM dans cert_path (chmod 644)."""
    cert_path = Path(cert_path)
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_text(cert_pem, encoding="utf-8")
    os.chmod(cert_path, 0o644)
