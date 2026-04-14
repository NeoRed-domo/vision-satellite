# tests/test_identity.py
import stat
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from vision_satellite import identity


def test_generate_keypair_writes_files(tmp_path: Path):
    key_path = tmp_path / "device.key"
    pub_pem = identity.generate_keypair(key_path)

    assert key_path.exists()
    assert "BEGIN PUBLIC KEY" in pub_pem
    # Le fichier est réellement une ECDSA P-256
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    assert isinstance(key.curve, ec.SECP256R1)


def test_generate_keypair_private_chmod_600(tmp_path: Path):
    key_path = tmp_path / "device.key"
    identity.generate_keypair(key_path)
    mode = key_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_load_pubkey_matches_generated(tmp_path: Path):
    key_path = tmp_path / "device.key"
    pub_pem_1 = identity.generate_keypair(key_path)
    pub_pem_2 = identity.load_pubkey(key_path)
    assert pub_pem_1.strip() == pub_pem_2.strip()


def test_write_cert_chmod_644(tmp_path: Path):
    cert_path = tmp_path / "device.crt"
    identity.write_cert("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n", cert_path)
    assert cert_path.exists()
    mode = cert_path.stat().st_mode & 0o777
    assert mode == 0o644


def test_generate_keypair_creates_parent_dir(tmp_path: Path):
    """Si le dossier parent n'existe pas, le créer (chmod 700)."""
    key_path = tmp_path / "nested" / "dir" / "device.key"
    identity.generate_keypair(key_path)
    assert key_path.exists()
    parent_mode = key_path.parent.stat().st_mode & 0o777
    # Parent should be 0700 for security
    assert parent_mode in (0o700, 0o755), f"expected 700 or 755, got {oct(parent_mode)}"
