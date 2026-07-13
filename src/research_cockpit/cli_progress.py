from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import json
import sys
import time
from typing import Any, Callable, Iterator, TypeVar


PROGRESS_PREFIX = "[research-cockpit-progress] "


@dataclass(frozen=True)
class ProgressState:
    enabled: bool
    command: str
    started_at: float


_STATE: ContextVar[ProgressState | None] = ContextVar("research_cockpit_progress", default=None)
T = TypeVar("T")


def progress_enabled() -> bool:
    state = _STATE.get()
    return bool(state and state.enabled)


def emit_progress_event(
    phase: str,
    *,
    event: str,
    duration_ms: float | None = None,
    status: str | None = None,
) -> None:
    state = _STATE.get()
    if state is None or not state.enabled:
        return
    payload: dict[str, Any] = {
        "event": event,
        "command": state.command,
        "phase": phase,
        "elapsed_ms": round((time.perf_counter() - state.started_at) * 1000, 3),
    }
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 3)
    if status:
        payload["status"] = status
    print(
        PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


@contextmanager
def progress_session(command: str, *, explicit: bool = False) -> Iterator[None]:
    enabled = explicit or bool(getattr(sys.stderr, "isatty", lambda: False)())
    state = ProgressState(enabled=enabled, command=command, started_at=time.perf_counter())
    token = _STATE.set(state)
    emit_progress_event("command", event="phase_start")
    try:
        yield
    except BaseException:
        emit_progress_event("command", event="phase_end", status="failed")
        raise
    else:
        emit_progress_event("command", event="phase_end", status="completed")
    finally:
        _STATE.reset(token)


@contextmanager
def progress_phase(name: str) -> Iterator[None]:
    started_at = time.perf_counter()
    emit_progress_event(name, event="phase_start")
    try:
        yield
    except BaseException:
        emit_progress_event(
            name,
            event="phase_end",
            duration_ms=(time.perf_counter() - started_at) * 1000,
            status="failed",
        )
        raise
    else:
        emit_progress_event(
            name,
            event="phase_end",
            duration_ms=(time.perf_counter() - started_at) * 1000,
            status="completed",
        )


def progress_traced(name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorate(function: Callable[..., T]) -> Callable[..., T]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> T:
            with progress_phase(name):
                return function(*args, **kwargs)

        return wrapped

    return decorate
