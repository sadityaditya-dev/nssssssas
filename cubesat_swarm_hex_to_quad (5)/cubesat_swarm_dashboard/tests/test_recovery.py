import numpy as np
import pytest
from cubesat_swarm.models import FaultKind, Satellite
from cubesat_swarm.recovery import RoleAssigner, CoverageEvaluator, SelfHealingController
from cubesat_swarm.simulation.engine import SimulationEngine
from scenario import make_initial_satellites, MEAN_MOTION_RAD_S


def test_initial_role_assignment_is_capability_feasible():
    sats = make_initial_satellites(); controller = SelfHealingController({s.satellite_id:s for s in sats}, MEAN_MOTION_RAD_S)
    assert not controller.degraded
    assert len(controller.assignment) == 6
    assert len(set(controller.assignment.values())) == 6


def test_two_failures_reassign_four_critical_roles():
    sats = make_initial_satellites(); sat_map = {s.satellite_id:s for s in sats}
    controller = SelfHealingController(sat_map, MEAN_MOTION_RAD_S)
    sat_map['S2'].apply_fault(FaultKind.BUS_FAILURE); controller.reconcile(20)
    sat_map['S5'].apply_fault(FaultKind.BUS_FAILURE); controller.reconcile(40)
    assert not controller.degraded
    assert set(controller.assignment) == {'S1','S3','S4','S6'}
    assert set(controller.assignment.values()) == {'COORDINATOR','MAPPER_1','MAPPER_2','RELAY'}
    for sid, role in controller.assignment.items():
        sat = sat_map[sid]
        assert sat.is_operational
        assert sat.effective_capabilities & next(r.required_any for r in __import__('cubesat_swarm.recovery.assignment', fromlist=['ROLES']).ROLES if r.name == role)


def test_less_than_four_active_is_explicitly_infeasible():
    sats = make_initial_satellites(); sat_map = {s.satellite_id:s for s in sats}
    controller = SelfHealingController(sat_map, MEAN_MOTION_RAD_S)
    for sid in ['S2','S3','S4']:
        sat_map[sid].apply_fault(FaultKind.BUS_FAILURE)
    controller.reconcile(50)
    assert controller.degraded
    assert controller.assignment_log[-1]['type'] == 'assignment_infeasible'


def test_coverage_is_bounded_and_loses_sensor_when_payload_fails():
    sats = make_initial_satellites(); sat_map = {s.satellite_id:s for s in sats}
    evaluator = CoverageEvaluator(nx=21, ny=51)
    full = evaluator.evaluate(0, sat_map)
    sat_map['S2'].apply_fault(FaultKind.PAYLOAD_FAILURE, 'observation_highres')
    reduced = evaluator.evaluate(1, sat_map)
    assert 0 <= reduced.coverage_fraction <= full.coverage_fraction <= 1
    assert reduced.active_sensors == full.active_sensors - 1


def test_full_simulation_two_faults_reassign_and_control():
    sats = make_initial_satellites(); sat_map = {s.satellite_id:s for s in sats}
    controller = SelfHealingController(sat_map, MEAN_MOTION_RAD_S)
    engine = SimulationEngine(sats, MEAN_MOTION_RAD_S, dt_s=0.5, control_law=controller.control)
    engine.schedule_fault(2.0, 'S2', FaultKind.BUS_FAILURE)
    engine.schedule_fault(4.0, 'S5', FaultKind.BUS_FAILURE)
    engine.run_until(10.0)
    assert len(engine.active_ids) == 4
    assert set(controller.assignment) == {'S1','S3','S4','S6'}
    assert any(e['type'] == 'role_reassignment' and len(e['active_ids']) == 4 for e in controller.assignment_log)
    assert all(np.all(np.isfinite(s.state)) for s in sats)
    assert all(s.remaining_dv_mps >= 0 for s in sats)


def test_assignment_rejects_critical_role_shortage():
    sats = make_initial_satellites()
    for sat in sats[:3]: sat.apply_fault(FaultKind.BUS_FAILURE)
    result = RoleAssigner().assign(sats)
    assert not result.feasible
