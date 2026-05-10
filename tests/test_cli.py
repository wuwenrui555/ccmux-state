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


def test_event_suffix_pre_tool_use_shows_tool_name():
    from ccmux_state.cli import _event_suffix

    ev = {
        "event_type": "pre_tool_use",
        "payload": {"tool_name": "Bash", "tool_use_id": "toolu_01"},
    }
    assert _event_suffix(ev) == " tool=Bash"


def test_event_suffix_post_tool_use_shows_tool_and_duration():
    from ccmux_state.cli import _event_suffix

    ev = {
        "event_type": "post_tool_use",
        "payload": {
            "tool_name": "Bash",
            "tool_use_id": "toolu_01",
            "duration_ms": 75,
        },
    }
    assert _event_suffix(ev) == " tool=Bash duration=75ms"


def test_event_suffix_stop_shows_truncated_reply():
    from ccmux_state.cli import _event_suffix

    ev = {
        "event_type": "stop",
        "payload": {"last_assistant_message": "Done. Let me know if anything else."},
    }
    suffix = _event_suffix(ev)
    assert suffix.startswith(' reply="')
    assert "Done. Let me know" in suffix


def test_event_suffix_stop_truncates_long_reply():
    from ccmux_state.cli import _event_suffix

    long_text = "A" * 200
    ev = {"event_type": "stop", "payload": {"last_assistant_message": long_text}}
    suffix = _event_suffix(ev)
    # Truncation cap of 50 chars + ellipsis + closing quote
    assert len(suffix) < 80
    assert "…" in suffix
    assert suffix.endswith('"')


def test_event_suffix_stop_handles_empty_reply():
    from ccmux_state.cli import _event_suffix

    ev = {"event_type": "stop", "payload": {"last_assistant_message": ""}}
    assert _event_suffix(ev) == ""


def test_event_suffix_other_events_return_empty():
    from ccmux_state.cli import _event_suffix

    for et in ("user_prompt_submit", "notification", "session_end", "session_start"):
        assert _event_suffix({"event_type": et, "payload": {}}) == "", et


def test_event_suffix_handles_none():
    from ccmux_state.cli import _event_suffix

    assert _event_suffix(None) == ""


def test_event_suffix_post_tool_use_strips_newlines_in_reply():
    """Multi-line replies must collapse to a single line so the
    one-line debug output stays one line."""
    from ccmux_state.cli import _event_suffix

    ev = {
        "event_type": "stop",
        "payload": {"last_assistant_message": "Line 1\nLine 2\nLine 3"},
    }
    suffix = _event_suffix(ev)
    assert "\n" not in suffix


def test_print_debug_one_line_appends_event_suffix(capsys):
    """When the latest event is a post_tool_use, the one-line output
    appends ` tool=NAME duration=Nms` after the State repr."""
    from unittest.mock import MagicMock

    from ccmux_state.cli import _print_debug_one_line
    from ccmux_state.state import Working

    monitor = MagicMock()
    monitor.last_event = {
        "event_type": "post_tool_use",
        "payload": {"tool_name": "Bash", "duration_ms": 75},
    }
    monitor.kind = ("working",)
    monitor.last_pane_text = ""
    _print_debug_one_line(monitor, Working(text="x"))
    line = capsys.readouterr().out

    assert "tool=Bash" in line
    assert "duration=75ms" in line


def test_format_event_short_strips_tool_annotation():
    from ccmux_state.cli import _format_event_short

    ev = {
        "event_type": "permission_request",
        "payload": {"tool_name": "AskUserQuestion"},
    }
    # Just event_type. The tool name lives in the State repr at the
    # tail of the one-line output, no need to repeat it here.
    assert _format_event_short(ev) == "permission_request"


def test_format_event_short_handles_none():
    from ccmux_state.cli import _format_event_short

    assert _format_event_short(None) == "—"


def test_format_kind_short_strips_tool_name():
    from ccmux_state.cli import _format_kind_short

    assert _format_kind_short(("idle",)) == "idle"
    assert _format_kind_short(("working",)) == "working"
    assert _format_kind_short(("blocked", "AskUserQuestion")) == "blocked"


def test_print_debug_one_line_widths_and_glyph(capsys):
    """One-line debug widths: event 18, kind 7, glyph 1, chrome 5.

    18 fits the longest known event_type (`user_prompt_submit` /
    `permission_request`); 7 fits the longest known kind name
    (`working`). No truncation needed.
    """
    from unittest.mock import MagicMock

    from ccmux_state.cli import _print_debug_one_line
    from ccmux_state.state import Working

    monitor = MagicMock()
    monitor.last_event = {"event_type": "user_prompt_submit", "payload": {}}
    monitor.kind = ("working",)
    monitor.last_pane_text = (
        "✻ Thinking… (16s)\n\n" + ("─" * 80) + "\n❯\n" + ("─" * 80) + "\n"
    )
    state = Working(text="✻ Thinking… (16s)")

    _print_debug_one_line(monitor, state)
    captured = capsys.readouterr().out

    head, _, _ = captured.partition("] ")
    parts = head.split("][")
    assert len(parts) == 4, parts
    event, kind, glyph, chrome = parts
    event = event.lstrip("[")
    chrome = chrome.rstrip("]").rstrip()
    assert len(event) == 18, repr(event)
    assert len(kind) == 7, repr(kind)
    assert len(glyph) == 1, repr(glyph)
    assert len(chrome) == 5, repr(chrome)
    assert event.strip() == "user_prompt_submit"
    assert kind.strip() == "working"
    assert glyph == "✻"
    assert chrome == "──❯──"


def test_print_debug_one_line_omits_tool_annotation_in_event_and_kind(capsys):
    """When kind is blocked(AskUserQuestion) and the event is a
    permission_request for the same tool, the one-line output keeps
    the columns at fixed widths and lets State repr carry tool_name.
    """
    from unittest.mock import MagicMock

    from ccmux_state.cli import _print_debug_one_line
    from ccmux_state.state import Blocked

    monitor = MagicMock()
    monitor.last_event = {
        "event_type": "permission_request",
        "payload": {"tool_name": "AskUserQuestion"},
    }
    monitor.kind = ("blocked", "AskUserQuestion")
    monitor.last_pane_text = ""
    _print_debug_one_line(monitor, Blocked(tool_name="AskUserQuestion"))
    line = capsys.readouterr().out

    head, _, tail = line.partition("] ")
    parts = head.split("][")
    event = parts[0].lstrip("[")
    kind = parts[1]
    assert len(event) == 18
    assert len(kind) == 7
    assert event.strip() == "permission_request"
    assert kind.strip() == "blocked"
    # tool_name appears only in the State repr at the tail.
    assert "AskUserQuestion" in tail
    assert "(tool=" not in line


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
