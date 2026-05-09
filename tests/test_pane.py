from pathlib import Path

import pytest

from ccmux_state.errors import PaneCaptureError, TmuxResolutionError
from ccmux_state.pane import (
    capture_pane,
    extract_between_rules,
    has_input_chrome,
    parse_status_line,
    resolve_pane_id,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


# ---------- has_input_chrome ----------


def test_has_input_chrome_true_for_working_pane():
    lines = _read("pane_working.txt").split("\n")
    assert has_input_chrome(lines) is True


def test_has_input_chrome_true_for_idle_completion():
    lines = _read("pane_idle_completion.txt").split("\n")
    assert has_input_chrome(lines) is True


def test_has_input_chrome_true_for_idle_empty():
    lines = _read("pane_idle_empty.txt").split("\n")
    assert has_input_chrome(lines) is True


def test_has_input_chrome_false_for_blocked_permission():
    lines = _read("pane_blocked_permission.txt").split("\n")
    assert has_input_chrome(lines) is False


def test_has_input_chrome_false_for_no_chrome():
    lines = _read("pane_no_chrome.txt").split("\n")
    assert has_input_chrome(lines) is False


def test_has_input_chrome_false_for_empty_lines():
    assert has_input_chrome([]) is False


# ---------- parse_status_line ----------


def test_parse_status_line_returns_running_text():
    text = parse_status_line(_read("pane_working.txt"))
    assert text is not None
    assert "Thinking…" in text
    assert "16s" in text


def test_parse_status_line_returns_completion_text():
    """Unlike cc-state's version, ours returns completion summaries
    too. The caller decides how to interpret them."""
    text = parse_status_line(_read("pane_idle_completion.txt"))
    assert text is not None
    assert "Worked for 56s" in text


def test_parse_status_line_returns_none_when_no_status_row():
    assert parse_status_line(_read("pane_idle_empty.txt")) is None


def test_parse_status_line_returns_none_when_no_chrome():
    assert parse_status_line(_read("pane_no_chrome.txt")) is None


def test_parse_status_line_returns_none_for_empty_input():
    assert parse_status_line("") is None


# ---------- extract_between_rules ----------


def test_extract_between_rules_returns_dialog_body():
    body = extract_between_rules(_read("pane_blocked_permission.txt"))
    assert "Do you want to proceed?" in body
    assert "❯ 1. Yes" in body
    assert "Esc to cancel" in body


def test_extract_between_rules_empty_when_fewer_than_two_rules():
    # pane_no_chrome has zero rule rows.
    assert extract_between_rules(_read("pane_no_chrome.txt")) == ""


def test_extract_between_rules_empty_for_empty_input():
    assert extract_between_rules("") == ""


# ---------- resolve_pane_id ----------


def test_resolve_pane_id_raises_on_missing_session():
    """We can't fake tmux availability, but we can point at a session
    name that almost certainly does not exist."""
    bogus = "ccmux-state-test-no-such-session-9999"
    with pytest.raises(TmuxResolutionError):
        resolve_pane_id(bogus)


# ---------- capture_pane ----------


def test_capture_pane_raises_on_missing_pane():
    bogus = "%99999999"
    with pytest.raises(PaneCaptureError):
        capture_pane(bogus)
