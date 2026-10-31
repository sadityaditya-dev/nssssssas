from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from cubesat_swarm.dashboard.server import DashboardRequestHandler, DashboardSession


def test_dashboard_session_initializes_six_satellites_and_models():
    session = DashboardSession()
    data = session.state()
    assert len(data["satellites"]) == 6
    assert data["mission"]["active_satellites"] == 6
    assert data["simulation"]["time_s"] == 0.0
    assert len(data["roles"]) == 6
    assert len(data["coverage"]["models"]) == 5
    assert data["mission"]["assignment_feasible"] is True


def test_communication_isolation_is_a_suspicion_not_a_bus_failure_and_can_clear():
    session = DashboardSession()
    session.set_communication_isolation("S3", True)
    session.start()
    session.advance(4.0)
    data = session.state()
    s3 = next(s for s in data["satellites"] if s["satellite_id"] == "S3")
    assert s3["is_operational"] is True
    assert s3["health_state"] == "nominal"
    assert s3["is_isolated"] is True
    assert any("S3" in targets for targets in data["communication"]["suspicions"].values())
    session.set_communication_isolation("S3", False)
    session.advance(1.0, force=True)
    data = session.state()
    assert "S3" not in data["communication"]["isolated_ids"]
    assert any(e["type"] == "suspicion_cleared" and e.get("target") == "S3" for e in data["events"])


def test_scheduled_bus_failure_triggers_role_reconciliation_and_excludes_failed_satellite():
    session = DashboardSession(fault_schedule=[])
    session.schedule_fault({"time_s": 1.0, "satellite_id": "S2", "kind": "bus_failure", "value": None})
    session.start()
    session.advance(2.0)
    data = session.state()
    assert data["mission"]["active_satellites"] == 5
    assert "S2" not in data["mission"]["active_ids"]
    assert "S2" not in data["mission"]["role_assignment"]
    assert all(r["assigned_satellite"] != "S2" for r in data["roles"])


def test_full_default_end_to_end_two_failure_scenario():
    session = DashboardSession()
    session.advance(120.0, force=True)
    data = session.state()
    assert data["simulation"]["finished"] is True
    assert data["simulation"]["time_s"] == 120.0
    assert data["mission"]["active_satellites"] == 4
    assert set(data["mission"]["failed_ids"]) == {"S2", "S5"}
    assert data["mission"]["assignment_feasible"] is True
    assert data["mission"]["critical_roles_restored"] is True
    assert "S2" not in data["mission"]["role_assignment"]
    assert data["coverage"]["coverage_pct"] == data["mission"]["coverage_pct"]
    assert len(data["metrics"]) >= 100
    exported = session.export_summary()
    assert exported["simulation"]["finished"] is True
    assert json.loads(json.dumps(exported))["mission"]["active_satellites"] == 4
    assert "coverage_fraction" in session.export_csv()


