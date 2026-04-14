from unittest.mock import patch, MagicMock

from vision_satellite.capabilities import camera


V4L2_OUTPUT_C920 = """Driver Info:
\tDriver name      : uvcvideo
\tCard type        : HD Pro Webcam C920
\tBus info         : usb-0000:00:14.0-1
Format Video Capture:
\tType                : Video Capture
\tPixel Format        : 'YUYV'
\tField               : None
\tBytes per Line      : 3840
\tSize Image          : 4147200
\tColorspace          : sRGB
\tTransfer Function   : Default
\tYCbCr/HSV Encoding  : Default
\tQuantization        : Default
\tFlags               :
\tSizes available: 1920x1080 1280x720 640x480 320x240
"""


@patch("vision_satellite.capabilities.camera.glob.glob")
@patch("vision_satellite.capabilities.camera.subprocess.run")
def test_detect_camera_picks_highest_resolution(mock_run, mock_glob):
    mock_glob.return_value = ["/dev/video0"]
    mock_run.return_value = MagicMock(returncode=0, stdout=V4L2_OUTPUT_C920, stderr="")
    result = camera.detect_camera()
    assert result is not None
    assert result["device"] == "/dev/video0"
    assert "C920" in result["description"]
    assert result["max_resolution"] == "1920x1080"


@patch("vision_satellite.capabilities.camera.glob.glob")
def test_detect_camera_none_if_no_video_devices(mock_glob):
    mock_glob.return_value = []
    assert camera.detect_camera() is None


@patch("vision_satellite.capabilities.camera.glob.glob")
@patch("vision_satellite.capabilities.camera.subprocess.run")
def test_detect_camera_skips_broken_devices(mock_run, mock_glob):
    mock_glob.return_value = ["/dev/video0"]
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No such device")
    assert camera.detect_camera() is None
