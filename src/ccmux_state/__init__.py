"""ccmux-state: tmux-pane state monitor for Claude Code sessions
running under the ccmux convention.
"""

from ccmux_state._version import __version__
from ccmux_state.errors import (
    CCMuxStateError,
    PaneCaptureError,
    TmuxResolutionError,
)
from ccmux_state.monitor import SessionMonitor
from ccmux_state.state import Blocked, Dead, Idle, State, Working

__all__ = [
    "__version__",
    "Blocked",
    "CCMuxStateError",
    "Dead",
    "Idle",
    "PaneCaptureError",
    "SessionMonitor",
    "State",
    "TmuxResolutionError",
    "Working",
]
