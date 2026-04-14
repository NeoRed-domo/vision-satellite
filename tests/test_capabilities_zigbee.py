from unittest.mock import patch, MagicMock

from vision_satellite.capabilities import zigbee


class FakeEntry:
    def __init__(self, name):
        self.name = name


@patch("vision_satellite.capabilities.zigbee.os.scandir")
@patch("vision_satellite.capabilities.zigbee.os.readlink")
def test_detect_zigbee_sonoff(mock_readlink, mock_scandir):
    mock_scandir.return_value.__enter__.return_value = [
        FakeEntry("usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_20231234567-if00-port0")
    ]
    mock_readlink.return_value = "../../ttyUSB0"
    result = zigbee.detect_zigbee()
    assert result is not None
    assert "Sonoff" in result["model"]
    assert result["device_path"].endswith("/ttyUSB0")


@patch("vision_satellite.capabilities.zigbee.os.scandir")
def test_detect_zigbee_unknown_dongle(mock_scandir):
    mock_scandir.return_value.__enter__.return_value = [
        FakeEntry("usb-Mystery_Widget_12345")
    ]
    assert zigbee.detect_zigbee() is None


@patch("vision_satellite.capabilities.zigbee.os.scandir", side_effect=FileNotFoundError())
def test_detect_zigbee_no_usb_path(mock_scandir):
    assert zigbee.detect_zigbee() is None