def test_local_http_dashboard_serves_ui_and_live_state_api():
    session = DashboardSession(fault_schedule=[])
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
    server.dashboard_session = session
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            assert response.status == 200
            assert b"Mission overview" in response.read()
        with urllib.request.urlopen(base + "/api/state", timeout=5) as response:
            state = json.loads(response.read())
        assert state["mission"]["initial_satellites"] == 6
        req = urllib.request.Request(base + "/api/start", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            assert json.loads(response.read())["ok"] is True
        req = urllib.request.Request(base + "/api/tick", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            assert json.loads(response.read())["ok"] is True
        with urllib.request.urlopen(base + "/api/state", timeout=5) as response:
            state2 = json.loads(response.read())
        assert state2["simulation"]["time_s"] > 0
    finally:
        server.shutdown()
        server.server_close()


def test_satellite_initial_configuration_is_applied_to_the_live_dashboard():
    from copy import deepcopy
    from cubesat_swarm.dashboard.server import DEFAULT_CONFIG

    config = deepcopy(DEFAULT_CONFIG)
    config["satellite_settings"]["S3"]["state"] = [500.0, 6300.0, 0.25, -0.1]
    config["satellite_settings"]["S3"]["energy_percent"] = 63.0
    config["satellite_settings"]["S3"]["remaining_dv_mps"] = 4.5
    session = DashboardSession(config=config, fault_schedule=[])
    s3 = next(s for s in session.state()["satellites"] if s["satellite_id"] == "S3")
    assert s3["position_x_m"] == 500.0
    assert s3["position_y_m"] == 6300.0
    assert s3["velocity_x_mps"] == 0.25
    assert s3["energy_percent"] == 63.0
    assert s3["remaining_dv_mps"] == 4.5


def test_payload_fault_reconciles_roles_without_assigning_a_lost_sensor():
    session = DashboardSession(fault_schedule=[])
    session.inject_fault_now({"satellite_id": "S2", "kind": "payload_failure", "value": "observation_highres"})
    data = session.state()
    s2 = next(s for s in data["satellites"] if s["satellite_id"] == "S2")
    assert s2["is_operational"] is True
    assert "observation_highres" not in s2["capabilities"]
    assert s2["role"] not in {"MAPPER_1", "MAPPER_2", "TRACKER_1", "TRACKER_2"}
    assert data["mission"]["assignment_feasible"] is True


def test_http_tick_uses_supplied_elapsed_simulation_time():
    session = DashboardSession(fault_schedule=[])
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
    server.dashboard_session = session
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        req = urllib.request.Request(base + "/api/start", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5):
            pass
        req = urllib.request.Request(base + "/api/tick", data=json.dumps({"seconds": 0.5}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            assert json.loads(response.read())["ok"] is True
        assert abs(session.engine.time_s - 0.5) < 1e-8
    finally:
        server.shutdown()
        server.server_close()


def test_config_rejects_nonpositive_heartbeat_period():
    from copy import deepcopy
    from cubesat_swarm.dashboard.server import DEFAULT_CONFIG

    config = deepcopy(DEFAULT_CONFIG)
    config["heartbeat_period_s"] = 0
    try:
        DashboardSession(config=config, fault_schedule=[])
    except ValueError as exc:
        assert "Heartbeat period" in str(exc)
    else:
        raise AssertionError("Invalid heartbeat period should be rejected")


def test_random_two_failure_scenario_cancels_pending_default_faults():
    session = DashboardSession()
    events = session.inject_random_failures(2)
    data = session.state()
    assert len(events) == 2
    assert data["mission"]["active_satellites"] == 4
    assert len(data["mission"]["failed_ids"]) == 2
    assert data["mission"]["assignment_feasible"] is True
    assert data["mission"]["critical_roles_restored"] is True
    assert len(session.engine.events) == 0
    assert any(e.get("type") == "scheduled_faults_cancelled" for e in session.engine.event_log)


def test_assignment_candidate_communication_score_tracks_temporary_isolation():
    session = DashboardSession(fault_schedule=[])
    baseline = next(c for c in session.state()["assignment_matrix"]
                    if c["satellite_id"] == "S1" and c["role"] == "COORDINATOR")
    session.set_communication_isolation("S1", True)
    isolated = next(c for c in session.state()["assignment_matrix"]
                    if c["satellite_id"] == "S1" and c["role"] == "COORDINATOR")
    assert baseline["communication_quality_pct"] > 0
    assert isolated["communication_quality_pct"] == 0
    assert isolated["communication_penalty"] > baseline["communication_penalty"]
    session.set_communication_isolation("S1", False)
    restored = next(c for c in session.state()["assignment_matrix"]
                    if c["satellite_id"] == "S1" and c["role"] == "COORDINATOR")
    assert restored["communication_quality_pct"] == baseline["communication_quality_pct"]


def test_formation_geometry_transitions_from_hexagon_to_four_satellite_polygon():
    session = DashboardSession()
    initial = session.state()
    assert initial["formation"]["formation_shape"] == "hexagon"
    assert len(initial["formation"]["target_positions"]) == 6

    session.advance(40.0, force=True)
    after = session.state()
    assert set(after["mission"]["failed_ids"]) == {"S2", "S5"}
    assert after["formation"]["formation_shape"] == "quadrilateral"
    targets = after["formation"]["target_positions"]
    assert set(targets) == {"S1", "S3", "S4", "S6"}
    radii = [(p["x"] ** 2 + p["y"] ** 2) ** 0.5 for p in targets.values()]
    assert max(radii) - min(radii) < 1e-6

    before_positions = {s["satellite_id"]: (s["position_x_m"], s["position_y_m"])
                        for s in after["satellites"] if s["is_operational"]}
    session.advance(20.0, force=True)
    later = session.state()
    after_positions = {s["satellite_id"]: (s["position_x_m"], s["position_y_m"])
                       for s in later["satellites"] if s["is_operational"]}
    assert any(((after_positions[sid][0] - before_positions[sid][0]) ** 2
                + (after_positions[sid][1] - before_positions[sid][1]) ** 2) > 1.0
               for sid in before_positions)
