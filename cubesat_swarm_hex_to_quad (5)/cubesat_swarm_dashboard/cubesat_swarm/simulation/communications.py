"""Deterministic simulated inter-satellite heartbeat network and failure suspicion.

This is a software communication abstraction, not a radio/PHY model. Each
operational satellite emits a heartbeat on a fixed simulation-time cadence.
Receivers independently track sequence numbers and consecutive missed periods.
A timeout creates a *suspicion*, not a definitive declaration of spacecraft failure.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Mapping

from ..models import Satellite


@dataclass(frozen=True)
class HeartbeatRecord:
    time_s: float
    sender_id: str
    receiver_id: str
    sequence: int
    delivered: bool
    reason: str


class HeartbeatNetwork:
    """Simple reproducible broadcast-heartbeat model with per-link packet loss."""

    def __init__(
        self,
        satellite_ids: list[str],
        heartbeat_period_s: float = 1.0,
        missed_threshold: int = 3,
        packet_loss_probability: float = 0.0,
        seed: int = 7,
    ) -> None:
        if not math.isfinite(heartbeat_period_s) or heartbeat_period_s <= 0:
            raise ValueError("heartbeat_period_s must be finite and > 0")
        if isinstance(missed_threshold, bool) or missed_threshold < 1:
            raise ValueError("missed_threshold must be an integer >= 1")
        if not math.isfinite(packet_loss_probability) or not 0 <= packet_loss_probability < 1:
            raise ValueError("packet_loss_probability must be in [0, 1)")
        if len(satellite_ids) != len(set(satellite_ids)) or not satellite_ids:
            raise ValueError("satellite_ids must be non-empty and unique")
        self.satellite_ids = tuple(satellite_ids)
        # A communication outage leaves the spacecraft operational but prevents it
        # from sending and receiving heartbeat packets. This is distinct from bus failure.
        self.isolated_ids: set[str] = set()
        self.heartbeat_period_s = float(heartbeat_period_s)
        self.missed_threshold = int(missed_threshold)
        self.packet_loss_probability = float(packet_loss_probability)
        self._rng = random.Random(seed)
        self._next_heartbeat_s = 0.0
        self._sequence: dict[str, int] = {sid: 0 for sid in satellite_ids}
        self._last_received_sequence: dict[tuple[str, str], int] = {}
        self._consecutive_misses: dict[tuple[str, str], int] = {
            (receiver, sender): 0
            for receiver in satellite_ids for sender in satellite_ids if receiver != sender
        }
        self._suspicions: dict[tuple[str, str], str] = {}
        self.records: list[HeartbeatRecord] = []
        self.events: list[dict[str, object]] = []
        self.sent_count = 0
        self.delivered_count = 0
        self.dropped_count = 0

    @property
    def suspicions(self) -> dict[str, set[str]]:
        """Map receiver ID to the IDs it currently suspects are unreachable."""
        result = {sid: set() for sid in self.satellite_ids}
        for (receiver, target), _reason in self._suspicions.items():
            result[receiver].add(target)
        return result

    def set_isolated(self, satellite_id: str, isolated: bool = True) -> None:
        if satellite_id not in self.satellite_ids:
            raise ValueError(f"unknown satellite ID: {satellite_id}")
        if isolated:
            self.isolated_ids.add(satellite_id)
        else:
            self.isolated_ids.discard(satellite_id)

    @property
    def next_heartbeat_time_s(self) -> float:
        return self._next_heartbeat_s

    def tick(self, time_s: float, satellites: Mapping[str, Satellite]) -> None:
        """Process every heartbeat period due at ``time_s`` (including exact times)."""
        while self._next_heartbeat_s <= time_s + 1e-9:
            t = self._next_heartbeat_s
            self._transmit_period(t, satellites)
            self._next_heartbeat_s += self.heartbeat_period_s

    def _transmit_period(self, time_s: float, satellites: Mapping[str, Satellite]) -> None:
        senders = [sid for sid in self.satellite_ids if satellites[sid].is_operational and sid not in self.isolated_ids]
        # Each operational sender emits exactly one sequence number per period.
        for sender in senders:
            self._sequence[sender] += 1
            sequence = self._sequence[sender]
            for receiver in self.satellite_ids:
                if receiver == sender or not satellites[receiver].is_operational or receiver in self.isolated_ids:
                    continue
                key = (receiver, sender)
                self.sent_count += 1
                if self._rng.random() < self.packet_loss_probability:
                    self.dropped_count += 1
                    misses = self._consecutive_misses[key] + 1
                    self._consecutive_misses[key] = misses
                    self.records.append(HeartbeatRecord(time_s, sender, receiver, sequence, False, "simulated_packet_loss"))
                    if misses >= self.missed_threshold:
                        self._raise_suspicion(time_s, receiver, sender, f"{misses} consecutive heartbeat periods missed")
                    continue
                self.delivered_count += 1
                previous = self._last_received_sequence.get(key, 0)
                if sequence <= previous:
                    self.records.append(HeartbeatRecord(time_s, sender, receiver, sequence, False, "stale_sequence"))
                    continue
                self._last_received_sequence[key] = sequence
                self._consecutive_misses[key] = 0
                self.records.append(HeartbeatRecord(time_s, sender, receiver, sequence, True, "received"))
                if key in self._suspicions:
                    del self._suspicions[key]
                    self.events.append({"time_s": time_s, "type": "suspicion_cleared", "observer": receiver,
                                        "target": sender, "reason": "new heartbeat received"})

        # An operational receiver expects one packet from every other member each period.
        # If a target has failed, it emits nothing; count the missing heartbeat per receiver.
        for receiver in self.satellite_ids:
            if not satellites[receiver].is_operational or receiver in self.isolated_ids:
                continue
            for target in self.satellite_ids:
                if target == receiver or target in senders:
                    continue
                key = (receiver, target)
                misses = self._consecutive_misses[key] + 1
                self._consecutive_misses[key] = misses
                self.records.append(HeartbeatRecord(time_s, target, receiver, self._sequence[target], False, "sender_not_operational"))
                if misses >= self.missed_threshold:
                    self._raise_suspicion(time_s, receiver, target, f"{misses} consecutive heartbeat periods missed")

    def _raise_suspicion(self, time_s: float, observer: str, target: str, reason: str) -> None:
        key = (observer, target)
        if key not in self._suspicions:
            self._suspicions[key] = reason
            self.events.append({"time_s": time_s, "type": "suspected_unreachable", "observer": observer,
                                "target": target, "reason": reason})
