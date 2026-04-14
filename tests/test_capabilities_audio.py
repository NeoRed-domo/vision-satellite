from unittest.mock import patch, MagicMock

from vision_satellite.capabilities import audio


@patch("vision_satellite.capabilities.audio.enumerate_cards")
@patch("vision_satellite.capabilities.audio._test_arecord_capture")
@patch("vision_satellite.capabilities.audio._disable_usb_autosuspend")
def test_detect_audio_picks_usb_that_works(mock_disable, mock_test, mock_enum):
    mock_enum.return_value = [
        (0, "tegra [hda]", False),
        (2, "TONOR TM20 [Device]", True),
    ]
    # arecord works for hw:2,0 @ 16kHz
    mock_test.side_effect = lambda dev, rate, duration_s=3: (dev == "hw:2,0" and rate == 16000)
    result = audio.detect_audio()
    assert result == {"device": "hw:2,0", "native_rate": 16000, "description": "TONOR TM20 [Device]"}
    mock_disable.assert_called_with(2)


@patch("vision_satellite.capabilities.audio.enumerate_cards")
@patch("vision_satellite.capabilities.audio._test_arecord_capture")
def test_detect_audio_no_usb_returns_none(mock_test, mock_enum):
    mock_enum.return_value = [(0, "tegra", False)]
    result = audio.detect_audio()
    assert result is None


@patch("vision_satellite.capabilities.audio.enumerate_cards")
def test_detect_audio_no_cards_returns_none(mock_enum):
    mock_enum.return_value = []
    result = audio.detect_audio()
    assert result is None
