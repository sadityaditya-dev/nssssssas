"""Interactive local web dashboard backed by the live CubeSat simulation engine.

Only Python's standard-library HTTP server is required. Simulation dynamics,
recovery assignment, heartbeat monitoring and coverage calculations come from
this project's engine modules.
"""
from __future__ import annotations

import csv
import io
import json
import copy
import random
import math
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np

from ..models import FaultKind, HealthState
from ..recovery import CoverageEvaluator, SelfHealingController
from ..recovery.assignment import CRITICAL_ROLES, ROLE_BY_NAME, ROLES
from ..simulation.engine import SimulationEngine
from .. import __version__ as _pkg_version
from scenario import make_initial_satellites, MEAN_MOTION_RAD_S

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "dashboard"
SATELLITE_IDS = [f"S{i}" for i in range(1, 7)]

DEFAULT_SATELLITE_SETTINGS: dict[str, dict[str, Any]] = {
    # Initial regular hexagon in the LVLH plane; x/y are metres.
    "S1": {"state": [-10000.0, 0.0, 0.0, 0.0], "capabilities": ["compute", "isl"], "energy_percent": 100.0, "remaining_dv_mps": 10.0, "max_acceleration_mps2": 0.001},
    "S2": {"state": [-5000.0, -8660.254, 0.0, 0.0], "capabilities": ["compute", "isl", "observation_highres"], "energy_percent": 100.0, "remaining_dv_mps": 10.0, "max_acceleration_mps2": 0.001},
    "S3": {"state": [5000.0, -8660.254, 0.0, 0.0], "capabilities": ["compute", "isl", "observation_highres"], "energy_percent": 100.0, "remaining_dv_mps": 10.0, "max_acceleration_mps2": 0.001},
    "S4": {"state": [10000.0, 0.0, 0.0, 0.0], "capabilities": ["compute", "isl", "observation_wide"], "energy_percent": 100.0, "remaining_dv_mps": 10.0, "max_acceleration_mps2": 0.001},
    "S5": {"state": [5000.0, 8660.254, 0.0, 0.0], "capabilities": ["compute", "isl", "observation_wide"], "energy_percent": 100.0, "remaining_dv_mps": 10.0, "max_acceleration_mps2": 0.001},
    "S6": {"state": [-5000.0, 8660.254, 0.0, 0.0], "capabilities": ["compute", "isl", "enhanced_relay"], "energy_percent": 100.0, "remaining_dv_mps": 10.0, "max_acceleration_mps2": 0.001},
}

DEFAULT_CONFIG: dict[str, Any] = {
    "duration_s": 120.0,
    "dt_s": 0.2,
    "speed": 5.0,
    "packet_loss_probability": 0.0,
    "seed": 7,
    "heartbeat_period_s": 1.0,
    "missed_heartbeat_threshold": 3,
    "sensor_radius_m": 8000.0,
    "communication_range_m": 18000.0,
    "safe_distance_m": 250.0,
    "coverage_target_pct": 75.0,
    "nx": 41,
    "ny": 151,
    "satellite_settings": DEFAULT_SATELLITE_SETTINGS,
}
DEFAULT_FAULTS: list[dict[str, Any]] = [
    {"time_s": 20.0, "satellite_id": "S2", "kind": "bus_failure", "value": None},
    {"time_s": 40.0, "satellite_id": "S5", "kind": "bus_failure", "value": None},
]


