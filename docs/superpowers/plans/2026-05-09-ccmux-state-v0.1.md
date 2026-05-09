# ccmux-state v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or executing-plans-test-first to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the v0.1 design specified in
[`docs/superpowers/specs/2026-05-08-ccmux-state-design.md`](../specs/2026-05-08-ccmux-state-design.md):
a single-tmux-session state monitor that consumes claude-tap events,
refines them with narrowly-scoped pane reads, and exposes Idle /
Working / Blocked / Dead state via `current` snapshot and async-for
iteration.

**Architecture:** Pure data types (`state.py`) and pure logic
(`kind.py`, `derive.py`) at the bottom; pane / tmux IO primitives
(`pane.py`) in the middle; async event-loop machinery
(`monitor.py`) at the top. Two concurrent asyncio tasks share
`pane_id` and `kind` state: a tap consumer that updates them on
incoming events, and a poll loop that captures the pane every
`poll_interval` seconds, derives a State, and yields when it
differs from `current`.

**Tech Stack:** Python 3.11+, asyncio, hatchling build backend,
`claude-tap` (git URL runtime dep, provides
`claude_tap.EventStream`), tmux subprocess (`capture-pane`,
`display-message`), pytest + pytest-asyncio for tests, ruff for
lint and format.

---

## File structure

```text
src/ccmux_state/
├── __init__.py        # public API exports (already exists, will rewrite in Task 10)
├── _version.py        # already exists
├── state.py           # Idle / Working / Blocked / Dead frozen dataclasses
├── errors.py          # CCMuxStateError, TmuxResolutionError, PaneCaptureError
├── pane.py            # has_input_chrome, parse_status_line, capture_pane,
│                      # resolve_pane_id, extract_between_rules
├── kind.py            # Kind type alias + kind_from_pane (cold-start) +
│                      # kind_from_event (transitions)
├── derive.py          # derive_state(kind, pane_text) -> State
└── monitor.py         # SessionMonitor async context manager

tests/
├── conftest.py                  # already exists, will add helpers in Task 8
├── test_skeleton.py             # already exists; left untouched
├── test_state.py                # state dataclass equality tests
├── test_errors.py               # error-class trivial tests
├── fixtures/
│   ├── pane_working.txt
│   ├── pane_idle_completion.txt
│   ├── pane_idle_empty.txt
│   ├── pane_blocked_permission.txt
│   └── pane_no_chrome.txt
├── test_pane.py                 # primitives, fixture-driven
├── test_kind.py                 # kind state machine, table-driven
├── test_derive.py               # derive_state, table-driven
└── test_monitor.py              # SessionMonitor integration with mocked IO
```

## Branching

Before Task 1: `git checkout -b feature/v0.1-implementation dev`
After Task 10: `git checkout dev && git merge --no-ff feature/v0.1-implementation`

All commits inside this plan land on `feature/v0.1-implementation`.
Conventional commit prefixes; include
`Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
in every commit body.

---

## Task 1: State dataclasses

**Files:**

- Create: `src/ccmux_state/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
from ccmux_state.state import Idle, Working, Blocked, Dead


def test_idle_default_text_is_empty():
    assert Idle().text == ""


def test_idle_with_text():
    assert Idle(text="✻ Churned for 55s").text == "✻ Churned for 55s"


def test_working_default_text_is_empty():
    assert Working().text == ""


def test_blocked_requires_tool_name():
    b = Blocked(tool_name="AskUserQuestion")
    assert b.tool_name == "AskUserQuestion"
    assert b.content == ""


def test_blocked_with_content():
    b = Blocked(tool_name="Bash", content="Read(/etc/passwd)")
    assert b.content == "Read(/etc/passwd)"


def test_dead_carries_last_state():
    d = Dead(reason="session_end", last_state=Idle(text="bye"))
    assert d.reason == "session_end"
    assert d.last_state == Idle(text="bye")


def test_states_are_distinct_by_type():
    assert Idle() != Working()
    assert Working(text="x") != Idle(text="x")


def test_states_are_value_equal():
    assert Idle(text="a") == Idle(text="a")
    assert Working(text="b") == Working(text="b")
    assert Blocked(tool_name="t", content="c") == Blocked(tool_name="t", content="c")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_state.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ccmux_state.state'`.

- [ ] **Step 3: Implement state.py**

```python
# src/ccmux_state/state.py
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

    `tool_name` comes from the `claude-tap` permission_request event
    (e.g. "AskUserQuestion", "ExitPlanMode", "Bash"), or
    "unknown" if cold-start saw the dialog before any event arrived.
    `content` is the dialog body extracted between the most recent
    pair of `────` horizontal rules.
    """

    tool_name: str
    content: str = ""


@dataclass(frozen=True)
class Dead:
    """Terminal state. Emitted once, then the iterator ends."""

    reason: str  # "session_end" | "pane_lost" | "tmux_unavailable"
    last_state: "State"


State = Idle | Working | Blocked | Dead
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_state.py -v
```

Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_state/state.py tests/test_state.py
git commit -m "$(cat <<'EOF'
feat(state): add Idle / Working / Blocked / Dead frozen dataclasses

Public state types for SessionMonitor's iterator output. Per the
v0.1 spec each carries an optional pane-derived detail string;
Blocked also carries tool_name (from claude-tap permission_request
events). Dead carries the last successful state so consumers don't
lose context on termination.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Typed errors

**Files:**

- Create: `src/ccmux_state/errors.py`
- Create: `tests/test_errors.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_errors.py
import pytest

from ccmux_state.errors import (
    CCMuxStateError,
    PaneCaptureError,
    TmuxResolutionError,
)


def test_resolution_error_is_ccmux_state_error():
    assert issubclass(TmuxResolutionError, CCMuxStateError)


def test_capture_error_is_ccmux_state_error():
    assert issubclass(PaneCaptureError, CCMuxStateError)


def test_resolution_error_carries_message():
    with pytest.raises(TmuxResolutionError, match="no such session"):
        raise TmuxResolutionError("no such session 'foo'")


def test_capture_error_carries_message():
    with pytest.raises(PaneCaptureError, match="capture failed"):
        raise PaneCaptureError("capture failed for %3")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_errors.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ccmux_state.errors'`.

