import pytest

from cubesat_swarm.models import FaultKind, HealthState, Satellite
from cubesat_swarm.simulation.events import EventQueue


def make_satellite():
    return Satellite("S1", [0, 0, 0, 0], {"compute", "observation_highres"})


def test_capability_is_removed_after_payload_failure():
    sat = make_satellite()
    sat.apply_fault(FaultKind.PAYLOAD_FAILURE, "observation_highres")
    assert "observation_highres" not in sat.effective_capabilities
    assert "compute" in sat.effective_capabilities
    assert sat.health_state is HealthState.DEGRADED


def test_bus_failure_removes_operational_status_and_control():
    sat = make_satellite()
    sat.apply_fault(FaultKind.BUS_FAILURE)
    assert not sat.is_operational
    assert sat.clamp_control([0.1, 0.1]).tolist() == [0.0, 0.0]


def test_thruster_derating_is_validated_and_cannot_silently_improve():
    sat = make_satellite()
    sat.apply_fault(FaultKind.THRUSTER_DEGRADATION, 0.5)
    sat.apply_fault(FaultKind.THRUSTER_DEGRADATION, 0.8)
    assert sat.thruster_scale == 0.5
    with pytest.raises(ValueError):
        sat.apply_fault(FaultKind.THRUSTER_DEGRADATION, 1.2)


def test_event_queue_is_time_ordered_and_stable_for_ties():
    q = EventQueue()
    late = q.schedule(5.0, "S1", FaultKind.BUS_FAILURE)
    first = q.schedule(2.0, "S2", FaultKind.BUS_FAILURE)
    tied = q.schedule(2.0, "S3", FaultKind.BUS_FAILURE)
    assert q.pop_due(1.0) == []
    due = q.pop_due(2.0)
    assert [e.satellite_id for e in due] == ["S2", "S3"]
    assert q.peek_time() == 5.0
    assert late.time_s == 5.0
    assert first.sequence < tied.sequence


def test_event_queue_rejects_invalid_payload_event():
    with pytest.raises(ValueError):
        EventQueue().schedule(1.0, "S1", FaultKind.PAYLOAD_FAILURE)
