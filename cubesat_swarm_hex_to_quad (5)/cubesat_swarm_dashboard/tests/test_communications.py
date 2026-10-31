import pytest

from cubesat_swarm.models import FaultKind, Satellite
from cubesat_swarm.simulation.communications import HeartbeatNetwork
from cubesat_swarm.simulation.engine import SimulationEngine


def test_heartbeat_sequence_numbers_increase_and_deliver_without_loss():
    sats = {sid: Satellite(sid, [0, 0, 0, 0]) for sid in ("S1", "S2")}
    network = HeartbeatNetwork(list(sats), heartbeat_period_s=1.0, missed_threshold=3)
    network.tick(0.0, sats)
    network.tick(1.0, sats)
    assert network.delivered_count == 4
    assert [r.sequence for r in network.records if r.sender_id == "S1" and r.receiver_id == "S2"] == [1, 2]
    assert network.suspicions["S1"] == set()


def test_failed_satellite_is_suspected_after_three_missed_periods():
    sats = [Satellite("S1", [0, 0, 0, 0]), Satellite("S2", [0, 0, 0, 0])]
    engine = SimulationEngine(sats, mean_motion_rad_s=0.0, dt_s=0.25,
                              heartbeat_period_s=1.0, missed_heartbeat_threshold=3)
    engine.schedule_fault(1.0, "S2", FaultKind.BUS_FAILURE)
    engine.run_until(3.0)
    assert "S2" in engine.network.suspicions["S1"]
    event = next(e for e in engine.network.events if e["type"] == "suspected_unreachable")
    assert event["observer"] == "S1"
    assert event["target"] == "S2"
    assert event["time_s"] == pytest.approx(3.0)


def test_packet_loss_is_reproducible_for_same_seed():
    sats = {sid: Satellite(sid, [0, 0, 0, 0]) for sid in ("S1", "S2", "S3")}
    a = HeartbeatNetwork(list(sats), packet_loss_probability=0.35, seed=42)
    b = HeartbeatNetwork(list(sats), packet_loss_probability=0.35, seed=42)
    for t in (0.0, 1.0, 2.0, 3.0):
        a.tick(t, sats)
        b.tick(t, sats)
    assert [(r.sender_id, r.receiver_id, r.sequence, r.delivered) for r in a.records] == [
        (r.sender_id, r.receiver_id, r.sequence, r.delivered) for r in b.records
    ]
    assert a.dropped_count == b.dropped_count


def test_suspicion_clears_when_heartbeat_resumes():
    sats = {sid: Satellite(sid, [0, 0, 0, 0]) for sid in ("S1", "S2")}
    network = HeartbeatNetwork(list(sats), missed_threshold=1, packet_loss_probability=0.0)
    network.tick(0.0, sats)
    sats["S2"].apply_fault(FaultKind.BUS_FAILURE)
    network.tick(1.0, sats)
    assert "S2" in network.suspicions["S1"]
    # The model supports a restored operational state for isolated network testing.
    sats["S2"].health_state = type(sats["S2"].health_state).NOMINAL
    network.tick(2.0, sats)
    assert "S2" not in network.suspicions["S1"]
