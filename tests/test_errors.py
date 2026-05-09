import pytest

from ccmux_state.errors import (
    CCMuxStateError,
    PaneCaptureError,
    TmuxResolutionError,
)


def test_resolution_error_is_ccmux_state_error():
    assert issubclass(TmuxResolutionError, CCMuxStateError)


def test_capture_error_is_ccmux_state_error():
    assert issubclass(PaneCaptureError, CCMuxStateError)


def test_resolution_error_carries_message():
    with pytest.raises(TmuxResolutionError, match="no such session"):
        raise TmuxResolutionError("no such session 'foo'")


def test_capture_error_carries_message():
    with pytest.raises(PaneCaptureError, match="capture failed"):
        raise PaneCaptureError("capture failed for %3")
