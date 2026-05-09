from pathlib import Path

from ccmux_state.derive import derive_state
from ccmux_state.state import Blocked, Idle, Working

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


def test_idle_kind_with_completion_summary():
    """Pane shows 'Worked for 56s' above chrome -> Idle with that text."""
    state = derive_state(("idle",), _read("pane_idle_completion.txt"))
    assert isinstance(state, Idle)
    assert "Worked for 56s" in state.text


def test_idle_kind_with_empty_chrome():
    state = derive_state(("idle",), _read("pane_idle_empty.txt"))
    assert state == Idle(text="")


def test_working_kind_with_running_spinner():
    state = derive_state(("working",), _read("pane_working.txt"))
    assert isinstance(state, Working)
    assert "Thinking…" in state.text
    assert "16s" in state.text


def test_working_kind_with_completion_downgrades_to_idle():
    """Esc-interrupt: events say working, but pane shows completion
    summary (no `…`). Trust the screen and emit Idle."""
    state = derive_state(("working",), _read("pane_idle_completion.txt"))
    assert isinstance(state, Idle)
    assert "Worked for 56s" in state.text


def test_working_kind_with_blank_above_chrome_downgrades_to_empty_idle():
    state = derive_state(("working",), _read("pane_idle_empty.txt"))
    assert state == Idle(text="")


def test_blocked_kind_extracts_dialog_body():
    state = derive_state(
        ("blocked", "AskUserQuestion"),
        _read("pane_blocked_permission.txt"),
    )
    assert isinstance(state, Blocked)
    assert state.tool_name == "AskUserQuestion"
    assert "Do you want to proceed?" in state.content


def test_blocked_kind_keeps_tool_name_unknown():
    state = derive_state(
        ("blocked", "unknown"),
        _read("pane_blocked_permission.txt"),
    )
    assert isinstance(state, Blocked)
    assert state.tool_name == "unknown"
    assert "Do you want to proceed?" in state.content


def test_blocked_kind_empty_content_when_no_rules_in_pane():
    state = derive_state(("blocked", "Bash"), _read("pane_no_chrome.txt"))
    assert state == Blocked(tool_name="Bash", content="")
