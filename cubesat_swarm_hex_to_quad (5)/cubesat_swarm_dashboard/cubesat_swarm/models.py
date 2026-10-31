"""Validated spacecraft and telemetry models (SI units)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

import numpy as np

STATE_SIZE = 4


class HealthState(str, Enum):
    NOMINAL = "nominal"
    DEGRADED = "degraded"
    FAILED = "failed"


class FaultKind(str, Enum):
    BUS_FAILURE = "bus_failure"
    PAYLOAD_FAILURE = "payload_failure"
    THRUSTER_DEGRADATION = "thruster_degradation"


@dataclass
class Satellite:
    """Physical spacecraft model.

    State vector ordering is [x_m, y_m, vx_mps, vy_mps] in the planar
    local Hill/LVLH frame. ``capabilities`` are hardware capabilities;
    ``current_role`` is a software-assigned mission responsibility.
    """

    satellite_id: str
    state: np.ndarray
    capabilities: set[str] = field(default_factory=set)
    current_role: str | None = None
    remaining_dv_mps: float = 10.0
    max_acceleration_mps2: float = 1.0e-3
    health_state: HealthState = HealthState.NOMINAL
    failed_capabilities: set[str] = field(default_factory=set)
    thruster_scale: float = 1.0
    failure_reason: str | None = None
    energy_percent: float = 100.0

    def __post_init__(self) -> None:
        self.state = np.asarray(self.state, dtype=float).copy()
        if not self.satellite_id or not self.satellite_id.strip():
            raise ValueError("satellite_id must be a non-empty string")
        if self.state.shape != (STATE_SIZE,):
            raise ValueError(f"state must have shape ({STATE_SIZE},), got {self.state.shape}")
        if not np.all(np.isfinite(self.state)):
            raise ValueError("state must contain only finite values")
        if not np.isfinite(self.remaining_dv_mps) or self.remaining_dv_mps < 0:
            raise ValueError("remaining_dv_mps must be finite and >= 0")
        if not np.isfinite(self.max_acceleration_mps2) or self.max_acceleration_mps2 < 0:
            raise ValueError("max_acceleration_mps2 must be finite and >= 0")
        if not 0.0 <= self.thruster_scale <= 1.0:
            raise ValueError("thruster_scale must be between 0 and 1")
        if not np.isfinite(self.energy_percent) or not 0.0 <= self.energy_percent <= 100.0:
            raise ValueError("energy_percent must be finite and between 0 and 100")
        self.capabilities = set(self.capabilities)
        self.failed_capabilities = set(self.failed_capabilities)

    @property
    def is_operational(self) -> bool:
        return self.health_state is not HealthState.FAILED

    @property
    def effective_capabilities(self) -> frozenset[str]:
        return frozenset(self.capabilities - self.failed_capabilities)

    @property
    def effective_max_acceleration_mps2(self) -> float:
        if not self.is_operational or self.remaining_dv_mps <= 0.0:
            return 0.0
        return self.max_acceleration_mps2 * self.thruster_scale

    def apply_fault(self, kind: FaultKind, value: str | float | None = None) -> str:
        """Apply one fault and return a human-readable event description."""
        if kind is FaultKind.BUS_FAILURE:
            if not self.is_operational:
                return f"{self.satellite_id} was already failed"
            self.health_state = HealthState.FAILED
            self.failure_reason = "bus failure"
            return f"{self.satellite_id}: bus failure; spacecraft removed from operational membership"

        if not self.is_operational:
            return f"{self.satellite_id}: fault ignored because spacecraft is already failed"

        if kind is FaultKind.PAYLOAD_FAILURE:
            if not isinstance(value, str) or not value:
                raise ValueError("PAYLOAD_FAILURE requires value=<capability name>")
            if value not in self.capabilities:
                return f"{self.satellite_id}: payload capability {value!r} is not installed"
            self.failed_capabilities.add(value)
            self.health_state = HealthState.DEGRADED
            return f"{self.satellite_id}: payload capability {value!r} failed"

        if kind is FaultKind.THRUSTER_DEGRADATION:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("THRUSTER_DEGRADATION requires value=<scale between 0 and 1>")
            scale = float(value)
            if not np.isfinite(scale) or not 0.0 <= scale <= 1.0:
                raise ValueError("thruster scale must be finite and between 0 and 1")
            self.thruster_scale = min(self.thruster_scale, scale)
            self.health_state = HealthState.DEGRADED
            return f"{self.satellite_id}: thruster authority scaled to {self.thruster_scale:.3f}"

        raise ValueError(f"Unsupported fault kind: {kind}")

    def clamp_control(self, command: Iterable[float]) -> np.ndarray:
        """Return a control vector respecting health, thrust and delta-v limits."""
        u = np.asarray(tuple(command), dtype=float)
        if u.shape != (2,) or not np.all(np.isfinite(u)):
            raise ValueError("control command must be a finite two-element vector")
        if not self.is_operational or self.remaining_dv_mps <= 0.0:
            return np.zeros(2, dtype=float)
        limit = self.effective_max_acceleration_mps2
        norm = float(np.linalg.norm(u))
        if norm > limit and norm > 0.0:
            u = u * (limit / norm)
        return u


@dataclass(frozen=True)
class StateSnapshot:
    """Read-only telemetry snapshot emitted by the simulation engine."""

    time_s: float
    states_m: dict[str, tuple[float, float, float, float]]
    health: dict[str, str]
    roles: dict[str, str | None]
    remaining_dv_mps: dict[str, float]
    active_count: int

