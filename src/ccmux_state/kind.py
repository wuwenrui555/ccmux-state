"""Kind: the internal pre-pane-refinement classifier.

A Kind is one of:
- ("idle",)
- ("working",)
- ("blocked", tool_name: str)

Two derivation paths:
- kind_from_pane: cold-start from pane text alone (this module, below)
- kind_from_event: incremental update from claude-tap events (also here)
"""

from __future__ import annotations

import sys
from typing import Literal

from ccmux_state.pane import (
    extract_between_rules,
    has_input_chrome,
    parse_status_line,
)

Kind = (
    tuple[Literal["idle"]] | tuple[Literal["working"]] | tuple[Literal["blocked"], str]
)


def kind_from_pane(pane_text: str) -> Kind:
    """Derive an initial Kind from a single pane snapshot.

    Used by SessionMonitor.__aenter__ before any tap event has
    arrived. Once events are flowing, kind_from_event takes over.
    """
    if not pane_text:
        return ("idle",)
    lines = pane_text.split("\n")
    if has_input_chrome(lines):
        spinner = parse_status_line(pane_text)
        if spinner and "…" in spinner:
            return ("working",)
        return ("idle",)
    # No chrome: distinguish "dialog present" from "bare bash / empty".
    if extract_between_rules(pane_text):
        return ("blocked", "unknown")
    return ("idle",)


def kind_from_event(
    prev_kind: Kind,
    pending_tool: str | None,
    event: dict,
) -> tuple[Kind, str | None]:
    """Update Kind given the previous kind, pending PR memo, and a tap event.

    Returns (new_kind, new_pending_tool). Both are returned together
    because permission_request / post_tool_use transitions need to
    coordinate them.

    Unknown event_types are no-ops (with a one-line warning to
    stderr) so a future Claude Code hook addition cannot crash the
    monitor.
    """
    et = event.get("event_type", "")
    payload = event.get("payload") or {}

    if et == "stop":
        return ("idle",), None
    if et == "user_prompt_submit":
        return ("working",), pending_tool
    if et == "pre_tool_use":
        return ("working",) if prev_kind != ("working",) else prev_kind, pending_tool
    if et == "post_tool_use":
        tool = payload.get("tool_name", "")
        if pending_tool is not None and tool == pending_tool:
            return ("working",), None
        return prev_kind, pending_tool
    if et == "permission_request":
        tool = payload.get("tool_name", "")
        return ("blocked", tool), tool
    if et == "notification":
        return prev_kind, pending_tool
    if et == "session_end":
        # Caller handles termination by checking event_type before
        # calling kind_from_event. If we see it here it's a no-op.
        return prev_kind, pending_tool
    print(f"ccmux-state: unknown tap event_type {et!r}", file=sys.stderr)
    return prev_kind, pending_tool
