"""Reproducible initial configuration for the planar observation-strip scenario."""

from __future__ import annotations
import math
from cubesat_swarm.models import Satellite
from cubesat_swarm.simulation.engine import SimulationEngine

EARTH_MU_M3_S2 = 3.986004418e14
EARTH_RADIUS_M = 6_378_137.0
REFERENCE_ALTITUDE_M = 500_000.0
REFERENCE_RADIUS_M = EARTH_RADIUS_M + REFERENCE_ALTITUDE_M
MEAN_MOTION_RAD_S = math.sqrt(EARTH_MU_M3_S2 / REFERENCE_RADIUS_M**3)


def make_initial_satellites() -> list[Satellite]:
    """Create the proposed six-satellite strip geometry in SI units.

    Observation-capable agents are S2-S5. S2/S3 represent higher-quality sensors;
    S4/S5 represent lower-quality, wider-area sensors. This is a configurable
    software-test scenario, not a claim about flight hardware.
    """
    hexagon_positions_m = [(-10000.0, 0.0), (-5000.0, -8660.254), (5000.0, -8660.254),
                           (10000.0, 0.0), (5000.0, 8660.254), (-5000.0, 8660.254)]
    profile = [
        ("S1", {"compute", "isl"}, "COORDINATOR"),
        ("S2", {"compute", "isl", "observation_highres"}, "MAPPER_1"),
        ("S3", {"compute", "isl", "observation_highres"}, "MAPPER_2"),
        ("S4", {"compute", "isl", "observation_wide"}, "TRACKER_1"),
        ("S5", {"compute", "isl", "observation_wide"}, "TRACKER_2"),
        ("S6", {"compute", "isl", "enhanced_relay"}, "RELAY"),
    ]
    sats: list[Satellite] = []
    for (sat_id, capabilities, role), (x_m, y_m) in zip(profile, hexagon_positions_m, strict=True):
        sats.append(
            Satellite(
                satellite_id=sat_id,
                state=[x_m, y_m, 0.0, 0.0],
                capabilities=capabilities,
                current_role=role,
                remaining_dv_mps=10.0,
                max_acceleration_mps2=1e-3,
            )
        )
    return sats


def make_engine(dt_s: float = 0.1) -> SimulationEngine:
    return SimulationEngine(make_initial_satellites(), MEAN_MOTION_RAD_S, dt_s=dt_s)
