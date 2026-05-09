from __future__ import annotations


class CCMuxStateError(Exception):
    """Base exception for ccmux-state."""


class TmuxResolutionError(CCMuxStateError):
    """tmux could not resolve the requested session, window, or pane.

    Raised at cold-start (`SessionMonitor.__aenter__`) when
    `tmux display-message` cannot find the requested session or
    window 0. The monitor never starts in this case.
    """


class PaneCaptureError(CCMuxStateError):
    """tmux capture-pane failed for a known pane id.

    Raised by `capture_pane`. Mid-life occurrences are caught by the
    poll loop and surfaced as `Dead(reason="pane_lost")`.
    """
