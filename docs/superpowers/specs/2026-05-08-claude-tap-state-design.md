# claude-tap-state v0.1 design

## Context

`claude-tap` exposes a faithful event stream from Claude Code's hook
system, but the stream is low-level: 8 different `event_type`s, each
firing per turn, plus `permission_request` for blocking dialogs. A
typical consumer (a TUI dashboard, a chat backend, a notification
fanout) does not want to track every `pre_tool_use` and
`post_tool_use`; it wants to know "what state is this Claude session
in **right now**?" and have that answer kept current.

`claude-tap-state` is the smallest library that answers that
question. It consumes the `claude-tap` stream, classifies the session
into one of three coarse-grained kinds (`idle` / `working` /
`blocked`), and refines the answer with a narrowly-scoped read of
the tmux pane to extract live text (spinner row, dialog content). The
pane read is **scoped by the kind** — Working only looks at the
spinner row, Blocked only looks at the dialog area — so the brittle
"parse the whole screen" approach of pure-pane classification is
avoided.

The package is **strongly coupled to claude-tap**: it imports
`claude_tap.EventStream` and treats `~/.claude-tap/events.jsonl` as
its primary input. It is the successor to the broader
[claude-code-state](https://github.com/wuwenrui555/claude-code-state)
package, which classified state from pane parsing alone and is now
deprecated.

## Goals (v0.1)

- A consumer can ask "what state is the Claude in tmux session X?"
  and get a precise, pane-refined answer.
- A consumer can subscribe to state-change notifications via async
  iteration; the iterator yields only on actual state change.
- The library exposes both a snapshot accessor (`current`) and an
  async iterator (`async for state in monitor`).
- Single-session API. Multi-session support is the consumer's job
  (instantiate one `SessionMonitor` per tmux session).
- Detect Esc-interrupt as part of state derivation: if the kind is
  Working but the pane spinner has no `…`, downgrade to `Idle`.
- Tmux discovery at cold-start gives a usable `pane_id` and `kind`
  before any tap event arrives, so the iterator never has an
  "unknown" initial period.

## Convention

The library assumes **one tmux session = one window = one Claude
Code instance**. The user-facing identifier is the tmux session
name. Violating the convention (multiple windows or panes per
session, or mixing Claude with other commands in the same session)
is undefined behaviour: `SessionMonitor` will pick the first pane
of the first window and ignore the rest.

## Non-goals (v0.1)

- **No process-death detection beyond what tmux signals.** If the
  pane is gone or `capture-pane` fails, emit `Dead` and stop. We
  don't watch PIDs or do liveness pings.
- **No multi-session orchestration.** Consumers wanting a unified
  view across N sessions wire that up themselves.
- **No `drift` correction event** for unknown blocking dialogs. If
  Claude Code adds a new dialog type that `permission_request` does
  not surface, this library will misclassify. That's a maintenance
  task for a later version.
- **No CLI daemon.** v0.1 ships a Python library only. A daemon that
  writes `~/.claude-tap-state/events.jsonl` for cross-process consumers
  may come later.

## Public API

### `SessionMonitor`

```python
from claude_tap_state import SessionMonitor, Idle, Working, Blocked, Dead, State

async with SessionMonitor(
    tmux_session="ccmux-projA",                      # primary key
    poll_interval=1.0,                              # default
    tap_events_path=None,                           # default: ~/.claude-tap/events.jsonl
) as monitor:
    # snapshot anytime
    state: State = monitor.current

    # subscribe to changes
    async for state in monitor:
        match state:
            case Idle(text):
                ...
            case Working(text):
                ...
            case Blocked(tool_name, content):
                ...
            case Dead(reason, last_state):
                # iterator ends after Dead is emitted
                ...
```

### State types

```python
@dataclass(frozen=True)
class Idle:
    text: str = ""           # e.g. "✻ Churned for 55s", or "" before any turn

@dataclass(frozen=True)
class Working:
    text: str = ""           # e.g. "✻ Thinking… 16s · ↑ 827 tokens"

@dataclass(frozen=True)
class Blocked:
    tool_name: str           # "AskUserQuestion" / "ExitPlanMode" / "Bash" / ...
    content: str = ""        # text between the two ───── horizontal rules

@dataclass(frozen=True)
class Dead:
    reason: str              # "pane_lost" / "session_end" / "tmux_unavailable"
    last_state: State        # last successful classification before death

State = Idle | Working | Blocked | Dead
```

**Equality and dedup.** The async iterator yields when `current !=
previous_yielded` using full structural equality (kind + all detail
fields). The first iteration always yields (no previous value to
compare against), so a freshly-entered monitor always produces one
state immediately. Spinner-text ticking from `… 16s` to `… 17s`
counts as a change and produces a new yield. Consumers that want
kind-only notifications filter on their side.

## Architecture

```text
┌──────────────────────────────────────────────────────────┐
│  SessionMonitor(tmux_session, poll_interval)             │
│                                                          │
│   internal state:                                        │
│     pane_id: str    (initially from tmux discovery,      │
│                       then updated from each tap event)  │
│     kind: idle | working | blocked(tool_name)            │
│     current: State                                       │
│                                                          │
│   __aenter__ (cold-start):                               │
│     1. resolve pane_id via `tmux display-message`        │
│     2. capture pane → derive initial kind from pane      │
│     3. derive_state → emit initial State                 │
│     4. spawn tap consumer task + poll loop task          │
│                                                          │
│   tap consumer (background task):                        │
│     for each event where tmux.session_name == session:   │
│       update kind from event_type                        │
│       update pane_id from event.tmux.pane_id (latest)    │
│                                                          │
│   poll loop (background task, every poll_interval):      │
│     capture pane (using current pane_id)                 │
│     state = derive_state(kind, pane_text)                │
│     if state != current: current = state; yield          │
└──────────────────────────────────────────────────────────┘
```

Two concurrent asyncio tasks share two internal variables:
`pane_id` and `kind`. The poll loop is the single source of yields:
each tick it captures the pane (using whatever `pane_id` is
currently recorded) and derives a State by combining the captured
text with the current `kind`. Yields are deduped against
`current`.

`pane_id` is initialised by tmux discovery at startup so the
monitor produces a useful state on tick 0. Subsequent tap events
overwrite `pane_id` with whatever they carry (`event.tmux.pane_id`)
so the monitor follows the pane across any restarts inside the
tmux session.

### kind and pane_id derivation (tap-event-driven)

`kind` and `pane_id` are internal-only — never exposed to
consumers. Every matching tap event has two side effects: it
updates `kind` per the table below, and it overwrites `pane_id`
with `event.tmux.pane_id` (latest wins, regardless of event_type).
The pane_id update is unconditional because every tap event
carries `tmux.pane_id` and the convention guarantees it is the
right pane.

| event_type | new kind |
|---|---|
| Initial (cold-start, derived from pane) | `idle` / `working` / `blocked(tool_name="unknown")` per pane heuristic |
| `stop` | `idle` |
| `user_prompt_submit` | `working` |
| `pre_tool_use` | `working` (no-op if already) |
| `post_tool_use` | `working` (no-op if already, unless matches pending PR — see below) |
| `permission_request` | `blocked(tool_name)`; remember `pending_tool=tool_name` |
| `post_tool_use` matching `pending_tool` | `working`; clear `pending_tool` |
| `notification` | no change |
| `session_end` (this session) | terminate iterator after emitting `Dead("session_end", ...)` |
| anything unrecognised | no change; log warning to stderr |

The `permission_request` → `post_tool_use` matching uses `tool_name`
because tap's `PostToolUse` event does not carry `request_id`. This
is correct for the common case (one in-flight permission at a time)
and is the same heuristic used elsewhere in the ecosystem.

### State derivation (kind + pane refinement)

```python
def derive_state(kind, pane_text) -> State:
    spinner = parse_status_line(pane_text)  # cc-state primitive
    match kind:
        case "idle":
            return Idle(text=spinner or "")
        case "working":
            if spinner and "…" in spinner:
                return Working(text=spinner)
            else:
                # Esc was pressed mid-turn: events say working,
                # screen says nothing is spinning. Trust screen.
                return Idle(text=spinner or "")
        case ("blocked", tool_name):
            content = extract_between_rules(pane_text)
            return Blocked(tool_name=tool_name, content=content)
```

**Why screen wins over events:** the user's keyboard interactions
(Esc) bypass Claude Code's hook contract. The pane is the only
ground truth for "is Claude actually still working?". The library's
contract is: when events and screen disagree about whether something
is spinning, the screen is canonical.

### Cold-start

`SessionMonitor.__aenter__` produces the first usable state without
waiting for any tap event:

1. Resolve the tmux pane via:

   ```bash
   tmux display-message -t <tmux_session>:0 -p '#{pane_id}'
   ```

   If the tmux session does not exist or has no window 0,
   `__aenter__` raises a typed exception (see Error handling). The
   consumer is expected to handle this — there is no monitor to
   produce a `Dead` state because we never opened one.

2. Run the pane through the same `derive_state` logic, with a
   pane-only kind heuristic since no events have been seen:

   - pane has input chrome + spinner row with `…` → `kind = working`
   - pane has input chrome (no spinner with `…`) → `kind = idle`
   - pane has no input chrome → `kind =
     blocked(tool_name="unknown")`. We do not pattern-match the
     dialog body on cold-start; the next `permission_request` event
     refines `tool_name`.
   - pane is empty / unreadable → `kind = idle`

3. Emit the initial State to the iterator and store it as
   `current`. Start the tap consumer task. The first event for this
   tmux session that arrives later may refine kind and/or update
   spinner text.

## Pane primitives

Copied verbatim from `claude-code-state` and maintained here, with
attribution comment in the source. Three functions only:

- `has_input_chrome(pane_lines: list[str]) -> bool`
- `parse_status_line(pane_text: str) -> str | None` — returns the
  raw spinner row text (e.g. `"✻ Thinking… 16s · ↑ 827 tokens"`),
  or None if no spinner row is present
- `capture_pane(pane_id: str) -> str` — shells out to `tmux
  capture-pane -p -t <pane_id>`, no `-e` (ANSI escapes excluded)

For tmux session lookup and `Blocked` content extraction we add
two primitives (not in cc-state):

- `resolve_pane_id(tmux_session: str) -> str` — shells out to
  `tmux display-message -t <tmux_session>:0 -p '#{pane_id}'`.
  Raises a typed error if the tmux session does not exist or has no
  window 0.
- `extract_between_rules(pane_text: str) -> str` — concatenates
  lines that fall between the most recent pair of horizontal-rule
  rows (`────...`). Returns `""` when fewer than two rule rows are
  present.

We do **not** copy cc-state's `extract_interactive_content` or its
6-pattern UI table. Tap's `permission_request` event provides
`tool_name`, which subsumes the role of pattern matching.

## Error handling

| Failure | Behaviour |
|---|---|
| `~/.claude-tap/events.jsonl` does not exist | tap consumer task blocks on `EventStream` (which sleeps until file appears); poll loop continues, deriving State from pane alone |
| `events.jsonl` is rotated / truncated | `EventStream` does not gracefully recover. Documented in README known-issues; full recovery is a v0.2 task |
| `resolve_pane_id` fails at cold-start (tmux session does not exist or has no window 0) | `__aenter__` raises a typed exception; the monitor never starts. Consumer wraps in try/except |
| `tmux capture-pane` fails mid-life (pane closed, tmux not running) | emit `Dead(reason="pane_lost" or "tmux_unavailable", last_state=...)`, terminate iterator |
| `session_end` event for this session | emit `Dead(reason="session_end", last_state=...)`, terminate iterator |
| Consumer cancels the iterator | both internal tasks are cancelled; `EventStream` is closed; capture subprocess (if any) is reaped |
| Unknown event_type | no-op, single warning to stderr (deduped per type per process) |
| Mid-render pane snapshot (incomplete spinner) | `parse_status_line` returns None; `derive_state` falls back to "no spinner detail"; in `working` kind that becomes `Idle(text="")` — same as Esc-downgrade. Acceptable: next tick (1s later) will likely catch the full row |

## Testing strategy

Four layers, each independently runnable.

1. **Pure logic — `kind_from_event(prev_kind, event)`**
   Table-driven. Every (prev_kind, event_type) combination. No IO.

2. **Pure logic — `derive_state(kind, pane_text)`**
   Fixture-driven. ~6 real pane samples committed under
   `tests/fixtures/pane_*.txt` (Working with spinner, Idle with
   "Churned for", Blocked with permission prompt, mid-render etc.).
   No IO.

3. **Dedup & yield contract**
   Feed a synthetic sequence of `derive_state` outputs through the
   monitor's queue logic; assert iterator yields only on change and
   `current` always reflects the most recent.

4. **Integration with mocked IO**
   Mock `EventStream` (yields scripted events) and `capture_pane`
   (returns scripted pane text per call). Drive `SessionMonitor`
   through scenarios:
   - normal turn (UPS → tools → Stop)
   - Esc mid-tool (UPS → pre_tool → no Stop, pane spinner gone)
   - permission flow (UPS → pre_tool → PR → user answers → Stop)
   - pane disappears mid-flight (`capture_pane` raises)
   - SessionEnd

   Test framework: `pytest` + `pytest-asyncio` (matches claude-tap
   and cc-state).

A real Claude Code smoke test (run a session, observe monitor
output) is **not** part of CI; release verification is performed by
the maintainer manually.

## Dependencies

- **Runtime:** `claude-tap` (git URL, pinned by `uv.lock`).
  Strong coupling is by design.
- **Dev:** `pytest`, `pytest-asyncio`, `ruff`. Same set as
  claude-tap.
- **External programs:** `tmux` binary on `$PATH`. Not a Python
  dependency. Library raises a typed error if `tmux` is unavailable
  on first capture.

No dependency on `claude-code-state` itself — we copy the few
primitives we need so cc-state can be deprecated cleanly.

## Out of scope for this spec

- The CLI daemon and `~/.claude-tap-state/events.jsonl` output.
- An `interrupted` correction event (Esc is folded into State
  derivation in v0.1; a separate event is not needed).
- A `drift` correction event for unrecognised dialogs.
- Watching multiple sessions in one process. Consumer composition.
- Persisting state across monitor restarts.