- [ ] **Step 3: Implement errors.py**

```python
# src/ccmux_state/errors.py
from __future__ import annotations


class CCMuxStateError(Exception):
    """Base exception for ccmux-state."""


class TmuxResolutionError(CCMuxStateError):
    """tmux could not resolve the requested session, window, or pane.

    Raised at cold-start (`SessionMonitor.__aenter__`) when
    `tmux display-message` cannot find the requested session or
    window 0. The monitor never starts in this case.
    """


class PaneCaptureError(CCMuxStateError):
    """tmux capture-pane failed for a known pane id.

    Raised by `capture_pane`. Mid-life occurrences are caught by the
    poll loop and surfaced as `Dead(reason="pane_lost")`.
    """
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_errors.py -v
```

Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_state/errors.py tests/test_errors.py
git commit -m "$(cat <<'EOF'
feat(errors): add typed exceptions for tmux resolution and pane capture

TmuxResolutionError surfaces cold-start failures (the monitor never
starts). PaneCaptureError surfaces mid-life capture failures (the
poll loop catches it and emits Dead). Both inherit from
CCMuxStateError so consumers can catch either with one except.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Pane primitives + fixtures

**Files:**

- Create: `src/ccmux_state/pane.py`
- Create: `tests/fixtures/pane_working.txt`
- Create: `tests/fixtures/pane_idle_completion.txt`
- Create: `tests/fixtures/pane_idle_empty.txt`
- Create: `tests/fixtures/pane_blocked_permission.txt`
- Create: `tests/fixtures/pane_no_chrome.txt`
- Create: `tests/test_pane.py`

This is the biggest task because the pane primitives are the
foundation for everything that follows. The four parsing functions
(`has_input_chrome`, `parse_status_line`, `extract_between_rules`,
`resolve_pane_id`, `capture_pane`) are intentionally simple ports
of cc-state's empirically-tested logic, simplified for v0.1's
narrower needs (no UI pattern table, no user-config overrides, no
TodoWrite/overlay collection).

- [ ] **Step 1: Create fixture files**

```text
# tests/fixtures/pane_working.txt
Some prior content

✻ Thinking… (16s · ↑ 827 tokens · thought for 7s)

────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)
```

```text
# tests/fixtures/pane_idle_completion.txt
Some prior content

✻ Worked for 56s

────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)
```

```text
# tests/fixtures/pane_idle_empty.txt
Some prior content

────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)
```

```text
# tests/fixtures/pane_blocked_permission.txt
Some prior content

────────────────────────────────────────────────────────────────────────────────
 Read file

  Read(/etc/passwd)

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, allow reading from etc/ during this session
   3. No

 Esc to cancel · Tab to amend
────────────────────────────────────────────────────────────────────────────────
```

```text
# tests/fixtures/pane_no_chrome.txt
Some prior content

A bare bash session, no Claude Code chrome anywhere.
```

The `────` separators in fixtures must be exactly 80 dashes (the
length cc-state empirically observed). Use `python -c "print('─'*80)"`
to generate them if your editor does not preserve the
character verbatim.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_pane.py
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


def test_resolve_pane_id_raises_on_missing_session(monkeypatch):
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
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
uv run pytest tests/test_pane.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ccmux_state.pane'`.

- [ ] **Step 4: Implement pane.py**

```python
# src/ccmux_state/pane.py
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

# Spinner glyphs Claude Code uses on its status row.
_STATUS_SPINNERS = frozenset(["·", "✻", "✽", "✶", "✳", "✢"])


def _is_chrome_separator(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= _CHROME_MIN_LEN and all(c == "─" for c in stripped)


def _find_chrome_separator(
    lines: list[str], window: int = _CHROME_SEARCH_WINDOW
) -> int | None:
    """Index of the topmost `────` line within the last *window* lines."""
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

    Pattern: a `────` separator, followed by a `❯`-prefixed prompt row
    (with up to a few continuation rows for multi-line input), then a
    second `────` separator closing the sandwich.
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


def parse_status_line(pane_text: str) -> str | None:
    """Return the spinner-row text above the input chrome.

    Unlike claude-code-state's version, this returns the row whether
    or not it contains the running-status `…` ellipsis. Completion
    summaries ("Worked for 56s") and running statuses ("Thinking…
    16s") both come back; the caller decides what `…` presence
    means.

    Returns None when the pane has no chrome, or when the row above
    chrome is blank / not a spinner row.
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
            return stripped[1:].strip()
        # First non-blank, non-spinner line: bail.
        return None
    return None


def extract_between_rules(pane_text: str) -> str:
    """Concatenate lines between the most recent pair of `────` rows.

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
    """Look up the pane id of window 0 in `tmux_session`.

    Shells out to `tmux display-message`. Raises TmuxResolutionError
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

    Uses `tmux capture-pane -p -J -t <pane_id>` (-J joins wrapped
    lines so the `────` chrome separator survives; no `-e`, ANSI
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
            f"tmux capture-pane failed for pane {pane_id!r}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
uv run pytest tests/test_pane.py -v
```

