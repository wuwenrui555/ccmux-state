# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial scaffold: package skeleton, pre-commit (ruff + markdownlint),
  GitHub Actions CI matrix on 3.11 / 3.12 / 3.13, README with status
  badges. No detection logic yet.
- v0.1 design spec under `docs/superpowers/specs/`: tmux-session-keyed
  `SessionMonitor` that consumes claude-tap events, refines them with
  narrowly-scoped pane reads, and exposes Idle / Working / Blocked /
  Dead state.

### Changed

- Package renamed from `claude-tap-state` to `ccmux-state` before
  first release. The "1 tmux session = 1 window = 1 Claude" assumption
  is a ccmux convention, not a generic claude-tap one, so the name
  belongs in the ccmux family.
