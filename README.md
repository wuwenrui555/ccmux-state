# claude-tap-state

[![CI](https://github.com/wuwenrui555/claude-tap-state/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/wuwenrui555/claude-tap-state/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://pre-commit.com)

Pane-based gap-filler for [claude-tap](https://github.com/wuwenrui555/claude-tap):
detect Claude Code session events that hooks do not surface.

## Status

v0.1 alpha. Skeleton only — actual detection logic still being built.

## Why this exists

`claude-tap` faithfully forwards every Claude Code hook event into a
structured stream, but the hook contract has gaps. Some events that
matter to a state-tracking consumer never reach the hook layer at all:

| Event | Why hooks miss it |
|---|---|
| User-initiated `Esc` interrupt | Claude Code's `Stop` hook fires only on natural turn completion. An external cancel skips it. |
| Process death (crash / kill -9) | `SessionEnd` only fires on graceful exit. |
| Unknown blocking dialog | If Claude Code adds a new dialog type that goes through neither `PermissionRequest` nor a recognised path, no hook fires. |

`claude-tap-state` subscribes to the `claude-tap` event stream **and**
captures the tmux pane, then emits synthetic correction events when the
two views disagree:

- `interrupted` — pane went idle but no `Stop` event arrived
- `dead` — pane lost its input chrome but no `SessionEnd` event arrived
- `drift` — pane is blocked by a dialog but no `PermissionRequest`
  event arrived (signal to investigate a new Claude Code UI)

This package has **no opinion** about what consumers do with these
correction events — it only detects and emits them.

## Relationship to other packages

- **[claude-tap](https://github.com/wuwenrui555/claude-tap)** —
  required dependency. Provides the canonical hook event stream
  this package layers on top of.
- **[claude-code-state](https://github.com/wuwenrui555/claude-code-state)** —
  predecessor package with a broader role (full pane-state
  classification). Now superseded; consumers that just need correction
  events should use this package instead.

## Install

```bash
uv tool install git+https://github.com/wuwenrui555/claude-tap-state.git
```

## License

Apache 2.0.
