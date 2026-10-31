"""Full self-healing CubeSat swarm simulation runner (research/software prototype)."""
from __future__ import annotations
import argparse, csv, json, math, time
from pathlib import Path
import numpy as np
from cubesat_swarm.models import FaultKind
from cubesat_swarm.simulation.engine import SimulationEngine
from cubesat_swarm.recovery import SelfHealingController, CoverageEvaluator
from scenario import make_initial_satellites, MEAN_MOTION_RAD_S


def build_engine(dt_s: float, packet_loss: float, seed: int):
    sats = make_initial_satellites()
    sat_map = {s.satellite_id: s for s in sats}
    assignment_coverage = CoverageEvaluator()
    controller = SelfHealingController(sat_map, MEAN_MOTION_RAD_S,
        coverage_evaluator=assignment_coverage, packet_loss_probability=packet_loss)
    engine = SimulationEngine(sats, MEAN_MOTION_RAD_S, dt_s=dt_s,
        control_law=controller.control, packet_loss_probability=packet_loss, communication_seed=seed)
    return engine, controller


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--duration", type=float, default=120.0, help="simulation duration in seconds")
    p.add_argument("--dt", type=float, default=0.2, help="integration step in seconds")
    p.add_argument("--fail", nargs=2, action="append", metavar=("TIME_S", "SAT_ID"), default=None,
                   help="inject a bus failure; repeatable (default: S2 at 20s and S5 at 40s)")
    p.add_argument("--packet-loss", type=float, default=0.0, help="per-link heartbeat packet loss in [0,1)")
    p.add_argument("--seed", type=int, default=7, help="random seed for communication packet loss")
    p.add_argument("--live", action="store_true", help="show an updating simulation dashboard")
    p.add_argument("--realtime", action="store_true", help="pace the simulation in wall-clock time")
    p.add_argument("--speed", type=float, default=5.0, help="simulation seconds per wall-clock second with --realtime")
    p.add_argument("--output", default="output", help="directory for CSV, JSON and PNG outputs")
    args = p.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0: p.error("--duration must be > 0")
    if not math.isfinite(args.dt) or args.dt <= 0: p.error("--dt must be > 0")
    if not 0 <= args.packet_loss < 1: p.error("--packet-loss must be in [0,1)")
    if args.speed <= 0: p.error("--speed must be > 0")

    engine, controller = build_engine(args.dt, args.packet_loss, args.seed)
    faults = args.fail if args.fail is not None else [["20", "S2"], ["40", "S5"]]
    for t_text, sid in faults:
        engine.schedule_fault(float(t_text), sid, FaultKind.BUS_FAILURE)

    coverage = CoverageEvaluator()
    metric_rows: list[dict[str, object]] = []
    dashboard = None
    if args.live:
        from cubesat_swarm.recovery.visualization import LiveDashboard
        dashboard = LiveDashboard(coverage, engine.satellites)
    last_metric_t = -1.0
    wall_start = time.perf_counter()
    while engine.time_s < args.duration - 1e-10:
        before = engine.time_s
        engine.step(min(args.dt, args.duration - engine.time_s))
        # Sample metrics once per simulated second and at final time.
        if engine.time_s - last_metric_t >= 1.0 - 1e-9 or engine.time_s >= args.duration - 1e-9:
            c = coverage.evaluate(engine.time_s, engine.satellites)
            metric_rows.append({"time_s": c.time_s, "coverage_fraction": c.coverage_fraction,
                "covered_cells": c.covered_cells, "total_cells": c.total_cells,
                "active_sensors": c.active_sensors, "active_satellites": len(engine.active_ids),
                "assignment_feasible": not controller.degraded,
                "roles": json.dumps(controller.assignment, sort_keys=True)})
            last_metric_t = engine.time_s
            if dashboard is not None: dashboard.update(engine.time_s, c, metric_rows)
        if args.realtime:
            time.sleep(max(0.0, (engine.time_s - before) / args.speed))

    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "metrics.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(metric_rows[0].keys()))
        writer.writeheader(); writer.writerows(metric_rows)
    final_cov = coverage.evaluate(engine.time_s, engine.satellites)
    summary = {
        "simulated_duration_s": engine.time_s, "integration_step_s": args.dt,
        "initial_satellites": len(engine.satellites), "operational_satellites": len(engine.active_ids),
        "failed_satellites": [sid for sid, s in engine.satellites.items() if not s.is_operational],
        "final_coverage_fraction": final_cov.coverage_fraction,
        "final_active_sensors": final_cov.active_sensors,
        "heartbeat_delivered": engine.network.delivered_count,
        "heartbeat_dropped": engine.network.dropped_count,
        "role_assignment": controller.assignment, "assignment_degraded": controller.degraded,
        "fault_events": engine.event_log, "assignment_events": controller.assignment_log,
        "communication_events": engine.network.events,
        "model_scope": "planar LVLH/Hill-Clohessy-Wiltshire software simulation; not flight-certified",
    }
    json_path = out / "summary.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    from cubesat_swarm.recovery.visualization import save_summary_plot
    plot_path = out / "simulation_summary.png"
    save_summary_plot(engine, metric_rows, plot_path)
    if dashboard is not None: dashboard.close()

    print("\n=== CUBESAT SWARM SIMULATION COMPLETE ===")
    print(f"Simulated time: {engine.time_s:.2f} s | integration step: {args.dt:g} s")
    print(f"Operational spacecraft: {len(engine.active_ids)}/{len(engine.satellites)}")
    print("Health: " + ", ".join(f"{sid}={s.health_state.value}" for sid, s in engine.satellites.items()))
    print("Role assignment: " + json.dumps(controller.assignment, sort_keys=True))
    print(f"Assignment feasible: {not controller.degraded}")
    print(f"Final abstract-strip coverage: {100*final_cov.coverage_fraction:.2f}% ({final_cov.covered_cells}/{final_cov.total_cells} grid cells)")
    print(f"Heartbeat packets delivered/dropped: {engine.network.delivered_count}/{engine.network.dropped_count}")
    print(f"Role assignment events: {len(controller.assignment_log)} | communication events: {len(engine.network.events)}")
    print(f"Outputs: {csv_path}, {json_path}, {plot_path}")
    print("Scope note: this is a research-grade planar simulation, not a validated orbital flight model.")

if __name__ == "__main__": main()
