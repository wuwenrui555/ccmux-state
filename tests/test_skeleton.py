def test_import_package():
    import ccmux_state

    assert ccmux_state.__version__ == "0.1.0"


def test_public_api_exports():
    from ccmux_state import (
        Blocked,
        CCMuxStateError,
        Dead,
        Idle,
        PaneCaptureError,
        SessionMonitor,
        State,
        TmuxResolutionError,
        Working,
    )

    assert SessionMonitor is not None
    assert Idle is not None
    assert Working is not None
    assert Blocked is not None
    assert Dead is not None
    assert State is not None
    assert CCMuxStateError is not None
    assert TmuxResolutionError is not None
    assert PaneCaptureError is not None
