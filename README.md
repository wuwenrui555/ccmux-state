# ccmux-state

[![CI](https://github.com/wuwenrui555/ccmux-state/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/wuwenrui555/ccmux-state/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://pre-commit.com)

Tmux-pane state monitor for Claude Code sessions running under the
ccmux convention. Consumes [claude-tap](https://github.com/wuwenrui555/claude-tap)
events and refines them with narrowly-scoped pane reads, exposing
the current state of one Claude Code session as `Idle` / `Working` /
`Blocked` / `Dead` with optional pane-derived detail text.

## Status

v0.1 alpha. Skeleton only — implementation still being built. Design
spec at
[`docs/superpowers/specs/2026-05-08-ccmux-state-design.md`](docs/superpowers/specs/2026-05-08-ccmux-state-design.md).

## Convention

This package assumes **one tmux session = one window = one Claude
Code instance**. The user-facing identifier is the tmux session
name. Violating the convention (multiple windows or panes per
session, or mixing Claude with other commands) is undefined
behaviour. This is why the package is named `ccmux-state` rather
than something like `claude-tap-state`: the convention is a ccmux
contract, not a generic claude-tap contract.

## Quick sketch

```python
from ccmux_state import SessionMonitor, Idle, Working, Blocked, Dead

async with SessionMonitor(tmux_session="ccmux-projA") as monitor:
    state = monitor.current               # snapshot
    async for state in monitor:           # change-driven
        match state:
            case Idle(text):
                ...
            case Working(text):            # text is the spinner row
                ...
            case Blocked(tool_name, content):
                ...
            case Dead(reason, last_state):
                break
```

Cold-start uses `tmux display-message` to resolve the pane and
captures it once to derive an initial state without waiting for any
tap event. Subsequent tap events update the internal `kind` and
`pane_id`; a periodic poll loop refreshes the pane and emits state
changes (deduped by full structural equality).

## Relationship to other packages

- **[claude-tap](https://github.com/wuwenrui555/claude-tap)** —
  required runtime dependency. Provides the canonical hook event
  stream this package layers on top of.
- **[claude-code-state](https://github.com/wuwenrui555/claude-code-state)** —
  predecessor package that classified state by parsing the pane
  alone. Now superseded by `ccmux-state`. Three pane primitives
  (`has_input_chrome`, `parse_status_line`, `capture_pane`) are
  copied verbatim into `ccmux-state` with attribution.

## Install

```bash
uv tool install git+https://github.com/wuwenrui555/ccmux-state.git
```

## License

Apache 2.0.
