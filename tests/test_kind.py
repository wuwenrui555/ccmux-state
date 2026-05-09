from pathlib import Path

from ccmux_state.kind import kind_from_pane

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


def test_kind_from_pane_working_with_ellipsis():
    assert kind_from_pane(_read("pane_working.txt")) == ("working",)


def test_kind_from_pane_idle_with_completion_summary():
    """Completion summary (no `…`) above chrome is Idle, not Working."""
    assert kind_from_pane(_read("pane_idle_completion.txt")) == ("idle",)


def test_kind_from_pane_idle_empty_chrome():
    assert kind_from_pane(_read("pane_idle_empty.txt")) == ("idle",)


def test_kind_from_pane_blocked_permission_unknown_tool():
    """No chrome -> blocked. tool_name is 'unknown' on cold-start
    (no permission_request event has refined it yet)."""
    assert kind_from_pane(_read("pane_blocked_permission.txt")) == (
        "blocked",
        "unknown",
    )


def test_kind_from_pane_no_chrome_idle():
    """A pane with no Claude Code chrome at all (e.g. bare bash)
    falls back to Idle. The monitor will produce Idle("") for this."""
    assert kind_from_pane(_read("pane_no_chrome.txt")) == ("idle",)


def test_kind_from_pane_empty_idle():
    assert kind_from_pane("") == ("idle",)
