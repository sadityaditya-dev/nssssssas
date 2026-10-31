"""Grid-based sensor coverage metrics for an abstract LVLH observation strip."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from ..models import Satellite

@dataclass(frozen=True)
class CoverageResult:
    time_s: float
    coverage_fraction: float
    covered_cells: int
    total_cells: int
    active_sensors: int

class CoverageEvaluator:
    def __init__(self, x_bounds_m=(-2_000.0, 2_000.0), y_bounds_m=(-15_000.0, 15_000.0),
                 sensor_radius_m: float = 8_000.0, nx: int = 41, ny: int = 151):
        if sensor_radius_m <= 0 or nx < 2 or ny < 2:
            raise ValueError("sensor_radius_m must be > 0 and grid dimensions >= 2")
        self.x = np.linspace(*x_bounds_m, nx)
        self.y = np.linspace(*y_bounds_m, ny)
        self.xx, self.yy = np.meshgrid(self.x, self.y)
        self.radius2 = float(sensor_radius_m) ** 2
        self.total_cells = int(self.xx.size)

    def evaluate(self, time_s: float, satellites: dict[str, Satellite]) -> CoverageResult:
        covered = np.zeros(self.xx.shape, dtype=bool)
        active_sensors = 0
        for sat in satellites.values():
            if not sat.is_operational or not (sat.effective_capabilities & {"observation_highres", "observation_wide"}):
                continue
            active_sensors += 1
            sx, sy = float(sat.state[0]), float(sat.state[1])
            covered |= (self.xx - sx) ** 2 + (self.yy - sy) ** 2 <= self.radius2
        cells = int(covered.sum())
        return CoverageResult(float(time_s), cells / self.total_cells, cells, self.total_cells, active_sensors)

    def coverage_mask(self, satellites: dict[str, Satellite]) -> np.ndarray:
        covered = np.zeros(self.xx.shape, dtype=bool)
        for sat in satellites.values():
            if sat.is_operational and sat.effective_capabilities & {"observation_highres", "observation_wide"}:
                covered |= (self.xx - sat.state[0]) ** 2 + (self.yy - sat.state[1]) ** 2 <= self.radius2
        return covered
    def individual_contributions(self, satellites: dict[str, Satellite]) -> dict[str, float]:
        """Return marginal unique grid coverage per satellite as percentage points.

        The metric answers: how many grid cells would become uncovered if this
        sensor were removed while all other current sensors stayed active?
        """
        contributions: dict[str, float] = {}
        sensor_ids = [sid for sid, sat in satellites.items() if sat.is_operational and
                      sat.effective_capabilities & {"observation_highres", "observation_wide"}]
        for sid in satellites:
            if sid not in sensor_ids:
                contributions[sid] = 0.0
                continue
            others = {oid: sat for oid, sat in satellites.items() if oid != sid}
            uncovered_without = np.logical_not(self.coverage_mask(others))
            sx, sy = map(float, satellites[sid].state[:2])
            own = (self.xx - sx) ** 2 + (self.yy - sy) ** 2 <= self.radius2
            unique_cells = int(np.logical_and(own, uncovered_without).sum())
            contributions[sid] = 100.0 * unique_cells / self.total_cells
        return contributions

