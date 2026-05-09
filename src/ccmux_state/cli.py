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


async def _watch(args: argparse.Namespace) -> int:
    try:
        async with SessionMonitor(
            tmux_session=args.tmux_session,
            poll_interval=args.poll_interval,
        ) as monitor:
            async for state in monitor:
                if args.json:
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
    p_watch.set_defaults(fn=cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