Expected: PASS, 14 tests. The `resolve_pane_id` and `capture_pane`
failure tests require `tmux` to be on the PATH (they exercise the
real binary's error paths). If tmux is not installed in the test
environment, those two tests will instead surface the "binary not
on PATH" branch, which raises the same exception types — still
PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ccmux_state/pane.py tests/test_pane.py tests/fixtures
git commit -m "$(cat <<'EOF'
feat(pane): port has_input_chrome, parse_status_line; add
extract_between_rules, resolve_pane_id, capture_pane

Three pane parsers ported from claude-code-state's parser.py with
the elaborate user-config / overlay / todo-collection logic
stripped out (v0.1 does not need it). parse_status_line is
modified to return spinner rows regardless of `…` so derive_state
can distinguish running vs completion in one place.

Two tmux IO primitives are new:
- resolve_pane_id wraps `tmux display-message -t <session>:0`
- capture_pane wraps `tmux capture-pane -p -J -t <pane_id>`

Both raise typed errors on tmux failures rather than swallowing
them (cc-state's capture returns "" on failure; we want the caller
to know).

Five fixture pane samples committed under tests/fixtures/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: kind cold-start heuristic

**Files:**

- Create: `src/ccmux_state/kind.py` (initial version)
- Create: `tests/test_kind.py`

The "kind" is the internal classifier that drives `derive_state`'s
parsing strategy. Cold-start derives kind from the pane alone (no
events seen yet); subsequent transitions are event-driven and
covered in Task 5.

We model `Kind` as a tagged union via tuples for simplicity:

- `("idle",)` — nothing in flight
- `("working",)` — a turn is in flight
- `("blocked", tool_name)` — a dialog has the input

Pattern matching in derive_state and the monitor will use Python's
`match` statement against these tuples.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kind.py
from pathlib import Path

from ccmux_state.kind import Kind, kind_from_pane

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


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
```

The "no chrome -> idle" branch in the last two tests is a slight
divergence from the spec, which says "no chrome -> blocked". We
override here because a bare-bash pane would otherwise wedge the
monitor in `Blocked(tool_name="unknown")` until tap proves
otherwise, and bare-bash is the most likely meaning of "no chrome
on cold-start when no Claude is running". Re-read the cold-start
section of the spec carefully and revise if you disagree; if you
agree, update the spec to match this behaviour.

- [ ] **Step 2: Re-read the spec and reconcile**

Open
[`docs/superpowers/specs/2026-05-08-ccmux-state-design.md`](../specs/2026-05-08-ccmux-state-design.md)
and locate the Cold-start section. The spec currently reads:

> pane has no input chrome → `kind = blocked(tool_name="unknown")`

The implementation diverges: bare-bash panes (no chrome, no
dialog) become `("idle",)`. The distinction "no chrome AND looks
like a dialog" vs "no chrome AND looks like bare bash" is a
heuristic we are choosing to skip in v0.1.

Update the spec inline so the contract is explicit:

```markdown
   - pane has no input chrome →
     - if a dialog body is present (any non-blank line between two
       `────` rules other than the chrome itself) →
       `kind = blocked(tool_name="unknown")`. The next
       `permission_request` event refines `tool_name`.
     - else → `kind = idle`. Treats bare-bash panes (no Claude
       running) as idle so the monitor does not wedge.
```

Commit the spec update separately so the evolution is traceable:

```bash
git add docs/superpowers/specs/2026-05-08-ccmux-state-design.md
git commit -m "$(cat <<'EOF'
docs(spec): clarify cold-start "no chrome" branch

The original wording mapped any chrome-less pane to
Blocked(tool_name="unknown"), which mis-classifies bare-bash panes
where no Claude is running. Split the branch: dialog body present
(content between two ───── rules) -> Blocked; otherwise -> Idle.
The implementation in kind_from_pane follows this revised contract.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
uv run pytest tests/test_kind.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ccmux_state.kind'`.

- [ ] **Step 4: Implement kind.py (cold-start only)**

```python
# src/ccmux_state/kind.py
"""Kind: the internal pre-pane-refinement classifier.

A Kind is one of:
- ("idle",)
- ("working",)
- ("blocked", tool_name: str)

Two derivation paths:
- kind_from_pane: cold-start from pane text alone (this module, below)
- kind_from_event: incremental update from claude-tap events (Task 5)
"""

from __future__ import annotations

from typing import Literal

from ccmux_state.pane import (
    extract_between_rules,
    has_input_chrome,
    parse_status_line,
)

Kind = (
    tuple[Literal["idle"]]
    | tuple[Literal["working"]]
    | tuple[Literal["blocked"], str]
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
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
uv run pytest tests/test_kind.py -v
```

Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
git add src/ccmux_state/kind.py tests/test_kind.py
git commit -m "$(cat <<'EOF'
feat(kind): add Kind type and cold-start heuristic kind_from_pane

Kind is a tagged tuple union: ("idle",) | ("working",) |
("blocked", tool_name). The cold-start derivation runs the pane
through has_input_chrome + parse_status_line + extract_between_rules
to produce one of these without needing any tap event.

Includes the spec revision committed in Step 2: chrome-less panes
without a dialog body are Idle (bare-bash case), not Blocked.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: kind event-driven transitions

**Files:**

- Modify: `src/ccmux_state/kind.py` (append)
- Modify: `tests/test_kind.py` (append)

`kind_from_event` is a pure function that takes the prior kind and
a `pending_tool` memo and returns the new kind plus updated memo.
Keeping it pure makes the state machine fully unit-testable
without async machinery.

- [ ] **Step 1: Write failing tests for the event transitions**

Append to `tests/test_kind.py`:

```python
# tests/test_kind.py (append)
from ccmux_state.kind import kind_from_event


def _ev(event_type: str, **payload):
    """Build a minimal tap event dict suitable for kind_from_event."""
    return {
        "event_type": event_type,
        "claude": {"session_id": "s"},
        "tmux": {"session_name": "tmux-s", "pane_id": "%1"},
        "payload": payload,
    }


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
    bring us out of Blocked. (Edge case: a queued Bash returned mid-
    permission-prompt for a different tool.)"""
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_kind.py -v
```

Expected: FAIL with `ImportError: cannot import name 'kind_from_event'`.

- [ ] **Step 3: Implement kind_from_event**

Append to `src/ccmux_state/kind.py`:

```python
# src/ccmux_state/kind.py (append)
import sys


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
        # Caller (the monitor) handles termination by checking event_type
        # before calling kind_from_event. If we see it here it's a no-op.
        return prev_kind, pending_tool
    print(f"ccmux-state: unknown tap event_type {et!r}", file=sys.stderr)
    return prev_kind, pending_tool
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_kind.py -v
```

Expected: PASS, 15 tests (6 from Task 4 + 9 here).

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_state/kind.py tests/test_kind.py
git commit -m "$(cat <<'EOF'
feat(kind): add kind_from_event for tap-event-driven transitions

Pure function: (prev_kind, pending_tool, event) -> (new_kind,
new_pending_tool). Encodes the kind state-machine table from the
v0.1 spec; permission_request remembers the tool_name in
pending_tool so the matching post_tool_use can transition back to
working without ambiguity. Unknown event_types log a stderr line
and pass through unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: derive_state

**Files:**

- Create: `src/ccmux_state/derive.py`
- Create: `tests/test_derive.py`

`derive_state(kind, pane_text) -> State` is the second pure
function in the data flow: kind in, refined pane-aware State out.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_derive.py
from pathlib import Path

from ccmux_state.derive import derive_state
from ccmux_state.state import Blocked, Idle, Working

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


def test_idle_kind_with_completion_summary():
    """Pane shows 'Worked for 56s' above chrome -> Idle with that text."""
    state = derive_state(("idle",), _read("pane_idle_completion.txt"))
    assert isinstance(state, Idle)
    assert "Worked for 56s" in state.text


def test_idle_kind_with_empty_chrome():
    state = derive_state(("idle",), _read("pane_idle_empty.txt"))
    assert state == Idle(text="")


def test_working_kind_with_running_spinner():
    state = derive_state(("working",), _read("pane_working.txt"))
    assert isinstance(state, Working)
    assert "Thinking…" in state.text
    assert "16s" in state.text


def test_working_kind_with_completion_downgrades_to_idle():
    """Esc-interrupt: events say working, but pane shows completion
    summary (no `…`). Trust the screen and emit Idle."""
    state = derive_state(("working",), _read("pane_idle_completion.txt"))
    assert isinstance(state, Idle)
    assert "Worked for 56s" in state.text


def test_working_kind_with_blank_above_chrome_downgrades_to_empty_idle():
    state = derive_state(("working",), _read("pane_idle_empty.txt"))
    assert state == Idle(text="")


def test_blocked_kind_extracts_dialog_body():
    state = derive_state(
        ("blocked", "AskUserQuestion"),
        _read("pane_blocked_permission.txt"),
    )
    assert isinstance(state, Blocked)
    assert state.tool_name == "AskUserQuestion"
    assert "Do you want to proceed?" in state.content


def test_blocked_kind_keeps_tool_name_unknown():
    state = derive_state(
        ("blocked", "unknown"),
        _read("pane_blocked_permission.txt"),
    )
    assert isinstance(state, Blocked)
    assert state.tool_name == "unknown"
    assert "Do you want to proceed?" in state.content


def test_blocked_kind_empty_content_when_no_rules_in_pane():
    state = derive_state(("blocked", "Bash"), _read("pane_no_chrome.txt"))
    assert state == Blocked(tool_name="Bash", content="")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_derive.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ccmux_state.derive'`.

- [ ] **Step 3: Implement derive.py**

```python
# src/ccmux_state/derive.py
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
    # Unreachable; typecheckers know Kind is exhaustive above.
    raise AssertionError(f"unreachable kind: {kind!r}")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_derive.py -v
```

Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_state/derive.py tests/test_derive.py
git commit -m "$(cat <<'EOF'
feat(derive): add derive_state(kind, pane_text) -> State

The second pure function in the data flow. Encodes the
Kind-to-State refinement rules from the v0.1 spec, including the
Esc-interrupt downgrade (kind=working but spinner has no `…` ->
Idle).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: SessionMonitor cold-start

**Files:**

- Create: `src/ccmux_state/monitor.py` (initial version, cold-start only)
- Create: `tests/test_monitor.py` (initial scope)

This task implements the constructor, `__aenter__`, the `current`
property, and the `__aiter__` skeleton (not yet driven). The two
background tasks (tap consumer, poll loop) come in Task 8.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_monitor.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ccmux_state.errors import TmuxResolutionError
from ccmux_state.monitor import SessionMonitor
from ccmux_state.state import Idle, Working

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


@pytest.mark.asyncio
async def test_cold_start_idle_when_pane_empty_chrome():
    with (
        patch("ccmux_state.monitor.resolve_pane_id", return_value="%1"),
        patch(
            "ccmux_state.monitor.capture_pane",
            return_value=_read("pane_idle_empty.txt"),
        ),
        patch("ccmux_state.monitor._spawn_background_tasks", return_value=None),
    ):
        async with SessionMonitor(tmux_session="ccmux-test") as m:
            assert m.current == Idle(text="")


@pytest.mark.asyncio
async def test_cold_start_working_when_pane_has_running_spinner():
    with (
        patch("ccmux_state.monitor.resolve_pane_id", return_value="%2"),
        patch(
            "ccmux_state.monitor.capture_pane",
            return_value=_read("pane_working.txt"),
        ),
        patch("ccmux_state.monitor._spawn_background_tasks", return_value=None),
    ):
        async with SessionMonitor(tmux_session="ccmux-test") as m:
            assert isinstance(m.current, Working)
            assert "Thinking…" in m.current.text


@pytest.mark.asyncio
async def test_cold_start_raises_on_tmux_resolution_failure():
    def _raise(_):
        raise TmuxResolutionError("no such session 'ccmux-bogus'")

    with (
        patch("ccmux_state.monitor.resolve_pane_id", side_effect=_raise),
        patch("ccmux_state.monitor._spawn_background_tasks", return_value=None),
    ):
        with pytest.raises(TmuxResolutionError):
            async with SessionMonitor(tmux_session="ccmux-bogus"):
                pytest.fail("monitor entered despite resolution failure")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_monitor.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ccmux_state.monitor'`.

- [ ] **Step 3: Implement monitor.py (cold-start scaffold)**

```python
# src/ccmux_state/monitor.py
"""SessionMonitor: async context manager that exposes Claude state per
tmux session. v0.1 implementation; see
docs/superpowers/specs/2026-05-08-ccmux-state-design.md for the
contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from ccmux_state.derive import derive_state
from ccmux_state.kind import Kind, kind_from_pane
from ccmux_state.pane import capture_pane, resolve_pane_id
from ccmux_state.state import State

if TYPE_CHECKING:
    from pathlib import Path


_DEFAULT_POLL_INTERVAL = 1.0


def _spawn_background_tasks(monitor: "SessionMonitor") -> None:
    """Hook for Task 8 to spawn the tap consumer and poll loop tasks.

    Tested as a patch target during Task 7's cold-start tests so we
    can verify __aenter__ works without any background work running.
    """
    return None


class SessionMonitor:
    """Monitor a single Claude Code session by tmux session name.

    Cold-start (`__aenter__`) resolves the pane via `tmux
    display-message` and produces an initial State from the captured
    pane alone. Subsequent updates flow from the tap consumer and
    poll loop tasks (Task 8) and surface via the `current` property
    and `async for state in monitor` iteration.
    """

    def __init__(
        self,
        tmux_session: str,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        tap_events_path: "Path | None" = None,
    ) -> None:
        self.tmux_session = tmux_session
        self.poll_interval = poll_interval
        self.tap_events_path = tap_events_path

        # Internal state (populated in __aenter__)
        self._pane_id: str = ""
        self._kind: Kind = ("idle",)
        self._pending_tool: str | None = None
        self._current: State | None = None

    async def __aenter__(self) -> "SessionMonitor":
        # Cold-start: tmux discovery + pane capture + initial state.
        self._pane_id = resolve_pane_id(self.tmux_session)
        pane_text = capture_pane(self._pane_id)
        self._kind = kind_from_pane(pane_text)
        self._current = derive_state(self._kind, pane_text)
        # Hook for Task 8.
        _spawn_background_tasks(self)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Cleanup is filled in in Task 9.
        return None

    @property
    def current(self) -> State:
        """Latest known State. Set by __aenter__ and updated by the
        poll loop (Task 8)."""
        if self._current is None:
            raise RuntimeError(
                "SessionMonitor.current accessed before __aenter__ completed"
            )
        return self._current

    def __aiter__(self) -> AsyncIterator[State]:
        # Real implementation in Task 8.
        raise NotImplementedError("Task 8 wires the iterator")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_monitor.py -v
```

Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/ccmux_state/monitor.py tests/test_monitor.py
git commit -m "$(cat <<'EOF'
feat(monitor): SessionMonitor scaffold and cold-start

__init__ stores tmux_session and configurable knobs.
__aenter__ resolves the pane via tmux discovery, captures it once,
and derives the initial Kind + State so `current` is immediately
populated. Background tasks (tap consumer, poll loop) are stubbed
behind _spawn_background_tasks for Task 8 to fill in;
__aiter__ raises NotImplementedError until then.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: SessionMonitor tap consumer + poll loop

**Files:**

- Modify: `src/ccmux_state/monitor.py`
- Modify: `tests/test_monitor.py` (append integration tests)
- Modify: `tests/conftest.py` (add helpers)

The two background tasks share the monitor's internal state
(`_pane_id`, `_kind`, `_pending_tool`, `_current`). They communicate
with the consumer iterator through an `asyncio.Queue` that holds
the next State to yield.

- [ ] **Step 1: Add test helpers to conftest.py**

```python
# tests/conftest.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any


class FakeEventStream:
    """A scriptable stand-in for claude_tap.EventStream.

    Pass a list of events at construction. The iterator yields them
    one at a time. To simulate "no more events for now", call
    `pause_after(n)` and `resume()` later from the test, or rely on
    the consumer cancelling the iterator.
    """

    def __init__(self, events: list[dict[str, Any]]):
        self._events = list(events)
        self._closed = False

    def close(self) -> None:
        self._closed = True

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for ev in self._events:
            if self._closed:
                return
            yield ev
            await asyncio.sleep(0)  # let other tasks run
        # Hold open so the monitor's tap consumer task does not exit
        # before the test cancels it.
        while not self._closed:
            await asyncio.sleep(0.01)
```

- [ ] **Step 2: Write failing integration tests**

Append to `tests/test_monitor.py`:

```python
# tests/test_monitor.py (append)
import asyncio

from ccmux_state.state import Blocked


def _ev(event_type, tmux_session="ccmux-test", pane_id="%1", **payload):
    return {
        "event_type": event_type,
        "claude": {"session_id": "abc", "transcript_path": "", "cwd": ""},
        "tmux": {
            "session_name": tmux_session,
            "window_id": "@1",
            "pane_id": pane_id,
        },
        "payload": payload,
    }


@pytest.mark.asyncio
async def test_iterator_yields_initial_state(monkeypatch):
    """First iteration always yields the cold-start state."""
    pane_seq = [_read("pane_idle_empty.txt")]

    def _capture(_):
        return pane_seq[-1]  # always latest

    fake = FakeEventStream(events=[])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr("ccmux_state.monitor.capture_pane", _capture)
    monkeypatch.setattr("ccmux_state.monitor._make_event_stream", lambda *a, **k: fake)

    async with SessionMonitor(
        tmux_session="ccmux-test", poll_interval=0.01
    ) as m:
        first = await asyncio.wait_for(m.__aiter__().__anext__(), timeout=1.0)
        assert first == Idle(text="")


@pytest.mark.asyncio
async def test_iterator_dedupes_unchanged_state(monkeypatch):
    """If pane and kind do not change, consecutive ticks do not yield."""
    pane_seq = [_read("pane_idle_empty.txt")]
    fake = FakeEventStream(events=[])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr(
        "ccmux_state.monitor.capture_pane", lambda _: pane_seq[-1]
    )
    monkeypatch.setattr(
        "ccmux_state.monitor._make_event_stream", lambda *a, **k: fake
    )

    async with SessionMonitor(
        tmux_session="ccmux-test", poll_interval=0.01
    ) as m:
        it = m.__aiter__()
        first = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert first == Idle(text="")
        # Wait a bit; expect no further yield.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(it.__anext__(), timeout=0.05)


@pytest.mark.asyncio
async def test_user_prompt_submit_transitions_to_working(monkeypatch):
    """Tap event flips kind, next pane snapshot reflects working."""
    panes = {"current": _read("pane_idle_empty.txt")}

    def _capture(_):
        return panes["current"]

    fake = FakeEventStream(events=[_ev("user_prompt_submit", prompt="hi")])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr("ccmux_state.monitor.capture_pane", _capture)
    monkeypatch.setattr(
        "ccmux_state.monitor._make_event_stream", lambda *a, **k: fake
    )

    async with SessionMonitor(
        tmux_session="ccmux-test", poll_interval=0.01
    ) as m:
        it = m.__aiter__()
        # First yield is the cold-start Idle.
        first = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert first == Idle(text="")
        # Tap event flips _kind to ("working",); pane is still empty,
        # so derive_state with kind=working + no spinner -> Idle (the
        # Esc-style downgrade applies here too — events lead but
        # screen has no spinner yet).
        # Now flip the pane to working text.
        panes["current"] = _read("pane_working.txt")
        second = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert isinstance(second, Working)
        assert "Thinking…" in second.text


@pytest.mark.asyncio
async def test_permission_request_transitions_to_blocked(monkeypatch):
    panes = {"current": _read("pane_idle_empty.txt")}
    fake = FakeEventStream(
        events=[
            _ev("user_prompt_submit"),
            _ev("permission_request", tool_name="AskUserQuestion"),
        ]
    )

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr(
        "ccmux_state.monitor.capture_pane", lambda _: panes["current"]
    )
    monkeypatch.setattr(
        "ccmux_state.monitor._make_event_stream", lambda *a, **k: fake
    )

    async with SessionMonitor(
        tmux_session="ccmux-test", poll_interval=0.01
    ) as m:
        it = m.__aiter__()
        # Burn the cold-start Idle.
        await asyncio.wait_for(it.__anext__(), timeout=1.0)
        panes["current"] = _read("pane_blocked_permission.txt")
        # Wait for kind to become blocked AND pane to flip.
        for _ in range(20):
            try:
                state = await asyncio.wait_for(it.__anext__(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if isinstance(state, Blocked):
                break
        assert isinstance(state, Blocked)
        assert state.tool_name == "AskUserQuestion"
        assert "Do you want to proceed?" in state.content
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
uv run pytest tests/test_monitor.py -v
```

Expected: FAIL — three new tests cannot import or pass because
`_make_event_stream` does not exist and `__aiter__` raises
NotImplementedError.

- [ ] **Step 4: Implement the tap consumer + poll loop**

Replace `monitor.py` entirely with the version below. The
key additions over Task 7: `_make_event_stream` factory (so tests
can patch it), `_tap_consumer_task`, `_poll_loop_task`, an
`asyncio.Queue[State]` that powers the iterator, and a real
`__aiter__` and `__aexit__`.

```python
# src/ccmux_state/monitor.py
"""SessionMonitor: async context manager that exposes Claude state per
tmux session. v0.1 implementation; see
docs/superpowers/specs/2026-05-08-ccmux-state-design.md for the
contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ccmux_state.derive import derive_state
from ccmux_state.errors import PaneCaptureError
from ccmux_state.kind import Kind, kind_from_event, kind_from_pane
from ccmux_state.pane import capture_pane, resolve_pane_id
from ccmux_state.state import Dead, State

if TYPE_CHECKING:
    from pathlib import Path


_DEFAULT_POLL_INTERVAL = 1.0


def _make_event_stream(tap_events_path: "Path | None"):
    """Factory for the tap event stream. Tests patch this."""
    from claude_tap import EventStream  # imported lazily

    return EventStream(path=tap_events_path, from_start=False)


def _spawn_background_tasks(monitor: "SessionMonitor") -> None:
    """Spawn the tap consumer and poll loop tasks on the monitor."""
    monitor._tasks = [
        asyncio.create_task(monitor._tap_consumer()),
        asyncio.create_task(monitor._poll_loop()),
    ]


class SessionMonitor:
    """Monitor a single Claude Code session by tmux session name."""

    def __init__(
        self,
        tmux_session: str,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        tap_events_path: "Path | None" = None,
    ) -> None:
        self.tmux_session = tmux_session
        self.poll_interval = poll_interval
        self.tap_events_path = tap_events_path

        self._pane_id: str = ""
        self._kind: Kind = ("idle",)
        self._pending_tool: str | None = None
        self._current: State | None = None
        self._last_yielded: State | None = None
        self._queue: asyncio.Queue[State] = asyncio.Queue()
        self._tasks: list[asyncio.Task[Any]] = []
        self._stream: Any = None
        self._stop_event = asyncio.Event()

    async def __aenter__(self) -> "SessionMonitor":
        self._pane_id = resolve_pane_id(self.tmux_session)
        pane_text = capture_pane(self._pane_id)
        self._kind = kind_from_pane(pane_text)
        self._current = derive_state(self._kind, pane_text)
        # Seed the queue with the initial state so the iterator's
        # first yield is the cold-start state.
        await self._queue.put(self._current)
        self._last_yielded = self._current
        _spawn_background_tasks(self)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._shutdown()

    @property
    def current(self) -> State:
        if self._current is None:
            raise RuntimeError(
                "SessionMonitor.current accessed before __aenter__ completed"
            )
        return self._current

    def __aiter__(self) -> AsyncIterator[State]:
        return self._iterator()

    async def _iterator(self) -> AsyncIterator[State]:
        while True:
            state = await self._queue.get()
            if isinstance(state, Dead):
                yield state
                return
            yield state

    async def _tap_consumer(self) -> None:
        try:
            self._stream = _make_event_stream(self.tap_events_path)
            async for event in self._stream:
                if self._stop_event.is_set():
                    return
                if not self._matches_session(event):
                    continue
                # Update pane_id (latest wins) and kind in lockstep.
                pane_id = (event.get("tmux") or {}).get("pane_id") or ""
                if pane_id:
                    self._pane_id = pane_id
                if event.get("event_type") == "session_end":
                    await self._die("session_end")
                    return
                self._kind, self._pending_tool = kind_from_event(
                    self._kind, self._pending_tool, event
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Defensive: a crash in the tap consumer must not
            # silently freeze the monitor. Surface as Dead so the
            # iterator terminates cleanly.
            await self._die("tap_consumer_crashed")

    def _matches_session(self, event: dict) -> bool:
        return (event.get("tmux") or {}).get("session_name") == self.tmux_session

    async def _poll_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(self.poll_interval)
                if self._stop_event.is_set():
                    return
                try:
                    pane_text = capture_pane(self._pane_id)
                except PaneCaptureError:
                    await self._die("pane_lost")
                    return
                state = derive_state(self._kind, pane_text)
                if state != self._last_yielded:
                    self._current = state
                    self._last_yielded = state
                    await self._queue.put(state)
        except asyncio.CancelledError:
            raise

    async def _die(self, reason: str) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        last = self._current
        if last is None:
            from ccmux_state.state import Idle

            last = Idle()
        dead = Dead(reason=reason, last_state=last)
        self._current = dead
        await self._queue.put(dead)

    async def _shutdown(self) -> None:
        self._stop_event.set()
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
uv run pytest tests/test_monitor.py -v
```

Expected: PASS, 7 tests in monitor (3 cold-start from Task 7 + 4
new). Note: the new tests rely on `_make_event_stream` being
patched. If a test accidentally hits the real claude-tap
EventStream and `~/.claude-tap/events.jsonl` exists, the test
hangs — that is a sign the patch missed and should be fixed in the
test, not in production code.

- [ ] **Step 6: Commit**

```bash
git add src/ccmux_state/monitor.py tests/test_monitor.py tests/conftest.py
git commit -m "$(cat <<'EOF'
feat(monitor): tap consumer + poll loop background tasks

Two asyncio tasks share the monitor's internal kind / pane_id and
cooperate via an asyncio.Queue. The tap consumer reads claude-tap
EventStream filtered by tmux.session_name, calls kind_from_event,
and updates pane_id (latest wins). The poll loop captures the pane
every poll_interval seconds, runs derive_state, and queues the
result when it differs from the last yielded value.

The cold-start state is enqueued in __aenter__ so the iterator's
first yield is always available immediately.

Includes a FakeEventStream test helper in conftest.py and three
integration tests covering iterator dedup, working transition, and
permission_request transition.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: SessionMonitor lifecycle (Dead and cancellation)

**Files:**

- Modify: `tests/test_monitor.py` (append)

The Dead-emission paths and cancellation safety are mostly written
in Task 8; this task adds tests for them.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_monitor.py`:

```python
# tests/test_monitor.py (append)
from ccmux_state.errors import PaneCaptureError
from ccmux_state.state import Dead


@pytest.mark.asyncio
async def test_pane_lost_emits_dead(monkeypatch):
    """When capture_pane starts raising mid-life, the monitor emits
    Dead and the iterator ends."""
    pane_state = {"alive": True}

    def _capture(_):
        if pane_state["alive"]:
            return _read("pane_idle_empty.txt")
        raise PaneCaptureError("pane gone")

    fake = FakeEventStream(events=[])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr("ccmux_state.monitor.capture_pane", _capture)
    monkeypatch.setattr(
        "ccmux_state.monitor._make_event_stream", lambda *a, **k: fake
    )

    async with SessionMonitor(
        tmux_session="ccmux-test", poll_interval=0.01
    ) as m:
        it = m.__aiter__()
        first = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert isinstance(first, Idle)

        pane_state["alive"] = False
        # Drive the iterator until Dead arrives.
        for _ in range(50):
            try:
                state = await asyncio.wait_for(it.__anext__(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if isinstance(state, Dead):
                break
        assert isinstance(state, Dead)
        assert state.reason == "pane_lost"

        # Iterator must end (StopAsyncIteration) on the next anext.
        with pytest.raises(StopAsyncIteration):
            await it.__anext__()


@pytest.mark.asyncio
async def test_session_end_event_emits_dead(monkeypatch):
    fake = FakeEventStream(events=[_ev("session_end")])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr(
        "ccmux_state.monitor.capture_pane",
        lambda _: _read("pane_idle_empty.txt"),
    )
    monkeypatch.setattr(
        "ccmux_state.monitor._make_event_stream", lambda *a, **k: fake
    )

    async with SessionMonitor(
        tmux_session="ccmux-test", poll_interval=0.01
    ) as m:
        it = m.__aiter__()
        await asyncio.wait_for(it.__anext__(), timeout=1.0)  # cold-start Idle
        for _ in range(50):
            try:
                state = await asyncio.wait_for(it.__anext__(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if isinstance(state, Dead):
                break
        assert isinstance(state, Dead)
        assert state.reason == "session_end"


@pytest.mark.asyncio
async def test_aexit_cleans_up_tasks(monkeypatch):
    fake = FakeEventStream(events=[])
    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr(
        "ccmux_state.monitor.capture_pane",
        lambda _: _read("pane_idle_empty.txt"),
    )
    monkeypatch.setattr(
        "ccmux_state.monitor._make_event_stream", lambda *a, **k: fake
    )

    monitor = SessionMonitor(tmux_session="ccmux-test", poll_interval=0.01)
    async with monitor:
        pass
    # All tasks must be done (cancelled or completed) after __aexit__.
    for task in monitor._tasks:
        assert task.done()
```

- [ ] **Step 2: Run tests to confirm they pass**

```bash
uv run pytest tests/test_monitor.py -v
```

Expected: PASS, 9 tests total. The lifecycle tests should pass
without further code changes — Task 8's monitor implementation
already handles `pane_lost`, `session_end`, and clean shutdown.

If any of the new tests fail, fix `monitor.py` until they pass —
do not modify the tests.

- [ ] **Step 3: Commit**

```bash
git add tests/test_monitor.py
git commit -m "$(cat <<'EOF'
test(monitor): cover Dead emission paths and __aexit__ cleanup

Three tests pin down the lifecycle contract:
- pane lost mid-life -> Dead("pane_lost"), iterator ends with
  StopAsyncIteration on the next anext
- session_end event -> Dead("session_end"), iterator ends similarly
- __aexit__ leaves all background tasks done (cancelled or
  completed)

No production code changes; Task 8's implementation already
satisfies these contracts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Public API exports + integration smoke test

**Files:**

- Modify: `src/ccmux_state/__init__.py`
- Modify: `tests/test_skeleton.py` (extend with public-API import test)

The package now has all the pieces; the `__init__` should expose
the public API listed in the spec.

- [ ] **Step 1: Update test_skeleton.py with public API expectations**

Replace `tests/test_skeleton.py` entirely:

```python
# tests/test_skeleton.py
def test_import_package():
    import ccmux_state

    assert ccmux_state.__version__ == "0.1.0"


def test_public_api_exports():
    from ccmux_state import (
        Blocked,
        CCMuxStateError,
        Dead,
        Idle,
        PaneCaptureError,
        SessionMonitor,
        State,
        TmuxResolutionError,
        Working,
    )

    # Each name resolves to a real object.
    assert SessionMonitor is not None
    assert Idle is not None
    assert Working is not None
    assert Blocked is not None
    assert Dead is not None
    assert State is not None
    assert CCMuxStateError is not None
    assert TmuxResolutionError is not None
    assert PaneCaptureError is not None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_skeleton.py -v
```

Expected: FAIL with `ImportError` for the new names.

- [ ] **Step 3: Implement `__init__.py` public API**

```python
# src/ccmux_state/__init__.py
"""ccmux-state: tmux-pane state monitor for Claude Code sessions
running under the ccmux convention."""

from ccmux_state._version import __version__
from ccmux_state.errors import (
    CCMuxStateError,
    PaneCaptureError,
    TmuxResolutionError,
)
from ccmux_state.monitor import SessionMonitor
from ccmux_state.state import Blocked, Dead, Idle, State, Working

__all__ = [
    "__version__",
    "Blocked",
    "CCMuxStateError",
    "Dead",
    "Idle",
    "PaneCaptureError",
    "SessionMonitor",
    "State",
    "TmuxResolutionError",
    "Working",
]
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_skeleton.py -v
```

Expected: PASS, 2 tests.

- [ ] **Step 5: Run the full test suite + lint pass**

```bash
uv run pytest -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run --with pyright pyright src/
```

Expected:

- pytest: every test passes (state, errors, pane, kind, derive,
  monitor, skeleton — roughly 60 tests total)
- ruff check: All checks passed
- ruff format --check: all files already formatted
- pyright: 0 errors

If ruff format fails, run `uv run ruff format src/ tests/` and
re-stage the changes.

- [ ] **Step 6: Commit**

```bash
git add src/ccmux_state/__init__.py tests/test_skeleton.py
git commit -m "$(cat <<'EOF'
feat: expose public API on ccmux_state package

Re-export SessionMonitor, the four State variants, the State
union alias, and the three exception types so consumers can
`from ccmux_state import SessionMonitor, Idle, Working, Blocked,
Dead`. Skeleton test now asserts the full public surface stays
importable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Merge feature branch back to dev**

```bash
git checkout dev
git merge --no-ff feature/v0.1-implementation
git branch -d feature/v0.1-implementation
git log --oneline --graph -15
```

Expected: dev tip is a merge commit with the feature branch's 10
commits underneath, on a side line.

A v0.1.0 release (release branch + tag + push) is intentionally
**out of scope** for this plan; it's a separate ritual covered by
the managing-git-branches skill.

---

## What this plan does NOT cover

- Cutting a `release/v0.1.0` branch, bumping version, and tagging.
  The version is already 0.1.0 in pyproject; once the
  implementation lands on dev and the user smoke-tests it
  manually, a one-shot release ceremony per the
  managing-git-branches skill produces the tag and pushes.
- Pushing to GitHub. The repo `wuwenrui555/ccmux-state` does not
  yet exist on GitHub. Creating it (via web UI or `gh repo
  create`) and the first push are out of scope here.
- Drift detection for unrecognised dialogs (the v0.1 spec marks it
  as a non-goal).
- A CLI daemon writing `~/.ccmux-state/events.jsonl` (also a
  non-goal in v0.1).
