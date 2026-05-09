"""CLI smoke tests. Heavy lifting is covered by test_monitor; here we
just check argparse + formatting."""

from __future__ import annotations

import json

import pytest

from ccmux_state.cli import _pretty, _to_dict, build_parser
from ccmux_state.state import Blocked, Dead, Idle, Working


def test_parser_requires_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_watch_session_required():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["watch"])


def test_parser_watch_basic():
    parser = build_parser()
    args = parser.parse_args(["watch", "ccmux-test"])
    assert args.command == "watch"
    assert args.tmux_session == "ccmux-test"
    assert args.poll_interval == 1.0
    assert args.json is False


def test_parser_watch_overrides():
    parser = build_parser()
    args = parser.parse_args(["watch", "p", "--poll-interval", "0.5", "--json"])
    assert args.poll_interval == 0.5
    assert args.json is True


def test_parser_watch_accepts_debug_flag():
    parser = build_parser()
    args = parser.parse_args(["watch", "p", "--debug"])
    assert args.debug is True


def test_format_event_none():
    from ccmux_state.cli import _format_event

    assert _format_event(None) == "—"


def test_format_event_with_tool_name():
    from ccmux_state.cli import _format_event

    ev = {"event_type": "permission_request", "payload": {"tool_name": "Bash"}}
    assert _format_event(ev) == "permission_request (tool=Bash)"


def test_format_event_without_tool_name():
    from ccmux_state.cli import _format_event

    ev = {"event_type": "stop", "payload": {}}
    assert _format_event(ev) == "stop"


def test_format_pane_tail_handles_empty():
    from ccmux_state.cli import _format_pane_tail

    assert _format_pane_tail("") == "  (empty)"


def test_format_pane_tail_keeps_last_lines():
    from ccmux_state.cli import _format_pane_tail

    pane = "\n".join(f"line{i}" for i in range(20))
    out = _format_pane_tail(pane, lines=3)
    assert "line17" in out
    assert "line19" in out
    assert "line0" not in out


def test_chrome_shape_uses_xxxxx_when_no_chrome():
    """The no-chrome marker must be exactly 5 characters so the
    one-line debug output stays column-aligned with the chrome-
    present markers (──❯── / ─t❯──)."""
    from ccmux_state.cli import _chrome_shape

    assert _chrome_shape("") == "XXXXX"
    bare = "Some shell content with no chrome anywhere.\n"
    assert _chrome_shape(bare) == "XXXXX"


def test_pretty_idle_with_text():
    assert "Churned" in _pretty(Idle(text="✻ Churned for 55s"))


def test_pretty_idle_empty_uses_dash():
    assert "—" in _pretty(Idle())


def test_pretty_working_shows_text():
    out = _pretty(Working(text="✻ Thinking… 16s"))
    assert "Thinking…" in out
    assert "[working" in out


def test_pretty_blocked_shows_tool_and_first_line():
    state = Blocked(tool_name="AskUserQuestion", content="Do you want to proceed?\n…")
    out = _pretty(state)
    assert "AskUserQuestion" in out
    assert "Do you want to proceed?" in out


def test_pretty_dead_shows_reason_and_last_kind():
    state = Dead(reason="session_end", last_state=Idle(text="bye"))
    out = _pretty(state)
    assert "session_end" in out
    assert "Idle" in out


def test_to_dict_idle_round_trips():
    payload = _to_dict(Idle(text="x"))
    assert payload == {"kind": "Idle", "text": "x"}
    json.dumps(payload, ensure_ascii=False)  # serialisable


def test_to_dict_dead_includes_nested_kind():
    payload = _to_dict(Dead(reason="r", last_state=Working(text="t")))
    assert payload["kind"] == "Dead"
    assert payload["last_state"]["kind"] == "Working"
    assert payload["last_state"]["text"] == "t"
    json.dumps(payload, ensure_ascii=False)
