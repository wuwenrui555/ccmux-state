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

- A consumer can ask "what state is session X in?" and get a precise,
  pane-refined answer.
- A consumer can subscribe to state-change notifications via async
  iteration without polling.
- The library exposes both a snapshot accessor (`current`) and an
  async iterator (`async for state in monitor`); the iterator yields
  only on actual state change.
- Single-session API. Multi-session support is the consumer's job
  (instantiate one `SessionMonitor` per session).
- Detect Esc-interrupt as part of state derivation: if the kind is
  Working but the pane spinner has no `…`, downgrade to `Idle`.

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
    session_id="69a3c45e-4b66-4863-8398-2ac4d50aaebf",
    pane_id="%80",
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
fields). Spinner-text ticking from `… 16s` to `… 17s` counts as a
change and produces a new yield. Consumers that want kind-only
notifications filter on their side.

## Architecture

```text
┌──────────────────────────────────────────────────────────┐
│  SessionMonitor(session_id, pane_id)                     │
│                                                          │
│   internal state:                                        │
│     kind: idle | working | blocked(tool_name)            │
│     current: State                                       │
│                                                          │
│   ┌──────────────┐         ┌──────────────────┐          │
│   │ tap consumer │  feeds  │  poll loop       │          │
│   │  (filtered   │ ──────► │   (every poll_   │          │
│   │   by sess_id)│         │    interval s)   │          │
│   └──────────────┘         └────────┬─────────┘          │
│         (updates kind)              │                    │
│                                     │ derives State      │
│                                     ▼                    │
│                            ┌────────────────┐            │
│                            │ async queue +  │            │
│                            │ current cache  │            │
│                            └────────────────┘            │
│                                     │                    │
│                                     ▼                    │
│                            consumer iterator             │
└──────────────────────────────────────────────────────────┘
```

Two concurrent asyncio tasks share an internal `kind` variable. The
poll loop is the single source of yields: it reads pane each tick,
combines `kind` with pane text via `derive_state(kind, pane_text)`,
deduplicates, and emits.

### kind derivation (tap-event-driven)

`kind` is internal-only — never exposed to consumers. It is updated
synchronously when each tap event arrives:

| event_type | new kind |
|---|---|
| Initial (before any event) | `idle` |
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

`SessionMonitor.__aenter__` does a single immediate pane capture and
sets `kind` from screen alone before any tap events arrive:

- pane has input chrome + spinner with `…` → `kind = working`
- pane has input chrome (no spinner with `…`) → `kind = idle`
- pane has no input chrome but matches a known dialog shape → `kind
  = blocked(tool_name=?)` (we don't know the tool name from screen
  alone — emit `Blocked(tool_name="unknown", ...)` until a tap event
  refines it)
- pane is empty / unreadable → `kind = idle`

This means the monitor produces a useful `current` state on tick 0
without needing event history. Subsequent tap events refine it.

## Pane primitives

Copied verbatim from `claude-code-state` and maintained here, with
attribution comment in the source. Three functions only:

- `has_input_chrome(pane_lines: list[str]) -> bool`
- `parse_status_line(pane_text: str) -> str | None` — returns the
  raw spinner row text (e.g. `"✻ Thinking… 16s · ↑ 827 tokens"`),
  or None if no spinner row is present
- `capture_pane(pane_id: str) -> str` — shells out to `tmux
  capture-pane -p -t <pane_id>`, no `-e` (ANSI escapes excluded)

For `Blocked` content extraction we add a new primitive (not in
cc-state):

- `extract_between_rules(pane_text: str) -> str` — concatenates
  lines that fall between the most recent pair of horizontal-rule
  rows (`────...`)

We do **not** copy cc-state's `extract_interactive_content` or its
6-pattern UI table. Tap's `permission_request` event provides
`tool_name`, which subsumes the role of pattern matching.

## Error handling

| Failure | Behaviour |
|---|---|
| `~/.claude-tap/events.jsonl` does not exist | tap consumer task blocks on `EventStream` (which sleeps until file appears); poll loop continues, deriving State from pane alone |
| `events.jsonl` is rotated / truncated | `EventStream` does not gracefully recover. Documented in README known-issues; full recovery is a v0.2 task |
| `tmux capture-pane` fails (pane closed, tmux not running) | emit `Dead(reason="pane_lost" or "tmux_unavailable", last_state=...)`, terminate iterator |
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
output) is **not** part of CI. Documented in `docs/manual-smoke.md`
for developers verifying releases.

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
