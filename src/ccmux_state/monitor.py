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
from ccmux_state.state import Dead, Idle, State

if TYPE_CHECKING:
    from pathlib import Path


_DEFAULT_POLL_INTERVAL = 1.0


def _make_event_stream(tap_events_path: Path | None):
    """Factory for the tap event stream. Tests patch this."""
    from claude_tap import EventStream

    return EventStream(path=tap_events_path, from_start=False)


def _spawn_background_tasks(monitor: SessionMonitor) -> None:
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
        tap_events_path: Path | None = None,
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

    async def __aenter__(self) -> SessionMonitor:
        # Cold-start: tmux discovery + pane capture + initial state.
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
            # Defensive: a crash in the tap consumer must not silently
            # freeze the monitor. Surface as Dead so the iterator
            # terminates cleanly.
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
        last = self._current if self._current is not None else Idle()
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
