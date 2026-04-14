from unittest.mock import patch, MagicMock

from vision_satellite.capabilities import bluetooth


HCICONFIG_OUTPUT = """hci0:\tType: Primary  Bus: UART
\tBD Address: AA:BB:CC:DD:EE:FF  ACL MTU: 1021:8  SCO MTU: 64:1
\tUP RUNNING
\tRX bytes:15708 acl:0 sco:0 events:1098 errors:0
\tTX bytes:4528 acl:0 sco:0 commands:1095 errors:0
"""


@patch("vision_satellite.capabilities.bluetooth.subprocess.run")
def test_detect_bluetooth_happy(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=HCICONFIG_OUTPUT, stderr="")
    result = bluetooth.detect_bluetooth()
    assert result is not None
    assert result["adapter"] == "hci0"
    assert result["address"] == "AA:BB:CC:DD:EE:FF"


@patch("vision_satellite.capabilities.bluetooth.subprocess.run")
def test_detect_bluetooth_empty(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    assert bluetooth.detect_bluetooth() is None


@patch("vision_satellite.capabilities.bluetooth.subprocess.run", side_effect=FileNotFoundError())
def test_detect_bluetooth_no_tool(mock_run):
    """If hciconfig not installed, return None."""
    assert bluetooth.detect_bluetooth() is None
