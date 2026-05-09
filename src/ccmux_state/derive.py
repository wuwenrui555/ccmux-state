"""derive_state: combine kind + pane text into the final State."""

from __future__ import annotations

from ccmux_state.kind import Kind
from ccmux_state.pane import extract_between_rules, parse_status_line
from ccmux_state.state import Blocked, Idle, State, Working


def derive_state(kind: Kind, pane_text: str) -> State:
    """Refine an internal Kind with the latest pane text.

    Cases:
    - kind = idle: emit Idle(text=spinner_or_empty). The spinner row
      may be a completion summary like "Worked for 56s" or empty.
    - kind = working: if the spinner row contains the running-status
      `…`, emit Working(spinner). Otherwise downgrade to
      Idle(spinner_or_empty); this captures the Esc-interrupt case.
    - kind = blocked: emit Blocked(tool_name, content) where content
      is the body between the most recent pair of `────` rule rows.
    """
    spinner = parse_status_line(pane_text)
    match kind:
        case ("idle",):
            return Idle(text=spinner or "")
        case ("working",):
            if spinner and "…" in spinner:
                return Working(text=spinner)
            return Idle(text=spinner or "")
        case ("blocked", tool_name):
            return Blocked(
                tool_name=tool_name,
                content=extract_between_rules(pane_text),
            )
    raise AssertionError(f"unreachable kind: {kind!r}")
