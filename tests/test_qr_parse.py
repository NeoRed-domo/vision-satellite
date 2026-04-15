# tests/test_qr_parse.py
import pytest

from vision_satellite import qr_parse


VALID_FP = "a" * 64  # 64 hex chars


def test_parse_valid_uri():
    uri = f"vision-enroll://192.168.1.10:9444?token=xyz&fp={VALID_FP}&name=Salon&v=1"
    d = qr_parse.parse_enroll_uri(uri)
    assert d["host"] == "192.168.1.10"
    assert d["port"] == 9444
    assert d["token"] == "xyz"
    assert d["fingerprint"] == VALID_FP
    assert d["name"] == "Salon"
    assert d["version"] == 1


def test_parse_default_port():
    uri = f"vision-enroll://vision.local?token=xyz&fp={VALID_FP}"
    d = qr_parse.parse_enroll_uri(uri)
    assert d["port"] == 9444  # default


def test_parse_invalid_scheme_raises():
    uri = f"https://x?token=xyz&fp={VALID_FP}"
    with pytest.raises(ValueError, match="schéma"):
        qr_parse.parse_enroll_uri(uri)


def test_parse_malformed_fingerprint_raises():
    uri = "vision-enroll://x?token=xyz&fp=abc"  # 3 chars, pas 64
    with pytest.raises(ValueError, match="fingerprint"):
        qr_parse.parse_enroll_uri(uri)


def test_parse_missing_token_raises():
    uri = f"vision-enroll://x?fp={VALID_FP}"
    with pytest.raises(ValueError, match="token"):
        qr_parse.parse_enroll_uri(uri)
