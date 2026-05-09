"""Pane parsing and tmux IO primitives.

The three parsing helpers (`has_input_chrome`, `parse_status_line`,
`extract_between_rules`) are simplified ports of claude-code-state's
parser. The tmux IO helpers (`resolve_pane_id`, `capture_pane`)
shell out to the `tmux` binary directly; no `libtmux` dependency.
"""

from __future__ import annotations

import subprocess

from ccmux_state.errors import PaneCaptureError, TmuxResolutionError

# ---------------------------------------------------------------------------
# Constants empirically observed against Claude Code 2.1.x
# ---------------------------------------------------------------------------

_CHROME_MIN_LEN = 20
_CHROME_SEARCH_WINDOW = 20
_CHROME_INPUT_MAX_LINES = 8
_STATUS_SCAN_WINDOW = 30

# Spinner glyphs Claude Code rotates through on its status row.
# Empirically observed cycle as of 2026-05-09: `·` `✻` `✽` `✶` `*`
# (5 frames). `✳` and `✢` were in claude-code-state's list and may
# appear in older Claude Code releases — kept here for forward
# compatibility. `*` is the plain ASCII asterisk; without it, every
# 5th frame mis-classifies as Idle.
_STATUS_SPINNERS = frozenset(["·", "✻", "✽", "✶", "✳", "✢", "*"])


def _is_chrome_separator(line: str) -> bool:
    """True for a horizontal-rule row that ccmux-state treats as chrome.

    Real claude-code chrome separators start at column 0; indented
    dashes (e.g. ``  ⎿  ────`` from a rendered tool-result line in
    scrollback) are excluded. Two shapes are accepted:

    1. Pure dashes: ``────...────`` of length >= _CHROME_MIN_LEN. This
       is what claude-code emits and what claude-code-state assumes.

    2. Dash run with embedded text: tmux's ``pane-border-status``
       renders the pane title inside the border row, producing
       ``─...─ <title> ─...─``. We accept lines whose leading dash
       run is >= _CHROME_MIN_LEN AND whose total dash density is
       >= 60% of the (right-stripped) line.
    """
    if not line or line[0] != "─":
        return False
    stripped = line.rstrip()
    if len(stripped) < _CHROME_MIN_LEN:
        return False
    if all(c == "─" for c in stripped):
        return True
    leading_dashes = 0
    for c in stripped:
        if c == "─":
            leading_dashes += 1
        else:
            break
    if leading_dashes < _CHROME_MIN_LEN:
        return False
    dash_count = stripped.count("─")
    return dash_count / len(stripped) >= 0.6


def _find_chrome_separator(
    lines: list[str], window: int = _CHROME_SEARCH_WINDOW
) -> int | None:
    """Index of the topmost ``────`` line within the last *window* lines."""
    start = max(0, len(lines) - window)
    for i in range(start, len(lines)):
        if _is_chrome_separator(lines[i]):
            return i
    return None


# ---------------------------------------------------------------------------
# Public parsing primitives
# ---------------------------------------------------------------------------


def has_input_chrome(lines: list[str]) -> bool:
    """True when Claude's input chrome is rendered at the pane bottom.

    Pattern: a ``────`` separator, followed by a ``❯``-prefixed prompt
    row (with up to a few continuation rows for multi-line input),
    then a second ``────`` separator closing the sandwich.
    """
    if not lines:
        return False
    search_start = max(0, len(lines) - _CHROME_SEARCH_WINDOW)
    for i in range(search_start, len(lines) - 1):
        if not _is_chrome_separator(lines[i]):
            continue
        if not lines[i + 1].lstrip().startswith("❯"):
            continue
        scan_end = min(i + 2 + _CHROME_INPUT_MAX_LINES, len(lines))
        for j in range(i + 2, scan_end):
            if _is_chrome_separator(lines[j]):
                return True
        return False
    return False


def _scan_status_row(pane_text: str) -> str | None:
    """Internal: return the raw stripped status row above the input
    chrome (including its leading spinner glyph), or None if the
    pane has no chrome / nothing scannable above it.
    """
    if not pane_text:
        return None
    lines = pane_text.split("\n")
    chrome_idx = _find_chrome_separator(lines)
    if chrome_idx is None:
        return None
    scan_floor = max(chrome_idx - _STATUS_SCAN_WINDOW, -1)
    for i in range(chrome_idx - 1, scan_floor, -1):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in _STATUS_SPINNERS:
            return stripped
        # First non-blank, non-spinner line: bail.
        return None
    return None


def parse_status_line(pane_text: str) -> str | None:
    """Return the spinner-row text above the input chrome.

    Unlike claude-code-state's version, this returns the row whether
    or not it contains the running-status `…` ellipsis. Completion
    summaries ("Worked for 56s") and running statuses ("Thinking…
    16s") both come back; the caller decides what `…` presence means.

    Returns None when the pane has no chrome, or when the row above
    chrome is blank / not a spinner row.
    """
    row = _scan_status_row(pane_text)
    if row is None:
        return None
    return row[1:].strip()


def parse_status_glyph(pane_text: str) -> str | None:
    """Return just the leading spinner glyph from the status row.

    Symmetric with `parse_status_line` but returns the single-char
    glyph (`·` / `✻` / `✽` / `✶` / `*` etc) rather than the text
    after it. None when no status row is present.
    """
    row = _scan_status_row(pane_text)
    if row is None:
        return None
    return row[0]


def extract_between_rules(pane_text: str) -> str:
    """Concatenate lines between the most recent pair of ``────`` rows.

    Returns "" when fewer than two rule rows are present.
    """
    if not pane_text:
        return ""
    lines = pane_text.split("\n")
    rule_indices = [i for i, line in enumerate(lines) if _is_chrome_separator(line)]
    if len(rule_indices) < 2:
        return ""
    top, bottom = rule_indices[-2], rule_indices[-1]
    return "\n".join(lines[top + 1 : bottom])


# ---------------------------------------------------------------------------
# tmux IO primitives
# ---------------------------------------------------------------------------


def resolve_pane_id(tmux_session: str) -> str:
    """Look up the pane id of window 0 in *tmux_session*.

    Shells out to ``tmux display-message``. Raises TmuxResolutionError
    when the binary is missing, the session does not exist, or the
    session has no window 0.
    """
    try:
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-t",
                f"{tmux_session}:0",
                "-p",
                "#{pane_id}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise TmuxResolutionError(f"tmux binary not on PATH: {e}") from e
    if result.returncode != 0:
        raise TmuxResolutionError(
            f"tmux display-message failed for session "
            f"{tmux_session!r}: {result.stderr.strip()}"
        )
    pane_id = result.stdout.strip()
    if not pane_id:
        raise TmuxResolutionError(
            f"tmux returned empty pane_id for session {tmux_session!r}"
        )
    return pane_id


def capture_pane(pane_id: str) -> str:
    """Capture the visible content of a tmux pane.

    Uses ``tmux capture-pane -p -J -t <pane_id>`` (-J joins wrapped
    lines so the ``────`` chrome separator survives; no ``-e``, ANSI
    escapes are excluded).

    Raises PaneCaptureError when the binary is missing or
    capture-pane returns non-zero (pane gone, server crashed, etc.).
    """
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", pane_id],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise PaneCaptureError(f"tmux binary not on PATH: {e}") from e
    if result.returncode != 0:
        raise PaneCaptureError(
            f"tmux capture-pane failed for pane {pane_id!r}: {result.stderr.strip()}"
        )
    return result.stdout
