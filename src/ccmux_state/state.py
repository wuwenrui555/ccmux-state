from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Idle:
    """Claude is waiting for the next user prompt.

    `text` carries any pane decoration above the input chrome
    (typically a completion summary like "✻ Churned for 55s"); it is
    "" when the pane has no decoration above the chrome.
    """

    text: str = ""


@dataclass(frozen=True)
class Working:
    """Claude is processing the current turn.

    `text` is the live spinner row, e.g. "Thinking… (16s · ↑ 827
    tokens)".
    """

    text: str = ""


@dataclass(frozen=True)
class Blocked:
    """A blocking dialog has replaced the input chrome.

    `tool_name` comes from the claude-tap permission_request event
    (e.g. "AskUserQuestion", "ExitPlanMode", "Bash"), or "unknown" if
    cold-start saw the dialog before any event arrived. `content` is
    the dialog body extracted between the most recent pair of `────`
    horizontal rules.
    """

    tool_name: str
    content: str = ""


@dataclass(frozen=True)
class Dead:
    """Terminal state. Emitted once, then the iterator ends."""

    reason: str
    last_state: State


State = Idle | Working | Blocked | Dead
