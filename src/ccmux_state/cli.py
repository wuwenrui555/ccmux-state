"""Command-line front end for ccmux-state.

Currently exposes a single subcommand:

  ccmux-state watch <tmux_session>     subscribe to a session's State
                                        and print each change one line
                                        (or one JSON object with
                                        --json).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys

from ccmux_state.errors import CCMuxStateError
from ccmux_state.monitor import SessionMonitor
from ccmux_state.pane import (
    _find_chrome_separator,
    has_input_chrome,
    parse_status_glyph,
)
from ccmux_state.state import Blocked, Dead, Idle, State, Working


def _to_dict(state: State) -> dict:
    """Flatten a State variant into a JSON-serialisable dict carrying
    a `kind` discriminator field."""
    payload = dataclasses.asdict(state)
    payload["kind"] = type(state).__name__
    if isinstance(state, Dead):
        payload["last_state"] = _to_dict(state.last_state)
    return payload


def _pretty(state: State) -> str:
    """Single-line human-readable rendering."""
    match state:
        case Idle(text):
            text_view = text or "—"
            return f"[idle    ] {text_view}"
        case Working(text):
            return f"[working ] {text}"
        case Blocked(tool_name, content):
            first_line = (content.split("\n", 1)[0] if content else "").strip()
            preview = first_line[:60] + ("…" if len(first_line) > 60 else "")
            return f"[blocked ] tool={tool_name} | {preview}"
        case Dead(reason, last_state):
            return f"[dead    ] reason={reason}; last={type(last_state).__name__}"
    return f"[unknown ] {state!r}"


def _format_event(event: dict | None) -> str:
    if event is None:
        return "—"
    et = event.get("event_type", "?")
    payload = event.get("payload") or {}
    tool = payload.get("tool_name", "")
    if tool:
        return f"{et} (tool={tool})"
    return et


def _format_pane_tail(pane_text: str, lines: int = 8) -> str:
    """Last N non-trailing-blank lines of the pane, indented for output."""
    if not pane_text:
        return "  (empty)"
    rows = pane_text.rstrip("\n").split("\n")
    tail = rows[-lines:]
    return "\n".join(f"  | {row}" for row in tail)


def _print_debug(monitor: SessionMonitor, state: State) -> None:
    print("=" * 60, flush=True)
    print(f"  event : {_format_event(monitor.last_event)}", flush=True)
    print(f"  kind  : {monitor.kind}", flush=True)
    print(f"  state : {state}", flush=True)
    print("  pane  :", flush=True)
    print(_format_pane_tail(monitor.last_pane_text), flush=True)


def _format_kind(kind) -> str:
    """Compact kind rendering with tool name for multi-line output.

    ('idle',)              -> 'idle'
    ('working',)           -> 'working'
    ('blocked', 'Bash')    -> 'blocked(Bash)'
    """
    if kind == ("idle",):
        return "idle"
    if kind == ("working",):
        return "working"
    if isinstance(kind, tuple) and len(kind) == 2 and kind[0] == "blocked":
        return f"blocked({kind[1]})"
    return str(kind)


def _format_event_short(event: dict | None) -> str:
    """Just event_type; bounded at 18 chars (longest known is
    `user_prompt_submit` / `permission_request`). No tool annotation —
    tool_name lives in the State repr at the tail of the one-line
    output, so repeating it here would only eat columns.
    """
    if event is None:
        return "—"
    return event.get("event_type", "?")


def _format_kind_short(kind) -> str:
    """Just the kind name (`idle` / `working` / `blocked`); bounded
    at 7 chars. Tool name lives in State repr at the tail.
    """
    if (
        isinstance(kind, tuple)
        and len(kind) >= 1
        and kind[0]
        in (
            "idle",
            "working",
            "blocked",
        )
    ):
        return kind[0]
    return "?"


def _chrome_shape(pane_text: str) -> str:
    """Five-char visual signature of the captured pane's chrome.

    ──❯──   chrome present, top separator is pure dashes
    ─t❯──   chrome present, top separator carries a tmux pane title
    XXXXX   no chrome detected (Blocked dialog or bare bash)
    """
    if not pane_text:
        return "XXXXX"
    lines = pane_text.split("\n")
    if not has_input_chrome(lines):
        return "XXXXX"
    chrome_idx = _find_chrome_separator(lines)
    if chrome_idx is None:
        return "XXXXX"
    top_line = lines[chrome_idx].rstrip()
    if all(c == "─" for c in top_line):
        return "──❯──"
    return "─t❯──"


def _print_debug_one_line(monitor: SessionMonitor, state: State) -> None:
    event_str = _format_event_short(monitor.last_event)
    kind_str = _format_kind_short(monitor.kind)
    glyph = parse_status_glyph(monitor.last_pane_text) or " "
    chrome = _chrome_shape(monitor.last_pane_text)
    print(
        f"[{event_str:<18}][{kind_str:<7}][{glyph}][{chrome}] {state}",
        flush=True,
    )


async def _watch(args: argparse.Namespace) -> int:
    try:
        async with SessionMonitor(
            tmux_session=args.tmux_session,
            poll_interval=args.poll_interval,
        ) as monitor:
            async for state in monitor:
                if args.debug_one_line:
                    _print_debug_one_line(monitor, state)
                elif args.debug:
                    _print_debug(monitor, state)
                elif args.json:
                    print(json.dumps(_to_dict(state), ensure_ascii=False), flush=True)
                else:
                    print(_pretty(state), flush=True)
    except CCMuxStateError as e:
        print(f"ccmux-state: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_watch(args))
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccmux-state",
        description="Tmux-pane state monitor for Claude Code under the ccmux convention.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_watch = sub.add_parser(
        "watch",
        help="Subscribe to a tmux session's State and print each change.",
    )
    p_watch.add_argument(
        "tmux_session",
        help="tmux session name (the package assumes 1 session = 1 window = 1 Claude).",
    )
    p_watch.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Pane capture interval in seconds (default: 1.0).",
    )
    p_watch.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON object per state change instead of pretty text.",
    )
    p_watch.add_argument(
        "--debug",
        action="store_true",
        help="Multi-line debug output: tap event + kind + State + last pane tail.",
    )
    p_watch.add_argument(
        "--debug-one-line",
        action="store_true",
        help=(
            "Single-line debug output: "
            "[event:18][kind:7][glyph][chrome:5] state. Widths fit the "
            "longest known event_type (18) and kind name (7) without "
            "truncation. Chrome glyph: "
            "──❯── pure / ─t❯── tmux-tagged top / XXXXX no chrome."
        ),
    )
    p_watch.set_defaults(fn=cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
