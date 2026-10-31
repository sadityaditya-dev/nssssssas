"""Deterministic, time-ordered simulation events."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

from ..models import FaultKind


@dataclass(order=True, frozen=True)
class ScheduledEvent:
    time_s: float
    sequence: int
    satellite_id: str = field(compare=False)
    kind: FaultKind = field(compare=False)
    value: str | float | None = field(default=None, compare=False)

    @classmethod
    def create(
        cls,
        time_s: float,
        sequence: int,
        satellite_id: str,
        kind: FaultKind | str,
        value: str | float | None = None,
    ) -> "ScheduledEvent":
        import math

        if not math.isfinite(time_s) or time_s < 0:
            raise ValueError("event time_s must be finite and >= 0")
        if not satellite_id:
            raise ValueError("event satellite_id must be non-empty")
        try:
            fault_kind = kind if isinstance(kind, FaultKind) else FaultKind(kind)
        except ValueError as exc:
            raise ValueError(f"unsupported event kind: {kind!r}") from exc
        if fault_kind is FaultKind.PAYLOAD_FAILURE and (not isinstance(value, str) or not value):
            raise ValueError("PAYLOAD_FAILURE requires value=<capability name>")
        if fault_kind is FaultKind.THRUSTER_DEGRADATION:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
                raise ValueError("THRUSTER_DEGRADATION requires value=<finite scale between 0 and 1>")
        return cls(float(time_s), sequence, satellite_id, fault_kind, value)


class EventQueue:
    """Priority queue: earlier simulation time is processed first; ties are stable."""

    def __init__(self) -> None:
        self._heap: list[ScheduledEvent] = []
        self._next_sequence = 0

    def schedule(
        self,
        time_s: float,
        satellite_id: str,
        kind: FaultKind | str,
        value: str | float | None = None,
    ) -> ScheduledEvent:
        event = ScheduledEvent.create(time_s, self._next_sequence, satellite_id, kind, value)
        self._next_sequence += 1
        heapq.heappush(self._heap, event)
        return event

    def peek_time(self) -> float | None:
        return self._heap[0].time_s if self._heap else None

    def pop_due(self, time_s: float, tolerance: float = 1e-9) -> list[ScheduledEvent]:
        due: list[ScheduledEvent] = []
        while self._heap and self._heap[0].time_s <= time_s + tolerance:
            due.append(heapq.heappop(self._heap))
        return due

    def cancel_where(self, predicate: Callable[[ScheduledEvent], bool]) -> list[ScheduledEvent]:
        """Cancel queued events matching predicate and return them in time order."""
        removed = [event for event in self._heap if predicate(event)]
        if removed:
            removed_ids = {event.sequence for event in removed}
            self._heap = [event for event in self._heap if event.sequence not in removed_ids]
            heapq.heapify(self._heap)
        return sorted(removed)

    def __len__(self) -> int:
        return len(self._heap)
