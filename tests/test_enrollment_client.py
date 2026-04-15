# tests/test_enrollment_client.py
import json

import pytest

from vision_satellite import enrollment


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeTransport:
    """Simule un client HTTP qui expose post(url, json=...) -> FakeResponse."""
    def __init__(self, status: int, body: dict, peer_fp: str = None):
        self.status = status
        self.body = body
        self.peer_fp = peer_fp  # ce que le transport "voit" côté serveur
        self.last_payload = None

    def get_peer_fingerprint(self, host: str, port: int) -> str:
        return self.peer_fp or "dummy-fp"

    def post(self, url: str, json: dict) -> FakeResponse:
        self.last_payload = json
        return FakeResponse(self.status, self.body)


VALID_FP = "a" * 64


def test_enroll_happy_path():
    transport = FakeTransport(
        status=200,
        body={
            "satellite_id": "sat-uuid-xxx",
            "device_cert_pem": "-----BEGIN CERTIFICATE-----\nxxx\n-----END CERTIFICATE-----\n",
            "vision_ca_pem": "-----BEGIN CERTIFICATE-----\nyyy\n-----END CERTIFICATE-----\n",
            "runtime_uri": "mtls://vision.local:9444",
            "cert_expires_at": "2026-05-14T10:00:00Z",
        },
        peer_fp=VALID_FP,
    )
    result = enrollment.enroll(
        host="vision.local", port=9443, token="tok",
        expected_fingerprint=VALID_FP, pubkey_pem="-----BEGIN PUBLIC KEY-----\nxx\n-----END PUBLIC KEY-----\n",
        hostname="sat-01", mac_addresses=["aa:bb:cc:dd:ee:ff"],
        capabilities={}, satellite_version="1.0.0",
        _transport=transport,
    )
    assert result["satellite_id"] == "sat-uuid-xxx"
    # Payload envoyé correctement
    assert transport.last_payload["token"] == "tok"
    assert transport.last_payload["protocol_version"] == 1


def test_enroll_fingerprint_mismatch_raises():
    transport = FakeTransport(
        status=200, body={}, peer_fp="different-fp-but-same-length-" + "x" * 36,
    )
    with pytest.raises(enrollment.EnrollmentError, match="MITM|fingerprint"):
        enrollment.enroll(
            host="x", port=9443, token="t", expected_fingerprint=VALID_FP,
            pubkey_pem="x", hostname="x", mac_addresses=["aa:bb:cc:dd:ee:ff"],
            capabilities={}, satellite_version="1.0.0",
            _transport=transport,
        )


def test_enroll_403_raises():
    transport = FakeTransport(
        status=403, body={"detail": "token invalide"}, peer_fp=VALID_FP,
    )
    with pytest.raises(enrollment.EnrollmentError):
        enrollment.enroll(
            host="x", port=9443, token="bad", expected_fingerprint=VALID_FP,
            pubkey_pem="x", hostname="x", mac_addresses=["aa:bb:cc:dd:ee:ff"],
            capabilities={}, satellite_version="1.0.0",
            _transport=transport,
        )


def test_enroll_payload_structure():
    transport = FakeTransport(
        status=200,
        body={
            "satellite_id": "s", "device_cert_pem": "c", "vision_ca_pem": "ca",
            "runtime_uri": "u", "cert_expires_at": "t",
        },
        peer_fp=VALID_FP,
    )
    enrollment.enroll(
        host="x", port=9443, token="t", expected_fingerprint=VALID_FP,
        pubkey_pem="PKEY", hostname="my-sat",
        mac_addresses=["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"],
        capabilities={"audio": {"device": "hw:2,0"}},
        satellite_version="1.2.3",
        _transport=transport,
    )
    p = transport.last_payload
    assert p["token"] == "t"
    assert p["hostname"] == "my-sat"
    assert p["mac_addresses"] == ["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]
    assert p["pubkey_pem"] == "PKEY"
    assert p["capabilities"] == {"audio": {"device": "hw:2,0"}}
    assert p["protocol_version"] == 1
    assert p["satellite_version"] == "1.2.3"