def _json_safe(obj: Any) -> Any:
    """Convert NumPy values and non-finite values into strict JSON-safe values."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, deque)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, set):
        return sorted(_json_safe(v) for v in obj)
    if isinstance(obj, np.ndarray):
        return _json_safe(obj.tolist())
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)


class DashboardSession:
    """Mutable session state for a single interactive simulation instance."""

    def __init__(self, config: dict[str, Any] | None = None,
                 fault_schedule: list[dict[str, Any]] | None = None) -> None:
        self.lock = threading.RLock()
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        if config:
            self.config.update(copy.deepcopy(config))
        self.fault_schedule = [dict(f) for f in (fault_schedule if fault_schedule is not None else DEFAULT_FAULTS)]
        self.reset(keep_config=True)

    def _new_simulation(self) -> None:
        sats = make_initial_satellites()
        sat_settings = self.config.get("satellite_settings", DEFAULT_SATELLITE_SETTINGS)
        for sat in sats:
            item = sat_settings.get(sat.satellite_id, DEFAULT_SATELLITE_SETTINGS[sat.satellite_id])
            sat.state = np.asarray(item.get("state", sat.state), dtype=float).copy()
            sat.capabilities = set(item.get("capabilities", sat.capabilities))
            sat.energy_percent = float(item.get("energy_percent", 100.0))
            sat.remaining_dv_mps = float(item.get("remaining_dv_mps", 10.0))
            sat.max_acceleration_mps2 = float(item.get("max_acceleration_mps2", 1e-3))
        self.initial_states = {s.satellite_id: [float(v) for v in s.state] for s in sats}
        self.initial_energy_percent = {s.satellite_id: float(s.energy_percent) for s in sats}
        self.initial_delta_v_mps = {s.satellite_id: float(s.remaining_dv_mps) for s in sats}
        self.satellites = {s.satellite_id: s for s in sats}
        self.coverage = CoverageEvaluator(
            sensor_radius_m=float(self.config["sensor_radius_m"]),
            nx=int(self.config["nx"]), ny=int(self.config["ny"]),
        )
        self.controller = SelfHealingController(
            self.satellites, MEAN_MOTION_RAD_S,
            safety_distance_m=float(self.config["safe_distance_m"]),
            coverage_evaluator=self.coverage,
            communication_range_m=float(self.config["communication_range_m"]),
            packet_loss_probability=float(self.config["packet_loss_probability"]),
        )
        self.engine = SimulationEngine(
            sats, MEAN_MOTION_RAD_S,
            dt_s=float(self.config["dt_s"]),
            control_law=self.controller.control,
            heartbeat_period_s=float(self.config["heartbeat_period_s"]),
            missed_heartbeat_threshold=int(self.config["missed_heartbeat_threshold"]),
            packet_loss_probability=float(self.config["packet_loss_probability"]),
            communication_seed=int(self.config["seed"]),
        )
        self.isolated_ids: set[str] = set()
        self.controller.isolated_ids = self.isolated_ids
        self.running = False
        self.finished = False
        self.status_message = "Ready — start the simulation"
        self.metric_rows: list[dict[str, Any]] = []
        self.last_metric_t = -1.0
        self.initial_cov = self.coverage.evaluate(0.0, self.satellites)
        self._record_metric(force=True)
        self._scheduled_ids: list[dict[str, Any]] = []
        # Schedule configured faults on the simulation clock; failures are applied
        # by the engine at their exact event times.
        for event in self.fault_schedule:
            self._schedule_fault_locked(event)

    def reset(self, keep_config: bool = True) -> None:
        with self.lock:
            if not keep_config:
                self.config = copy.deepcopy(DEFAULT_CONFIG)
                self.fault_schedule = [dict(f) for f in DEFAULT_FAULTS]
            self._validate_config(self.config)
            self._new_simulation()

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> None:
        if not math.isfinite(float(config["duration_s"])) or float(config["duration_s"]) <= 0:
            raise ValueError("Simulation duration must be greater than zero")
        if not math.isfinite(float(config["dt_s"])) or not 0 < float(config["dt_s"]) <= 2:
            raise ValueError("Integration step must be in (0, 2] seconds")
        if not 0 <= float(config["packet_loss_probability"]) < 1:
            raise ValueError("Packet loss must be between 0 and 1 (excluding 1)")
        if not 0.1 <= float(config["speed"]) <= 50:
            raise ValueError("Playback speed must be between 0.1 and 50 simulated seconds per wall second")
        if int(config["missed_heartbeat_threshold"]) < 1:
            raise ValueError("Missed-heartbeat threshold must be at least 1")
        if (not math.isfinite(float(config["sensor_radius_m"])) or not math.isfinite(float(config["communication_range_m"]))
                or float(config["sensor_radius_m"]) <= 0 or float(config["communication_range_m"]) <= 0):
            raise ValueError("Sensor radius and communication range must be finite and positive")
        if not math.isfinite(float(config["safe_distance_m"])) or float(config["safe_distance_m"]) <= 0:
            raise ValueError("Safe distance must be finite and positive")
        if not math.isfinite(float(config["heartbeat_period_s"])) or float(config["heartbeat_period_s"]) <= 0:
            raise ValueError("Heartbeat period must be finite and positive")
        if not 0 <= float(config["coverage_target_pct"]) <= 100:
            raise ValueError("Coverage target must be between 0 and 100 percent")
        if int(config["nx"]) < 5 or int(config["ny"]) < 5:
            raise ValueError("Coverage grid dimensions must be at least 5 by 5")
        sat_settings = config.get("satellite_settings", DEFAULT_SATELLITE_SETTINGS)
        if not isinstance(sat_settings, dict):
            raise ValueError("satellite_settings must be an object keyed by S1–S6")
        for sid in SATELLITE_IDS:
            item = sat_settings.get(sid)
            if not isinstance(item, dict):
                raise ValueError(f"Missing initial settings for {sid}")
            state = np.asarray(item.get("state", []), dtype=float)
            if state.shape != (4,) or not np.all(np.isfinite(state)):
                raise ValueError(f"Initial state for {sid} must contain finite x, y, vx, vy values")
            energy = float(item.get("energy_percent", 100))
            dv = float(item.get("remaining_dv_mps", 10))
            accel = float(item.get("max_acceleration_mps2", 0.001))
            if not math.isfinite(energy) or not 0 <= energy <= 100:
                raise ValueError(f"Initial energy for {sid} must be between 0 and 100 percent")
            if not math.isfinite(dv) or dv < 0 or not math.isfinite(accel) or accel < 0:
                raise ValueError(f"Initial maneuver resources for {sid} must be finite and non-negative")
            capabilities = item.get("capabilities", [])
            if not isinstance(capabilities, list) or not all(isinstance(x, str) and x.strip() for x in capabilities):
                raise ValueError(f"Capabilities for {sid} must be a list of non-empty strings")

    def _schedule_fault_locked(self, event: dict[str, Any]) -> dict[str, Any]:
        sid = str(event.get("satellite_id", ""))
        if sid not in self.satellites:
            raise ValueError(f"Unknown satellite: {sid}")
        time_s = float(event.get("time_s", 0))
        if time_s < self.engine.time_s - 1e-9:
            raise ValueError("Cannot schedule a fault in the simulation past")
        kind = FaultKind(str(event.get("kind", FaultKind.BUS_FAILURE.value)))
        value = event.get("value")
        if kind is FaultKind.PAYLOAD_FAILURE and value not in self.satellites[sid].capabilities:
            raise ValueError("Select a capability installed on this satellite")
        if kind is FaultKind.THRUSTER_DEGRADATION:
            value = float(value)
            if not 0 <= value <= 1:
                raise ValueError("Thruster authority must be between 0 and 1")
        scheduled = self.engine.schedule_fault(time_s, sid, kind, value)
        clean = {"time_s": scheduled.time_s, "satellite_id": sid,
                 "kind": scheduled.kind.value, "value": scheduled.value,
                 "queue_sequence": scheduled.sequence}
        self._scheduled_ids.append(clean)
        return clean

    def schedule_fault(self, event: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            return self._schedule_fault_locked(event)

    def _cancel_pending_faults(self, satellite_ids: set[str] | None = None) -> list[dict[str, Any]]:
        now = float(self.engine.time_s)
        removed = self.engine.events.cancel_where(
            lambda event: event.time_s >= now - 1e-9
            and (satellite_ids is None or event.satellite_id in satellite_ids)
        )
        if not removed:
            return []
        removed_sequences = {event.sequence for event in removed}
        self._scheduled_ids = [entry for entry in self._scheduled_ids
                               if entry.get("queue_sequence") not in removed_sequences]
        rows = [{"time_s": event.time_s, "satellite_id": event.satellite_id,
                 "kind": event.kind.value, "value": event.value,
                 "queue_sequence": event.sequence} for event in removed]
        self.engine.event_log.append({
            "time_s": now, "type": "scheduled_faults_cancelled",
            "description": f"Cancelled {len(rows)} pending scheduled fault(s) to avoid conflicting injections.",
            "cancelled_faults": rows,
        })
        return rows

    def inject_fault_now(self, event: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            sid = str(event.get("satellite_id", ""))
            if sid not in self.satellites:
                raise ValueError(f"Unknown satellite: {sid}")
            kind = FaultKind(str(event.get("kind", FaultKind.BUS_FAILURE.value)))
            sat = self.satellites[sid]
            if not sat.is_operational:
                raise ValueError(f"{sid} is already failed; reset before injecting another fault")
            value = event.get("value")
            if kind is FaultKind.PAYLOAD_FAILURE and value not in sat.capabilities:
                raise ValueError("Select a capability installed on this satellite")
            if kind is FaultKind.THRUSTER_DEGRADATION:
                value = float(value)
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("Thruster authority must be between 0 and 1")
            if kind is FaultKind.BUS_FAILURE:
                self._cancel_pending_faults({sid})
            description = sat.apply_fault(kind, value)
            entry = {"time_s": float(self.engine.time_s), "requested_time_s": float(self.engine.time_s),
                     "satellite_id": sid, "kind": kind.value, "description": description,
                     "source": "dashboard_manual_injection"}
            self.engine.event_log.append(entry)
            self.controller.reconcile(self.engine.time_s, force=True)
            self._record_metric(force=True)
            self.status_message = f"Fault injected: {sid} — {kind.value}"
            return entry

    def inject_random_failures(self, count: int) -> list[dict[str, Any]]:
        with self.lock:
            if count < 1:
                raise ValueError("Random failure count must be at least 1")
            candidates = list(self.engine.active_ids)
            if count > len(candidates):
                raise ValueError(f"Only {len(candidates)} operational satellites remain")
            # Random injection is a distinct test mode: cancel future preset faults
            # so the selected one-/two-failure scenario does not accidentally gain
            # additional failures later in the run.
            self._cancel_pending_faults()
            rng = random.Random(int(self.config["seed"]) + int(self.engine.time_s * 1000))
            selected = rng.sample(candidates, count)
            events = []
            for sid in selected:
                description = self.satellites[sid].apply_fault(FaultKind.BUS_FAILURE)
                entry = {"time_s": float(self.engine.time_s), "requested_time_s": float(self.engine.time_s),
                         "satellite_id": sid, "kind": FaultKind.BUS_FAILURE.value, "description": description,
                         "source": "dashboard_random_failure_injection"}
                self.engine.event_log.append(entry); events.append(entry)
            self.controller.reconcile(self.engine.time_s, force=True)
            self._record_metric(force=True)
            self.status_message = f"Randomly failed {count} spacecraft: {', '.join(selected)}"
            return events

    def set_communication_isolation(self, sid: str, isolated: bool) -> None:
        with self.lock:
            if sid not in self.satellites:
                raise ValueError(f"Unknown satellite: {sid}")
            if isolated and not self.satellites[sid].is_operational:
                raise ValueError(f"Cannot isolate a failed satellite ({sid})")
            self.engine.network.set_isolated(sid, bool(isolated))
            if isolated:
                self.isolated_ids.add(sid)
                self.engine.network.events.append({"time_s": self.engine.time_s,
                    "type": "communication_isolated", "target": sid,
                    "reason": "dashboard injected temporary communication outage"})
                self.status_message = f"Temporary communication outage enabled for {sid}"
            else:
                self.isolated_ids.discard(sid)
                self.engine.network.events.append({"time_s": self.engine.time_s,
                    "type": "communication_restored", "target": sid,
                    "reason": "dashboard restored communication"})
                self.status_message = f"Communication restored for {sid}"

    def start(self) -> None:
        with self.lock:
            if self.engine.time_s >= float(self.config["duration_s"]) - 1e-9:
                self.status_message = "Simulation has ended — reset to run again"
                self.finished = True
                self.running = False
                return
            self.running = True
            self.finished = False
            self.status_message = "Simulation running"

    def pause(self) -> None:
        with self.lock:
            self.running = False
            self.status_message = "Simulation paused"

    def advance(self, simulated_seconds: float | None = None, force: bool = False) -> None:
        with self.lock:
            if not self.running and not force:
                return
            remaining = float(self.config["duration_s"]) - self.engine.time_s
            requested = float(simulated_seconds if simulated_seconds is not None else 0.25 * float(self.config["speed"]))
            target_increment = max(0.0, min(requested, remaining))
            target = self.engine.time_s + target_increment
            while self.engine.time_s < target - 1e-9:
                self.engine.step(min(float(self.config["dt_s"]), target - self.engine.time_s))
                # Keep membership/role state in sync even with a no-control law step.
                self.controller.reconcile(self.engine.time_s)
                if self.engine.time_s - self.last_metric_t >= 1.0 - 1e-9:
                    self._record_metric(force=True)
            if self.engine.time_s >= float(self.config["duration_s"]) - 1e-8:
                self.engine.time_s = float(self.config["duration_s"])
                self.running = False
                self.finished = True
                self.status_message = "Simulation completed"
                self._record_metric(force=True)

    def _record_metric(self, force: bool = False) -> None:
        now = float(self.engine.time_s)
        if not force and now - self.last_metric_t < 1.0 - 1e-9:
            return
        cov = self.coverage.evaluate(now, self.satellites)
        connections = self._communication_graph()
        self.metric_rows.append({
            "time_s": now,
            "coverage_fraction": cov.coverage_fraction,
            "covered_cells": cov.covered_cells,
            "total_cells": cov.total_cells,
            "active_sensors": cov.active_sensors,
            "active_satellites": len(self.engine.active_ids),
            "connected_fraction": connections["connected_fraction"],
            "formation_error_rms_m": self._formation_error_rms(),
            "current_energy_percent_sum": sum(float(s.energy_percent) for s in self.satellites.values()),
            "energy_draw_pct_points": sum(self.initial_energy_percent.values()) - sum(float(s.energy_percent) for s in self.satellites.values()),
            "assignment_feasible": not self.controller.degraded,
            "roles": json.dumps(self.controller.assignment, sort_keys=True),
        })
        if len(self.metric_rows) > 1000:
            self.metric_rows = self.metric_rows[-1000:]
        self.last_metric_t = now

    def _communication_graph(self) -> dict[str, Any]:
        active = [sid for sid, sat in self.satellites.items() if sat.is_operational]
        links: list[dict[str, Any]] = []
        adjacency = {sid: set() for sid in active}
        range_m = float(self.config["communication_range_m"])
        packet_loss = float(self.config["packet_loss_probability"])
        for i, a in enumerate(active):
            for b in active[i + 1:]:
                sa, sb = self.satellites[a], self.satellites[b]
                distance = float(np.linalg.norm(sa.state[:2] - sb.state[:2]))
                feasible = (distance <= range_m and "isl" in sa.effective_capabilities
                            and "isl" in sb.effective_capabilities and a not in self.isolated_ids
                            and b not in self.isolated_ids)
                if feasible:
                    adjacency[a].add(b); adjacency[b].add(a)
                links.append({"a": a, "b": b, "distance_m": distance,
                              "up": bool(feasible),
                              "quality_pct": max(0.0, 100.0 * (1.0 - distance / range_m) * (1.0 - packet_loss)) if feasible else 0.0})
        visited: set[str] = set()
        components: list[list[str]] = []
        for sid in active:
            if sid in visited:
                continue
            stack = [sid]; component: list[str] = []; visited.add(sid)
            while stack:
                node = stack.pop(); component.append(node)
                for nbr in adjacency[node]:
                    if nbr not in visited:
                        visited.add(nbr); stack.append(nbr)
            components.append(sorted(component))
        largest = max((len(c) for c in components), default=0)
        connected_fraction = (largest / len(active)) if active else 0.0
        return {"links": links, "components": components,
                "connected_fraction": connected_fraction,
                "active_nodes": active, "link_up_count": sum(1 for x in links if x["up"]),
                "possible_links": len(links)}

    def _formation_error_by_satellite(self) -> dict[str, float | None]:
        errors: dict[str, float | None] = {}
        for sid, sat in self.satellites.items():
            role_name = self.controller.assignment.get(sid)
            if not sat.is_operational or role_name not in ROLE_BY_NAME:
                errors[sid] = None; continue
            target_x, target_y = self.controller.formation_targets.get(sid, (float(sat.state[0]), float(sat.state[1])))
            errors[sid] = float(math.hypot(float(sat.state[0]) - target_x, float(sat.state[1]) - target_y))
        return errors

    def _formation_error_rms(self) -> float:
        errors = [v for v in self._formation_error_by_satellite().values() if v is not None]
        return float(math.sqrt(sum(e * e for e in errors) / len(errors))) if errors else 0.0

    def _coverage_details(self) -> dict[str, Any]:
        cov = self.coverage.evaluate(self.engine.time_s, self.satellites)
        mask = self.coverage.coverage_mask(self.satellites)
        x_grid, y_grid = self.coverage.xx, self.coverage.yy
        cell_count = int(mask.sum())
        point_ys = [-14000, -9000, -4000, 0, 4000, 9000, 14000]
        points = []
        r2 = float(self.config["sensor_radius_m"]) ** 2
        for idx, y in enumerate(point_ys, start=1):
            is_covered = any(s.is_operational and bool(s.effective_capabilities & {"observation_highres", "observation_wide"})
                             and (float(s.state[0]) ** 2 + (float(s.state[1]) - y) ** 2 <= r2)
                             for s in self.satellites.values())
            points.append({"id": f"P{idx}", "x_m": 0.0, "y_m": float(y), "covered": is_covered})
        point_cov = sum(1 for p in points if p["covered"]) / len(points)
        individual: dict[str, dict[str, Any]] = {}
        sensor_sats = [s for s in self.satellites.values() if s.is_operational and s.effective_capabilities & {"observation_highres", "observation_wide"}]
        sensor_ids = {s.satellite_id for s in sensor_sats}
        for sat in self.satellites.values():
            if sat.satellite_id not in sensor_ids:
                individual[sat.satellite_id] = {"active_sensor": False, "unique_cells": 0, "unique_contribution_pct": 0.0}
                continue
            others = {sid: other for sid, other in self.satellites.items() if sid != sat.satellite_id}
            others_mask = self.coverage.coverage_mask(others)
            own_mask = ((x_grid - float(sat.state[0])) ** 2 + (y_grid - float(sat.state[1])) ** 2) <= r2
            unique_cells = int(np.logical_and(own_mask, np.logical_not(others_mask)).sum())
            individual[sat.satellite_id] = {"active_sensor": True, "unique_cells": unique_cells,
                "unique_contribution_pct": unique_cells / self.coverage.total_cells * 100.0}
        graph = self._communication_graph()
        return {
            "time_s": cov.time_s, "coverage_pct": 100.0 * cov.coverage_fraction,
            "covered_cells": cov.covered_cells, "total_cells": cov.total_cells,
            "uncovered_cells": cov.total_cells - cov.covered_cells,
            "active_sensors": cov.active_sensors,
            "sensor_radius_m": float(self.config["sensor_radius_m"]),
            "area_coverage_pct": 100.0 * cov.coverage_fraction,
            "grid_coverage_pct": 100.0 * cov.coverage_fraction,
            "sensor_footprint_coverage_pct": 100.0 * cov.coverage_fraction,
            "observation_point_coverage_pct": 100.0 * point_cov,
            "observation_points": points, "individual": individual,
            "mask": mask.astype(np.uint8).tolist(),
            "x_axis_m": self.coverage.x.astype(float).tolist(),
            "y_axis_m": self.coverage.y.astype(float).tolist(),
            "communication_coverage_pct": 100.0 * graph["connected_fraction"],
        }

    def _current_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        events.extend({**e, "category": "fault"} for e in self.engine.event_log)
        events.extend({**e, "category": "communication"} for e in self.engine.network.events)
        events.extend({**e, "category": "assignment"} for e in self.controller.assignment_log)
        events.sort(key=lambda e: float(e.get("time_s", 0.0)))
        return events[-300:][::-1]

    def state(self, include_grid: bool = False) -> dict[str, Any]:
        with self.lock:
            now = float(self.engine.time_s)
            active_ids = list(self.engine.active_ids)
            failed_ids = [sid for sid, sat in self.satellites.items() if not sat.is_operational]
            graph = self._communication_graph()
            cov = self.coverage.evaluate(now, self.satellites)
            coverage_details = self._coverage_details()
            coverage_pct = 100.0 * cov.coverage_fraction
            baseline_pct = 100.0 * self.initial_cov.coverage_fraction
            target_pct = float(self.config["coverage_target_pct"])
            # The target is a user-configured absolute percentage, not a fixed result.
            critical_roles_ok = set(self.controller.assignment.values()) >= set(CRITICAL_ROLES)
            failed_excluded = all(sid not in self.controller.assignment for sid in failed_ids)
            min_distance = None
            pair_distances: list[dict[str, Any]] = []
            for i, a in enumerate(active_ids):
                for b in active_ids[i + 1:]:
                    d = float(np.linalg.norm(self.satellites[a].state[:2] - self.satellites[b].state[:2]))
                    pair_distances.append({"a": a, "b": b, "distance_m": d,
                        "safe": d >= float(self.config["safe_distance_m"])})
                    min_distance = d if min_distance is None else min(min_distance, d)
            formation_error = self._formation_error_rms()
            formation_ok = formation_error <= 1000.0
            coverage_ok = coverage_pct >= target_pct
            comm_ok = not active_ids or graph["connected_fraction"] >= 0.999
            energy_draw_pct_points = sum(self.initial_energy_percent.values()) - sum(float(s.energy_percent) for s in self.satellites.values())
            enough_spacecraft = len(active_ids) >= 4
            recovery_checks = [
                {"name": "Failed satellites excluded from active roles", "passed": failed_excluded,
                 "detail": "Confirmed bus failures must not hold active roles."},
                {"name": "Four or more operational satellites", "passed": enough_spacecraft,
                 "detail": f"{len(active_ids)} operational out of 6; four are required by this mission policy."},
                {"name": "Mission-critical roles restored", "passed": critical_roles_ok and not self.controller.degraded,
                 "detail": "Coordinator, two observation roles, and relay must be capability-feasible."},
                {"name": "Communication graph connected", "passed": comm_ok,
                 "detail": f"Largest connected component contains {100*graph['connected_fraction']:.1f}% of active satellites."},
                {"name": "Minimum separation maintained", "passed": min_distance is None or min_distance >= float(self.config["safe_distance_m"]),
                 "detail": "Heuristic monitoring only; this is not a collision-avoidance guarantee."},
                {"name": "Coverage target achieved", "passed": coverage_ok,
                 "detail": f"Current coverage {coverage_pct:.2f}% versus configured target {target_pct:.2f}%."},
                {"name": "Formation converged", "passed": formation_ok,
                 "detail": f"RMS formation error {formation_error:.1f} m; convergence threshold 1000 m."},
            ]
            recovery_passed = all(c["passed"] for c in recovery_checks)
            failure_times = [float(e.get("time_s", 0.0)) for e in self.engine.event_log
                             if str(e.get("kind", "")) in {k.value for k in FaultKind}]
            last_failure_time_s = max(failure_times) if failure_times else None
            recovery_time_s = now - last_failure_time_s if recovery_passed and failed_ids and last_failure_time_s is not None else None
            if len(active_ids) < 4 or self.controller.degraded:
                mission_status = "DEGRADED"
            elif recovery_passed:
                mission_status = "RECOVERED"
            elif not comm_ok:
                mission_status = "PARTIALLY CONNECTED"
            elif not coverage_ok:
                mission_status = "COVERAGE BELOW TARGET"
            else:
                mission_status = "RECONFIGURING"
            suspicions = self.engine.network.suspicions
            sat_rows = []
            for sid, sat in self.satellites.items():
                x, y, vx, vy = map(float, sat.state)
                distances = [float(np.linalg.norm(sat.state[:2] - other.state[:2])) for oid, other in self.satellites.items()
                             if oid != sid and other.is_operational and sat.is_operational]
                nearest = min(distances) if distances else None
                suspected_by = sorted(observer for observer, targets in suspicions.items() if sid in targets)
                missed_periods = max((self.engine.network._consecutive_misses.get((observer, sid), 0)
                                      for observer in self.engine.network.satellite_ids if observer != sid
                                      and self.satellites[observer].is_operational), default=0)
                failure_confidence_pct = 100.0 if not sat.is_operational or suspected_by else min(100.0,
                    100.0 * missed_periods / max(1, int(self.config["missed_heartbeat_threshold"])))
                if not sat.is_operational:
                    operational_status = "FAILED — confirmed by model"
                elif suspected_by:
                    operational_status = "UNREACHABLE SUSPECT — hardware still operational"
                elif sid in self.isolated_ids:
                    operational_status = "TEMPORARY COMMUNICATION OUTAGE — hardware still operational"
                elif sat.health_state is HealthState.DEGRADED:
                    operational_status = "DEGRADED"
                else:
                    operational_status = "NOMINAL"
                speed = math.hypot(vx, vy)
                anomalies = []
                if speed > 25.0:
                    anomalies.append("velocity threshold")
                if sat.energy_percent < 20.0:
                    anomalies.append("low modeled energy")
                if sat.remaining_dv_mps < 1.0 and sat.is_operational:
                    anomalies.append("low delta-v budget")
                control = self.engine.last_control_mps2.get(sid, (0.0, 0.0))
                sat_rows.append({
                    "satellite_id": sid, "status": operational_status,
                    "health_state": sat.health_state.value, "role": self.controller.assignment.get(sid, sat.current_role),
                    "position_x_m": x, "position_y_m": y, "velocity_x_mps": vx, "velocity_y_mps": vy,
                    "speed_mps": speed, "control_x_mps2": control[0], "control_y_mps2": control[1],
                    "energy_percent": float(sat.energy_percent), "remaining_dv_mps": float(sat.remaining_dv_mps),
                    "initial_delta_v_mps": self.initial_delta_v_mps[sid],
                    "capabilities": sorted(sat.effective_capabilities),
                    "installed_capabilities": sorted(sat.capabilities),
                    "failed_capabilities": sorted(sat.failed_capabilities),
                    "maneuver_capable": sat.is_operational and sat.remaining_dv_mps > 0 and sat.effective_max_acceleration_mps2 > 0,
                    "is_operational": sat.is_operational, "is_isolated": sid in self.isolated_ids,
                    "suspected_by": suspected_by, "missed_heartbeat_periods": missed_periods,
                    "failure_confidence_pct": failure_confidence_pct, "nearest_neighbor_distance_m": nearest,
                    "formation_error_m": self._formation_error_by_satellite().get(sid),
                    "coverage_contribution": coverage_details["individual"].get(sid, {}),
                    "anomalies": anomalies, "failure_reason": sat.failure_reason,
                })
            roles_rows = []
            for role in ROLES:
                assigned_sid = next((sid for sid, r in self.controller.assignment.items() if r == role.name), None)
                sat = self.satellites.get(assigned_sid) if assigned_sid else None
                roles_rows.append({"role": role.name, "priority": role.priority,
                    "critical": role.name in CRITICAL_ROLES,
                    "required_any": sorted(role.required_any), "target_y_m": role.target_y_m,
                    "assigned_satellite": assigned_sid,
                    "feasible": bool(sat and sat.is_operational and sat.effective_capabilities & role.required_any)})
            assignment_matrix = []
            assignment_contributions = {sid: float(v.get("unique_contribution_pct", 0.0))
                                        for sid, v in coverage_details["individual"].items()}
            active_sat_objects = [s for s in self.satellites.values() if s.is_operational]
            for role in ROLES:
                for sid, sat in self.satellites.items():
                    terms = self.controller.assigner.score_candidate(
                        sat, role, self.controller.assignment, active_sat_objects,
                        float(self.config["communication_range_m"]),
                        float(self.config["packet_loss_probability"]), assignment_contributions,
                        isolated_ids=self.isolated_ids,
                    )
                    assignment_matrix.append({"role": role.name, "satellite_id": sid, **terms})
            histories = self.engine.history
            stride = max(1, math.ceil(len(histories) / 180))
            trajectory = []
            for snap in histories[::stride]:
                trajectory.append({"time_s": snap.time_s,
                    "positions": {sid: {"x": float(state[0]), "y": float(state[1])} for sid, state in snap.states_m.items()}})
            if histories and (not trajectory or trajectory[-1]["time_s"] != histories[-1].time_s):
                snap = histories[-1]
                trajectory.append({"time_s": snap.time_s,
                    "positions": {sid: {"x": float(state[0]), "y": float(state[1])} for sid, state in snap.states_m.items()}})
            data = {
                "version": _pkg_version,
                "simulation": {"time_s": now, "duration_s": float(self.config["duration_s"]),
                    "dt_s": float(self.config["dt_s"]), "speed": float(self.config["speed"]),
                    "running": self.running, "finished": self.finished, "status_message": self.status_message,
                    "progress_pct": min(100.0, 100.0 * now / float(self.config["duration_s"]))},
                "mission": {"name": "Six-CubeSat Self-Healing Formation Control",
                    "status": mission_status, "initial_satellites": 6, "active_satellites": len(active_ids),
                    "failed_satellites": len(failed_ids), "coverage_pct": coverage_pct,
                    "initial_coverage_pct": baseline_pct, "coverage_delta_pct_points": coverage_pct - baseline_pct,
                    "coverage_target_pct": target_pct, "connected_fraction": graph["connected_fraction"],
                    "formation_error_rms_m": formation_error, "min_pair_distance_m": min_distance,
                    "recovery_passed": recovery_passed, "recovery_checks": recovery_checks,
                    "assignment_feasible": not self.controller.degraded,
                    "role_assignment": dict(self.controller.assignment),
                    "critical_roles_restored": critical_roles_ok,
                    "heartbeat_sent": self.engine.network.sent_count,
                    "heartbeat_delivered": self.engine.network.delivered_count,
                    "heartbeat_dropped": self.engine.network.dropped_count,
                    "heartbeat_delivery_pct": 100.0 * self.engine.network.delivered_count / max(1, self.engine.network.sent_count),
                    "suspected_links": sum(len(x) for x in suspicions.values()),
                    "energy_draw_pct_points": energy_draw_pct_points,
                    "energy_remaining_pct_points": sum(float(s.energy_percent) for s in self.satellites.values()),
                    "last_failure_time_s": last_failure_time_s,
                    "time_since_last_failure_s": now - last_failure_time_s if last_failure_time_s is not None else None,
                    "recovery_time_s": recovery_time_s,
                    "failed_ids": failed_ids, "active_ids": active_ids},
                "config": dict(self.config),
                "satellites": sat_rows,
                "roles": roles_rows,
                "assignment_matrix": assignment_matrix,
                "communication": {**graph, "isolated_ids": sorted(self.isolated_ids),
                    "suspicions": {sid: sorted(targets) for sid, targets in suspicions.items()},
                    "heartbeat_period_s": float(self.config["heartbeat_period_s"]),
                    "missed_threshold": int(self.config["missed_heartbeat_threshold"]),
                    "packet_loss_probability": float(self.config["packet_loss_probability"])},
                "formation": {"error_by_satellite": self._formation_error_by_satellite(),
                    "pair_distances": pair_distances,
                    "safe_distance_m": float(self.config["safe_distance_m"]),
                    "target_positions": {sid: {"x": xy[0], "y": xy[1]}
                        for sid, xy in self.controller.formation_targets.items()},
                    "formation_sides": len(self.controller.formation_targets),
                    "formation_shape": {6: "hexagon", 5: "pentagon", 4: "quadrilateral", 3: "triangle", 2: "line", 1: "single"}.get(len(self.controller.formation_targets), "unavailable"),
                    "trajectory": trajectory},
                "coverage": {"coverage_pct": coverage_pct, "initial_coverage_pct": baseline_pct,
                    "target_pct": target_pct, "covered_cells": cov.covered_cells,
                    "total_cells": cov.total_cells, "uncovered_cells": cov.total_cells - cov.covered_cells,
                    "active_sensors": cov.active_sensors, "initial_active_sensors": self.initial_cov.active_sensors,
                    "sensor_radius_m": float(self.config["sensor_radius_m"]),
                    "models": self._coverage_model_summary(coverage_details),
                    "individual": coverage_details["individual"],
                    "observation_points": coverage_details["observation_points"]},
                "metrics": self.metric_rows[-600:],
                "events": self._current_events(),
                "fault_schedule": self._scheduled_ids,
                "initial_states": self.initial_states,
                "model_limits": [
                    "Planar LVLH/Hill-Clohessy-Wiltshire dynamics integrated using RK4.",
                    "Coverage is computed over an abstract local-frame observation grid, not Earth geography.",
                    "Communication range/quality and normalized energy draw are configurable software abstractions.",
                    "Separation response is heuristic; recovery checks are diagnostic, not a formal collision-safety proof.",
                    "This is not a flight-certified or high-fidelity 3-D orbital simulator."
                ],
            }
            if include_grid:
                data["coverage_grid"] = coverage_details
            return _json_safe(data)

    def _coverage_model_summary(self, details: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        details = details or self._coverage_details()
        return [
            {"name": "Grid-cell coverage", "value_pct": details["grid_coverage_pct"],
             "description": "Fraction of cells in the configured LVLH observation region covered by at least one active sensor."},
            {"name": "Sensor-footprint coverage", "value_pct": details["sensor_footprint_coverage_pct"],
             "description": f"Union of active sensor footprints; sensor radius {details['sensor_radius_m']:.0f} m."},
            {"name": "Communication coverage", "value_pct": details["communication_coverage_pct"],
             "description": "Operational satellites in the largest connected component of the range-limited ISL graph."},
            {"name": "Observation-point coverage", "value_pct": details["observation_point_coverage_pct"],
             "description": "Fraction of seven defined along-track observation points inside any active sensor footprint."},
            {"name": "Area coverage", "value_pct": details["area_coverage_pct"],
             "description": "Grid approximation of the proportion of the target region currently covered."},
        ]

    def coverage_grid(self) -> dict[str, Any]:
        with self.lock:
            return _json_safe(self._coverage_details())

    def export_csv(self) -> str:
        with self.lock:
            stream = io.StringIO()
            fields = list(self.metric_rows[0].keys()) if self.metric_rows else ["time_s"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader(); writer.writerows(self.metric_rows)
            return stream.getvalue()

    def export_summary(self) -> dict[str, Any]:
        d = self.state(include_grid=False)
        return {k: d[k] for k in ["simulation", "mission", "config", "satellites", "roles",
                  "communication", "formation", "coverage", "metrics", "events", "fault_schedule", "model_limits"]}


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "CubeSatDashboard/1.0"

    @property
    def session(self) -> DashboardSession:
        return self.server.dashboard_session  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep console output concise; HTTP errors remain visible.
        if args and str(args[1:2]).startswith("('4"):
            super().log_message(fmt, *args)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200,
                    extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for k, v in extra_headers.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(_json_safe(data), separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000: raise ValueError("Request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict): raise ValueError("Request body must be a JSON object")
        return data

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            file = WEB_ROOT / "index.html"
            self._send_bytes(file.read_bytes(), "text/html; charset=utf-8")
        elif path == "/style.css":
            self._send_bytes((WEB_ROOT / "style.css").read_bytes(), "text/css; charset=utf-8")
        elif path == "/app.js":
            self._send_bytes((WEB_ROOT / "app.js").read_bytes(), "text/javascript; charset=utf-8")
        elif path == "/api/state":
            self._json(self.session.state(include_grid=False))
        elif path == "/api/coverage-grid":
            self._json(self.session.coverage_grid())
        elif path == "/api/export/summary.json":
            body = json.dumps(self.session.export_summary(), indent=2, allow_nan=False).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8",
                extra_headers={"Content-Disposition": "attachment; filename=cubesat-swarm-summary.json"})
        elif path == "/api/export/metrics.csv":
            body = self.session.export_csv().encode("utf-8")
            self._send_bytes(body, "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": "attachment; filename=cubesat-swarm-metrics.csv"})
        else:
            self._json({"error": "Not found"}, 404)

    def do_HEAD(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            file = WEB_ROOT / "index.html"; content_type = "text/html; charset=utf-8"
        elif path == "/style.css":
            file = WEB_ROOT / "style.css"; content_type = "text/css; charset=utf-8"
        elif path == "/app.js":
            file = WEB_ROOT / "app.js"; content_type = "text/javascript; charset=utf-8"
        else:
            self.send_error(404); return
        body = file.read_bytes()
        self.send_response(200); self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            if path == "/api/start":
                self.session.start(); result = {"ok": True}
            elif path == "/api/pause":
                self.session.pause(); result = {"ok": True}
            elif path == "/api/resume":
                self.session.start(); result = {"ok": True}
            elif path == "/api/reset":
                config = copy.deepcopy(DEFAULT_CONFIG); config.update(copy.deepcopy(data.get("config", {})))
                DashboardSession._validate_config(config)
                faults = data.get("fault_schedule", DEFAULT_FAULTS)
                if not isinstance(faults, list): raise ValueError("fault_schedule must be a list")
                self.session.config = config
                self.session.fault_schedule = faults
                self.session.reset(keep_config=True)
                result = {"ok": True}
            elif path == "/api/tick":
                seconds = float(data.get("seconds", 0.25 * float(self.session.config["speed"])))
                if not math.isfinite(seconds) or seconds < 0 or seconds > 100:
                    raise ValueError("Tick duration must be between 0 and 100 simulated seconds")
                self.session.advance(seconds)
                result = {"ok": True}
            elif path == "/api/step":
                self.session.advance(float(data.get("seconds", self.session.config["dt_s"])), force=True)
                result = {"ok": True}
            elif path == "/api/speed":
                speed = float(data.get("speed", self.session.config["speed"]))
                if not 0.1 <= speed <= 50: raise ValueError("Speed must be between 0.1 and 50")
                self.session.config["speed"] = speed
                result = {"ok": True, "speed": speed}
            elif path == "/api/fault":
                result = {"ok": True, "event": self.session.inject_fault_now(data)}
            elif path == "/api/random-failures":
                count = int(data.get("count", 1))
                result = {"ok": True, "events": self.session.inject_random_failures(count)}
            elif path == "/api/schedule-fault":
                offset = float(data.get("offset_s", 0.0))
                if offset < 0: raise ValueError("Schedule offset cannot be negative")
                event = {"time_s": self.session.engine.time_s + offset,
                         "satellite_id": data.get("satellite_id"), "kind": data.get("kind", "bus_failure"),
                         "value": data.get("value")}
                result = {"ok": True, "event": self.session.schedule_fault(event)}
            elif path == "/api/comm":
                self.session.set_communication_isolation(str(data.get("satellite_id", "")), bool(data.get("isolated", True)))
                result = {"ok": True}
            else:
                self._json({"error": "Not found"}, 404); return
            self._json(result)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # defensive API boundary; details help local developers debug.
            self._json({"error": f"Internal dashboard error: {exc}"}, 500)


def serve(host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = True,
          config: dict[str, Any] | None = None) -> None:
    session = DashboardSession(config)
    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    server.daemon_threads = True
    server.dashboard_session = session  # type: ignore[attr-defined]
    address = f"http://{host}:{server.server_port}/"
    print("\nCUBESAT SWARM DASHBOARD")
    print(f"Dashboard URL: {address}")
    print("Press Ctrl+C to stop the local server. The simulator does not access spacecraft or external systems.\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nShutting down dashboard…")
    finally:
        session.pause()
        server.server_close()
