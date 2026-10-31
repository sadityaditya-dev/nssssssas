"""Headless, deterministic simulation engine for the physical-state foundation."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

import numpy as np

from ..dynamics.hcw import rk4_step
from ..models import FaultKind, HealthState, Satellite, StateSnapshot
from .events import EventQueue, ScheduledEvent
from .communications import HeartbeatNetwork

ControlLaw = Callable[[Satellite, float], Iterable[float]]


class SimulationEngine:
    """Advance spacecraft physics, schedule faults and publish telemetry snapshots.

    The nominal step is fixed. If an event lies inside a step, the integrator splits
    that step at the exact event time, applies the event, then integrates the remainder.
    This preserves exact event timing without silently skipping a configured fault.

    Communication is a deterministic heartbeat abstraction. A missed-heartbeat timeout raises a suspicion only; it does not itself mutate spacecraft health or trigger recovery.
    """

    def __init__(
        self,
        satellites: Iterable[Satellite],
        mean_motion_rad_s: float,
        dt_s: float = 0.1,
        control_law: ControlLaw | None = None,
        heartbeat_period_s: float = 1.0,
        missed_heartbeat_threshold: int = 3,
        packet_loss_probability: float = 0.0,
        communication_seed: int = 7,
    ) -> None:
        if not math.isfinite(mean_motion_rad_s) or mean_motion_rad_s < 0:
            raise ValueError("mean_motion_rad_s must be finite and >= 0")
        if not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError("dt_s must be finite and > 0")
        sat_list = list(satellites)
        if not sat_list:
            raise ValueError("at least one satellite is required")
        ids = [sat.satellite_id for sat in sat_list]
        if len(ids) != len(set(ids)):
            raise ValueError("satellite IDs must be unique")

        self.satellites: dict[str, Satellite] = {sat.satellite_id: sat for sat in sat_list}
        self.mean_motion_rad_s = float(mean_motion_rad_s)
        self.dt_s = float(dt_s)
        self.control_law = control_law or (lambda _satellite, _time_s: (0.0, 0.0))
        self.events = EventQueue()
        self.time_s = 0.0
        self.event_log: list[dict[str, object]] = []
        self.history: list[StateSnapshot] = []
        self.network = HeartbeatNetwork(
            list(self.satellites), heartbeat_period_s=heartbeat_period_s,
            missed_threshold=missed_heartbeat_threshold,
            packet_loss_probability=packet_loss_probability, seed=communication_seed,
        )
        self.last_control_mps2: dict[str, tuple[float, float]] = {sid: (0.0, 0.0) for sid in self.satellites}
        self.network.tick(self.time_s, self.satellites)
        self._snapshot()
        self._process_due_events()

    @property
    def active_ids(self) -> list[str]:
        return [sid for sid, sat in self.satellites.items() if sat.is_operational]

    def schedule_fault(
        self,
        time_s: float,
        satellite_id: str,
        kind: FaultKind | str,
        value: str | float | None = None,
    ) -> ScheduledEvent:
        if satellite_id not in self.satellites:
            raise ValueError(f"unknown satellite ID: {satellite_id}")
        if time_s < self.time_s - 1e-9:
            raise ValueError("cannot schedule a fault in the simulation past")
        return self.events.schedule(time_s, satellite_id, kind, value)

    def _process_due_events(self) -> None:
        for event in self.events.pop_due(self.time_s):
            sat = self.satellites[event.satellite_id]
            description = sat.apply_fault(event.kind, event.value)
            self.event_log.append(
                {
                    "time_s": self.time_s,
                    "requested_time_s": event.time_s,
                    "satellite_id": event.satellite_id,
                    "kind": event.kind.value,
                    "description": description,
                }
            )

    def _control_for(self, sat: Satellite) -> np.ndarray:
        if not sat.is_operational:
            return np.zeros(2, dtype=float)
        command = np.asarray(tuple(self.control_law(sat, self.time_s)), dtype=float)
        if command.shape != (2,) or not np.all(np.isfinite(command)):
            raise ValueError(f"control law returned invalid command for {sat.satellite_id}: {command}")
        return sat.clamp_control(command)

    def _integrate_all(self, interval_s: float) -> None:
        if interval_s <= 0:
            return
        controls: dict[str, np.ndarray] = {}
        for sid, sat in self.satellites.items():
            # Bus-failed satellites coast under unforced HCW dynamics; no control is applied.
            controls[sid] = self._control_for(sat) if sat.is_operational else np.zeros(2, dtype=float)

        for sid, sat in self.satellites.items():
            u = controls[sid]
            self.last_control_mps2[sid] = (float(u[0]), float(u[1]))
            sat.state = rk4_step(sat.state, u, self.mean_motion_rad_s, interval_s)
            if sat.is_operational:
                dv_used = float(np.linalg.norm(u)) * interval_s
                # Illustrative normalized energy model: small idle draw plus a
                # control-effort term. This is a dashboard proxy, not a battery model.
                relative_effort = (float(np.linalg.norm(u)) / max(sat.max_acceleration_mps2, 1e-12)) if sat.max_acceleration_mps2 > 0 else 0.0
                sat.energy_percent = max(0.0, sat.energy_percent - interval_s * (0.002 + 0.018 * min(1.0, relative_effort)))
                sat.remaining_dv_mps = max(0.0, sat.remaining_dv_mps - dv_used)
                if sat.remaining_dv_mps <= 0.0 and sat.health_state is HealthState.NOMINAL:
                    sat.health_state = HealthState.DEGRADED

    def step(self, duration_s: float | None = None) -> StateSnapshot:
        """Advance the simulation by one nominal step or a requested positive duration."""
        total_duration = self.dt_s if duration_s is None else float(duration_s)
        if not math.isfinite(total_duration) or total_duration <= 0:
            raise ValueError("duration_s must be finite and > 0")

        target_time = self.time_s + total_duration
        self._process_due_events()
        while self.time_s < target_time - 1e-10:
            next_event_time = self.events.peek_time()
            boundary = target_time
            if next_event_time is not None and self.time_s + 1e-10 < next_event_time < boundary - 1e-10:
                boundary = next_event_time

            interval_s = boundary - self.time_s
            self._integrate_all(interval_s)
            self.time_s = boundary
            self._process_due_events()
            self.network.tick(self.time_s, self.satellites)

        # Remove floating-point drift around common decimal step times.
        if abs(self.time_s - target_time) < 1e-8:
            self.time_s = target_time
        return self._snapshot()

    def run_until(self, end_time_s: float) -> StateSnapshot:
        """Run until an absolute simulation time, using a shorter final step if needed."""
        if not math.isfinite(end_time_s) or end_time_s < self.time_s:
            raise ValueError("end_time_s must be finite and >= current simulation time")
        while self.time_s < end_time_s - 1e-10:
            self.step(min(self.dt_s, end_time_s - self.time_s))
        return self.history[-1]

    def _snapshot(self) -> StateSnapshot:
        snapshot = StateSnapshot(
            time_s=float(self.time_s),
            states_m={sid: tuple(float(v) for v in sat.state) for sid, sat in self.satellites.items()},
            health={sid: sat.health_state.value for sid, sat in self.satellites.items()},
            roles={sid: sat.current_role for sid, sat in self.satellites.items()},
            remaining_dv_mps={sid: float(sat.remaining_dv_mps) for sid, sat in self.satellites.items()},
            active_count=len(self.active_ids),
        )
        self.history.append(snapshot)
        return snapshot
