import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from vision_satellite import main as m


@patch("vision_satellite.main.detect_audio", return_value={"device": "hw:2,0", "native_rate": 16000, "description": "TONOR"})
@patch("vision_satellite.main.detect_camera", return_value=None)
@patch("vision_satellite.main.detect_bluetooth", return_value=None)
@patch("vision_satellite.main.detect_zigbee", return_value=None)
@patch("vision_satellite.main.detect_zwave", return_value=None)
def test_detect_all_capabilities(mock_zw, mock_zb, mock_bt, mock_cam, mock_au):
    caps = m.detect_all_capabilities()
    assert caps["audio"] == {"device": "hw:2,0", "native_rate": 16000, "description": "TONOR"}
    assert caps["camera"] is None
    assert caps["bluetooth"] is None


@patch("vision_satellite.main._get_mac_addresses", return_value=["aa:bb:cc:dd:ee:ff"])
@patch("vision_satellite.main._get_hostname", return_value="testsat")
@patch("vision_satellite.main.detect_all_capabilities", return_value={"audio": None})
@patch("vision_satellite.main.enrollment.enroll")
@patch("vision_satellite.main.identity.generate_keypair", return_value="-----BEGIN PUBLIC KEY-----\nxx\n-----END PUBLIC KEY-----\n")
@patch("vision_satellite.main.identity.write_cert")
def test_do_enroll_success(mock_write, mock_keygen, mock_enroll, mock_caps, mock_host, mock_mac, tmp_path):
    mock_enroll.return_value = {
        "satellite_id": "sat-1",
        "device_cert_pem": "-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----\n",
        "vision_ca_pem": "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n",
        "runtime_uri": "mtls://x:9443",
        "cert_expires_at": "2026-05-14T10:00:00Z",
    }
    uri = f"vision-enroll://192.168.1.10:9443?token=tok&fp={'a'*64}&name=Salon&v=1"
    config_p = tmp_path / "config.json"
    rc = m.do_enroll(
        uri, key_path=tmp_path / "k", cert_path=tmp_path / "c",
        ca_cert_path=tmp_path / "ca",
        config_path=config_p,
        hostname=None, mac_addresses=None,
    )
    assert rc == 0
    # key + cert + ca were written
    assert mock_keygen.called
    assert mock_write.call_count == 2  # device.crt + vision-ca.crt


@patch("vision_satellite.main._get_mac_addresses", return_value=["aa:bb:cc:dd:ee:ff"])
@patch("vision_satellite.main._get_hostname", return_value="testsat")
@patch("vision_satellite.main.detect_all_capabilities", return_value={})
@patch("vision_satellite.main.enrollment.enroll")
@patch("vision_satellite.main.identity.generate_keypair", return_value="PKEY")
def test_do_enroll_fails_on_enrollment_error(mock_keygen, mock_enroll, mock_caps, mock_host, mock_mac, tmp_path):
    from vision_satellite.enrollment import EnrollmentError
    mock_enroll.side_effect = EnrollmentError("token invalide")
    uri = f"vision-enroll://x?token=t&fp={'a'*64}"
    rc = m.do_enroll(
        uri, key_path=tmp_path / "k", cert_path=tmp_path / "c",
        ca_cert_path=tmp_path / "ca", hostname=None, mac_addresses=None,
    )
    assert rc != 0


def test_do_enroll_invalid_uri(tmp_path):
    rc = m.do_enroll(
        "not-a-valid-uri", key_path=tmp_path / "k", cert_path=tmp_path / "c",
        ca_cert_path=tmp_path / "ca", hostname=None, mac_addresses=None,
    )
    assert rc != 0


@patch("vision_satellite.main.detect_audio", return_value=None)
@patch("vision_satellite.main._get_mac_addresses", return_value=["aa:bb:cc:dd:ee:ff"])
@patch("vision_satellite.main._get_hostname", return_value="testsat")
@patch("vision_satellite.main.detect_all_capabilities", return_value={"audio": None})
@patch("vision_satellite.main.enrollment.enroll")
@patch("vision_satellite.main.identity.generate_keypair", return_value="PKEY")
@patch("vision_satellite.main.identity.write_cert")
def test_do_enroll_persists_config(
    mock_write_cert, mock_keygen, mock_enroll, mock_caps,
    mock_host, mock_mac, mock_audio, tmp_path,
):
    mock_enroll.return_value = {
        "satellite_id": "sat-abc",
        "device_cert_pem": "-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----\n",
        "vision_ca_pem": "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n",
        "runtime_uri": "mtls://vision.local:9443",
        "cert_expires_at": "2026-05-14T10:00:00Z",
    }
    uri = f"vision-enroll://192.168.1.10?token=t&fp={'a'*64}&name=X"
    key_p = tmp_path / "device.key"
    cert_p = tmp_path / "device.crt"
    ca_p = tmp_path / "vision-ca.crt"
    config_p = tmp_path / "config.json"
    rc = m.do_enroll(
        uri, key_path=key_p, cert_path=cert_p, ca_cert_path=ca_p,
        config_path=config_p,
        hostname=None, mac_addresses=None,
    )
    assert rc == 0
    conf = json.loads(config_p.read_text())
    assert conf["satellite_id"] == "sat-abc"
    assert conf["runtime_uri"] == "mtls://vision.local:9443"
    assert "enrolled_at" in conf


def test_do_runtime_missing_config_fails(tmp_path):
    rc = m.do_runtime(
        key_path=tmp_path / "k", cert_path=tmp_path / "c",
        ca_cert_path=tmp_path / "ca", config_path=tmp_path / "missing.json",
    )
    assert rc != 0


@patch("vision_satellite.main.asyncio.run")
@patch("vision_satellite.main.SatelliteRuntimeClient")
@patch("vision_satellite.main.detect_audio", return_value={"device": "hw:2,0", "native_rate": 16000, "description": "TONOR"})
def test_do_runtime_launches_client(
    mock_audio, mock_client_cls, mock_asyncio_run, tmp_path,
):
    # Prepare fake config.json
    config_p = tmp_path / "config.json"
    config_p.write_text(json.dumps({
        "satellite_id": "sat-xxx",
        "runtime_uri": "mtls://vision.local:9443",
        "cert_expires_at": "2026-05-14T10:00:00Z",
        "capabilities": {},
        "enrolled_at": "2026-04-14T16:00:00Z",
    }))
    key_p = tmp_path / "k"
    key_p.write_text("PKEY")
    cert_p = tmp_path / "c"
    cert_p.write_text("CERT")
    ca_p = tmp_path / "ca"
    ca_p.write_text("CA")

    mock_client_inst = MagicMock()
    mock_client_cls.return_value = mock_client_inst

    rc = m.do_runtime(
        key_path=key_p, cert_path=cert_p, ca_cert_path=ca_p, config_path=config_p,
    )
    assert rc == 0
    # SatelliteRuntimeClient instantiated with audio_cmd including hw:2,0
    kwargs = mock_client_cls.call_args.kwargs
    assert kwargs["runtime_uri"] == "mtls://vision.local:9443"
    assert kwargs["satellite_id"] == "sat-xxx"
    assert "hw:2,0" in kwargs["audio_cmd"]
    assert "16000" in kwargs["audio_cmd"]
