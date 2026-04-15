"""Tests wizard.py — mock subprocess (whiptail) pour tester la logique sans TTY."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Le wizard est à la racine, pas dans un package — importer explicitement
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import wizard  # noqa: E402


def _mock_whiptail(return_text: str = "", returncode: int = 0):
    """Helper pour mocker un whiptail result."""
    m = MagicMock()
    m.stdout = return_text
    m.stderr = return_text
    m.returncode = returncode
    return m


@patch("wizard.subprocess.run")
def test_whiptail_yesno_user_confirms(mock_run):
    mock_run.return_value = _mock_whiptail(returncode=0)
    result = wizard._whiptail_yesno("Continuer ?", "Vision")
    assert result is True


@patch("wizard.subprocess.run")
def test_whiptail_yesno_user_cancels(mock_run):
    mock_run.return_value = _mock_whiptail(returncode=1)
    assert wizard._whiptail_yesno("Continuer ?", "Vision") is False


@patch("wizard.subprocess.run")
def test_whiptail_inputbox_returns_text(mock_run):
    mock_run.return_value = _mock_whiptail(return_text="192.168.1.10", returncode=0)
    result = wizard._whiptail_inputbox("Host :", title="Vision")
    assert result == "192.168.1.10"


@patch("wizard.subprocess.run")
def test_whiptail_inputbox_cancel_returns_none(mock_run):
    mock_run.return_value = _mock_whiptail(returncode=1)
    assert wizard._whiptail_inputbox("Host :", title="Vision") is None


@patch("wizard.subprocess.run")
def test_screen_welcome_accepts(mock_run):
    mock_run.return_value = _mock_whiptail(returncode=0)
    assert wizard.screen_welcome() is True


@patch("wizard._whiptail_yesno", return_value=False)
def test_screen_welcome_cancel(mock_yn):
    assert wizard.screen_welcome() is False


@patch("wizard.detect_all_capabilities", return_value={
    "audio": {"device": "hw:2,0", "native_rate": 16000, "description": "TONOR"},
    "camera": None, "bluetooth": None, "zigbee": None, "zwave": None,
})
@patch("wizard._whiptail_checklist", return_value=["audio"])
def test_screen_detect_capabilities(mock_check, mock_detect):
    caps = wizard.screen_detect_capabilities()
    assert "audio" in caps
    assert caps["audio"]["device"] == "hw:2,0"


@patch("wizard.qr_parse.parse_enroll_uri")
@patch("wizard._whiptail_inputbox")
def test_screen_enroll_uri_valid(mock_input, mock_parse):
    mock_input.return_value = "vision-enroll://x?token=t&fp=" + "a"*64
    mock_parse.return_value = {"host": "x", "port": 9444, "token": "t", "fingerprint": "a"*64, "name": "", "version": 1}
    uri, parsed = wizard.screen_enroll_uri()
    assert uri.startswith("vision-enroll://")
    assert parsed["host"] == "x"


@patch("wizard._whiptail_inputbox", return_value=None)
def test_screen_enroll_uri_cancel(mock_input):
    result = wizard.screen_enroll_uri()
    assert result is None


@patch("wizard.subprocess.run")
def test_screen_install_invokes_main_enroll(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    ok = wizard.screen_install(uri="vision-enroll://x?token=t&fp=" + "a"*64)
    assert ok is True
    args = mock_run.call_args[0][0]
    assert "--enroll" in args
