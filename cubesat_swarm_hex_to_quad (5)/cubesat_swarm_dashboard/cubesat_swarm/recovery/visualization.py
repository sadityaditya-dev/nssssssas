"""Live dashboard and post-run plot helpers; matplotlib is an optional dependency."""
from __future__ import annotations
from pathlib import Path
import numpy as np

class LiveDashboard:
    def __init__(self, coverage, satellites):
        import matplotlib.pyplot as plt
        self.plt, self.coverage, self.satellites = plt, coverage, satellites
        plt.ion()
        self.fig, (self.ax_orbit, self.ax_cov) = plt.subplots(1, 2, figsize=(13, 5))
        self.fig.suptitle("CubeSat Swarm Self-Healing — live simulated state")
        self.ax_orbit.set_xlabel("Cross-track x (m)"); self.ax_orbit.set_ylabel("Along-track y (m)")
        self.ax_orbit.grid(True, alpha=.25)
        self.ax_cov.set_xlabel("Simulation time (s)"); self.ax_cov.set_ylabel("Coverage (%)")
        self.ax_cov.set_ylim(0, 100); self.ax_cov.grid(True, alpha=.25)
        self.coverage_line, = self.ax_cov.plot([], [], label="grid coverage")
        self.ax_cov.legend(loc="best")
        self.scatter = None

    def update(self, time_s, metric, rows):
        self.ax_orbit.clear(); self.ax_orbit.set_title(f"t = {time_s:.1f} s | active = {sum(s.is_operational for s in self.satellites.values())}/6")
        self.ax_orbit.set_xlabel("Cross-track x (m)"); self.ax_orbit.set_ylabel("Along-track y (m)"); self.ax_orbit.grid(True, alpha=.25)
        for sid, sat in self.satellites.items():
            x, y = sat.state[:2]
            marker = "o" if sat.is_operational else "x"
            self.ax_orbit.scatter([x], [y], marker=marker, s=65)
            self.ax_orbit.annotate(f"{sid}\n{sat.current_role or '-'}", (x, y), xytext=(4, 4), textcoords="offset points", fontsize=7)
        self.ax_orbit.set_xlim(-12_000, 12_000); self.ax_orbit.set_ylim(-18_000, 18_000)
        ts = [float(r["time_s"]) for r in rows]; cov = [100*float(r["coverage_fraction"]) for r in rows]
        self.ax_cov.clear(); self.ax_cov.plot(ts, cov, label="grid coverage"); self.ax_cov.set_ylim(0, 100)
        self.ax_cov.set_xlabel("Simulation time (s)"); self.ax_cov.set_ylabel("Coverage (%)"); self.ax_cov.grid(True, alpha=.25); self.ax_cov.legend()
        self.fig.canvas.draw_idle(); self.plt.pause(0.001)

    def close(self):
        self.plt.ioff(); self.plt.show(block=False)


def save_summary_plot(engine, metric_rows, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    for sid, sat in engine.satellites.items():
        xs = [snap.states_m[sid][0] for snap in engine.history]
        ys = [snap.states_m[sid][1] for snap in engine.history]
        ax1.plot(xs, ys, label=f"{sid} ({'active' if sat.is_operational else 'failed'})", linewidth=1.4)
        ax1.scatter(xs[0], ys[0], s=20)
    ax1.set_title("LVLH relative trajectories"); ax1.set_xlabel("x (m)"); ax1.set_ylabel("y (m)"); ax1.grid(True, alpha=.25); ax1.legend(fontsize=7)
    ts = [float(r["time_s"]) for r in metric_rows]
    cov = [100*float(r["coverage_fraction"]) for r in metric_rows]
    active = [int(r["active_satellites"]) for r in metric_rows]
    ax2.plot(ts, cov, label="covered grid (%)")
    ax2.set_xlabel("Simulation time (s)"); ax2.set_ylabel("Coverage (%)"); ax2.set_ylim(0, 100); ax2.grid(True, alpha=.25)
    ax2b = ax2.twinx(); ax2b.step(ts, active, where="post", label="active satellites", linestyle="--")
    ax2b.set_ylabel("Active satellites"); ax2b.set_ylim(0, max(6, max(active)+1))
    lines, labels = ax2.get_legend_handles_labels(); lines2, labels2 = ax2b.get_legend_handles_labels(); ax2.legend(lines+lines2, labels+labels2, loc="best")
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)
