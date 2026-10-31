import numpy as np
import pytest

from cubesat_swarm.models import FaultKind, HealthState, Satellite
from cubesat_swarm.simulation.engine import SimulationEngine
from scenario import MEAN_MOTION_RAD_S, make_initial_satellites


def test_engine_has_six_unique_satellites_and_initial_snapshot():
    engine = SimulationEngine(make_initial_satellites(), MEAN_MOTION_RAD_S, dt_s=0.1)
    assert len(engine.satellites) == 6
    assert engine.history[0].time_s == 0.0
    assert engine.history[0].active_count == 6


def test_event_occurs_at_exact_time_inside_a_nominal_step():
    sat = Satellite("S1", [0, 100, 0, 0], {"compute"})
    engine = SimulationEngine([sat], mean_motion_rad_s=0.0, dt_s=1.0)
    engine.schedule_fault(0.35, "S1", FaultKind.BUS_FAILURE)
    engine.step()
    assert engine.time_s == pytest.approx(1.0)
    assert len(engine.event_log) == 1
    assert engine.event_log[0]["time_s"] == pytest.approx(0.35)
    assert engine.satellites["S1"].health_state is HealthState.FAILED


def test_controls_change_physical_state_and_are_bounded():
    sat = Satellite("S1", [0, 0, 0, 0], {"compute"}, max_acceleration_mps2=0.5)
    engine = SimulationEngine([sat], mean_motion_rad_s=0.0, dt_s=0.1,
                              control_law=lambda _sat, _time: (10.0, 0.0))
    engine.step()
    assert engine.satellites["S1"].state[2] == pytest.approx(0.05)
    assert engine.satellites["S1"].state[0] == pytest.approx(0.0025)
    assert engine.satellites["S1"].remaining_dv_mps == pytest.approx(9.95)


def test_failed_satellite_coasts_without_control_but_state_is_propagated():
    sat = Satellite("S1", [0, 0, 1, 0], {"compute"}, max_acceleration_mps2=1.0)
    sat.apply_fault(FaultKind.BUS_FAILURE)
    engine = SimulationEngine([sat], mean_motion_rad_s=0.0, dt_s=1.0,
                              control_law=lambda _sat, _time: (0.5, 0.0))
    engine.step()
    np.testing.assert_allclose(engine.satellites["S1"].state, [1.0, 0.0, 1.0, 0.0])
    assert engine.history[-1].active_count == 0


def test_run_until_uses_exact_final_time():
    engine = SimulationEngine([Satellite("S1", [0, 0, 0, 0])], 0.0, dt_s=0.3)
    snapshot = engine.run_until(1.0)
    assert snapshot.time_s == pytest.approx(1.0)


def test_invalid_unknown_or_past_fault_is_rejected():
    engine = SimulationEngine([Satellite("S1", [0, 0, 0, 0])], 0.0)
    with pytest.raises(ValueError, match="unknown satellite"):
        engine.schedule_fault(1.0, "S2", FaultKind.BUS_FAILURE)
    engine.run_until(1.0)
    with pytest.raises(ValueError, match="past"):
        engine.schedule_fault(0.5, "S1", FaultKind.BUS_FAILURE)


def test_duplicate_satellite_ids_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        SimulationEngine([Satellite("S1", [0, 0, 0, 0]), Satellite("S1", [0, 0, 0, 0])], 0.0)
