from unittest.mock import patch, MagicMock

from vision_satellite.capabilities import zwave


class FakeEntry:
    def __init__(self, name):
        self.name = name


@patch("vision_satellite.capabilities.zwave.os.scandir")
@patch("vision_satellite.capabilities.zwave.os.readlink")
def test_detect_zwave_aeotec(mock_readlink, mock_scandir):
    mock_scandir.return_value.__enter__.return_value = [
        FakeEntry("usb-Aeotec_ZW090_Z-Stick_Gen5_DeadBeef1234-if00-port0")
    ]
    mock_readlink.return_value = "../../ttyUSB0"
    result = zwave.detect_zwave()
    assert result is not None
    assert "Aeotec" in result["model"]
    assert result["device_path"].endswith("/ttyUSB0")


@patch("vision_satellite.capabilities.zwave.os.scandir")
def test_detect_zwave_unknown_dongle(mock_scandir):
    mock_scandir.return_value.__enter__.return_value = [
        FakeEntry("usb-Mystery_Widget_12345")
    ]
    assert zwave.detect_zwave() is None


@patch("vision_satellite.capabilities.zwave.os.scandir", side_effect=FileNotFoundError())
def test_detect_zwave_no_usb_path(mock_scandir):
    assert zwave.detect_zwave() is None
