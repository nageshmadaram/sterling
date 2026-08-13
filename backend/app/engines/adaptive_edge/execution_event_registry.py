from __future__ import annotations

from dataclasses import dataclass

from .execution_adapter import CanonicalExecutionEvent


@dataclass(frozen=True)
class ExecutionEventRecord:
    event: CanonicalExecutionEvent


class ExecutionEventRegistry:
    """Idempotent canonical execution-event registry."""

    def __init__(self) -> None:
        self._events: dict[str, ExecutionEventRecord] = {}

    def record(self, event: CanonicalExecutionEvent) -> bool:
        event.validate()
        existing = self._events.get(event.execution_event_id)
        if existing is None:
            self._events[event.execution_event_id] = ExecutionEventRecord(event)
            return True
        if existing.event != event:
            raise ValueError("execution event id reused with different event")
        return False
