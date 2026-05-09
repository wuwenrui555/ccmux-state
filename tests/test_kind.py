from pathlib import Path

from ccmux_state.kind import kind_from_event, kind_from_pane

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


def _ev(event_type: str, **payload):
    """Build a minimal tap event dict suitable for kind_from_event."""
    return {
        "event_type": event_type,
        "claude": {"session_id": "s"},
        "tmux": {"session_name": "tmux-s", "pane_id": "%1"},
        "payload": payload,
    }


# ---------- kind_from_pane ----------


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


# ---------- kind_from_event ----------


def test_user_prompt_submit_from_idle_goes_working():
    new_kind, pending = kind_from_event(("idle",), None, _ev("user_prompt_submit"))
    assert new_kind == ("working",)
    assert pending is None


def test_pre_tool_use_keeps_working():
    new_kind, pending = kind_from_event(
        ("working",), None, _ev("pre_tool_use", tool_name="Bash")
    )
    assert new_kind == ("working",)
    assert pending is None


def test_post_tool_use_keeps_working_when_no_pending_pr():
    new_kind, pending = kind_from_event(
        ("working",), None, _ev("post_tool_use", tool_name="Bash")
    )
    assert new_kind == ("working",)
    assert pending is None


def test_permission_request_goes_blocked_and_remembers_tool():
    new_kind, pending = kind_from_event(
        ("working",), None, _ev("permission_request", tool_name="AskUserQuestion")
    )
    assert new_kind == ("blocked", "AskUserQuestion")
    assert pending == "AskUserQuestion"


def test_post_tool_use_matching_pending_pr_returns_to_working():
    new_kind, pending = kind_from_event(
        ("blocked", "AskUserQuestion"),
        "AskUserQuestion",
        _ev("post_tool_use", tool_name="AskUserQuestion"),
    )
    assert new_kind == ("working",)
    assert pending is None


def test_post_tool_use_for_unrelated_tool_does_not_unblock():
    """A post_tool_use that does not match the pending_tool must not
    bring us out of Blocked."""
    new_kind, pending = kind_from_event(
        ("blocked", "AskUserQuestion"),
        "AskUserQuestion",
        _ev("post_tool_use", tool_name="Bash"),
    )
    assert new_kind == ("blocked", "AskUserQuestion")
    assert pending == "AskUserQuestion"


def test_stop_goes_idle():
    new_kind, pending = kind_from_event(("working",), None, _ev("stop"))
    assert new_kind == ("idle",)
    assert pending is None


def test_notification_does_not_change_kind():
    new_kind, pending = kind_from_event(
        ("working",), None, _ev("notification", message="x")
    )
    assert new_kind == ("working",)
    assert pending is None


def test_unknown_event_type_is_no_op():
    new_kind, pending = kind_from_event(
        ("working",), None, _ev("future_event_type_we_do_not_know")
    )
    assert new_kind == ("working",)
    assert pending is None
