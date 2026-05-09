from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ccmux_state.errors import PaneCaptureError, TmuxResolutionError
from ccmux_state.monitor import SessionMonitor
from ccmux_state.state import Blocked, Dead, Idle, Working

FIXTURE_DIR = Path(__file__).parent / "fixtures"


class FakeEventStream:
    """Scriptable stand-in for claude_tap.EventStream.

    Yields the supplied events one at a time, then holds open until
    `close()` is called so the monitor's tap consumer task does not
    exit prematurely.
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
            await asyncio.sleep(0)
        while not self._closed:
            await asyncio.sleep(0.01)


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


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


# ---------- cold-start ----------


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


# ---------- iterator basics ----------


@pytest.mark.asyncio
async def test_iterator_yields_initial_state(monkeypatch):
    pane_seq = [_read("pane_idle_empty.txt")]
    fake = FakeEventStream(events=[])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr("ccmux_state.monitor.capture_pane", lambda _: pane_seq[-1])
    monkeypatch.setattr("ccmux_state.monitor._make_event_stream", lambda *a, **k: fake)

    async with SessionMonitor(tmux_session="ccmux-test", poll_interval=0.01) as m:
        first = await asyncio.wait_for(m.__aiter__().__anext__(), timeout=1.0)
        assert first == Idle(text="")


@pytest.mark.asyncio
async def test_iterator_dedupes_unchanged_state(monkeypatch):
    pane_seq = [_read("pane_idle_empty.txt")]
    fake = FakeEventStream(events=[])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr("ccmux_state.monitor.capture_pane", lambda _: pane_seq[-1])
    monkeypatch.setattr("ccmux_state.monitor._make_event_stream", lambda *a, **k: fake)

    async with SessionMonitor(tmux_session="ccmux-test", poll_interval=0.01) as m:
        it = m.__aiter__()
        first = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert first == Idle(text="")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(it.__anext__(), timeout=0.1)


# ---------- event-driven transitions ----------


@pytest.mark.asyncio
async def test_user_prompt_submit_transitions_to_working(monkeypatch):
    panes = {"current": _read("pane_idle_empty.txt")}
    fake = FakeEventStream(events=[_ev("user_prompt_submit", prompt="hi")])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr("ccmux_state.monitor.capture_pane", lambda _: panes["current"])
    monkeypatch.setattr("ccmux_state.monitor._make_event_stream", lambda *a, **k: fake)

    async with SessionMonitor(tmux_session="ccmux-test", poll_interval=0.01) as m:
        it = m.__aiter__()
        first = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert first == Idle(text="")
        # Flip pane to working sample; next change-yield should be Working.
        panes["current"] = _read("pane_working.txt")
        for _ in range(50):
            try:
                second = await asyncio.wait_for(it.__anext__(), timeout=0.1)
            except TimeoutError:
                continue
            if isinstance(second, Working):
                break
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
    monkeypatch.setattr("ccmux_state.monitor.capture_pane", lambda _: panes["current"])
    monkeypatch.setattr("ccmux_state.monitor._make_event_stream", lambda *a, **k: fake)

    async with SessionMonitor(tmux_session="ccmux-test", poll_interval=0.01) as m:
        it = m.__aiter__()
        await asyncio.wait_for(it.__anext__(), timeout=1.0)  # cold-start Idle
        panes["current"] = _read("pane_blocked_permission.txt")
        for _ in range(50):
            try:
                state = await asyncio.wait_for(it.__anext__(), timeout=0.1)
            except TimeoutError:
                continue
            if isinstance(state, Blocked):
                break
        assert isinstance(state, Blocked)
        assert state.tool_name == "AskUserQuestion"
        assert "Do you want to proceed?" in state.content


# ---------- lifecycle / Dead ----------


@pytest.mark.asyncio
async def test_pane_lost_emits_dead(monkeypatch):
    pane_state = {"alive": True}

    def _capture(_):
        if pane_state["alive"]:
            return _read("pane_idle_empty.txt")
        raise PaneCaptureError("pane gone")

    fake = FakeEventStream(events=[])

    monkeypatch.setattr("ccmux_state.monitor.resolve_pane_id", lambda s: "%1")
    monkeypatch.setattr("ccmux_state.monitor.capture_pane", _capture)
    monkeypatch.setattr("ccmux_state.monitor._make_event_stream", lambda *a, **k: fake)

    async with SessionMonitor(tmux_session="ccmux-test", poll_interval=0.01) as m:
        it = m.__aiter__()
        first = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert isinstance(first, Idle)

        pane_state["alive"] = False
        for _ in range(50):
            try:
                state = await asyncio.wait_for(it.__anext__(), timeout=0.1)
            except TimeoutError:
                continue
            if isinstance(state, Dead):
                break
        assert isinstance(state, Dead)
        assert state.reason == "pane_lost"
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
    monkeypatch.setattr("ccmux_state.monitor._make_event_stream", lambda *a, **k: fake)

    async with SessionMonitor(tmux_session="ccmux-test", poll_interval=0.01) as m:
        it = m.__aiter__()
        await asyncio.wait_for(it.__anext__(), timeout=1.0)
        for _ in range(50):
            try:
                state = await asyncio.wait_for(it.__anext__(), timeout=0.1)
            except TimeoutError:
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
    monkeypatch.setattr("ccmux_state.monitor._make_event_stream", lambda *a, **k: fake)

    monitor = SessionMonitor(tmux_session="ccmux-test", poll_interval=0.01)
    async with monitor:
        pass
    for task in monitor._tasks:
        assert task.done()
