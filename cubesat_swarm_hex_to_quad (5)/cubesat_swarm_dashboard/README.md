# ORBITGUARD — Six-CubeSat Self-Healing Swarm Dashboard

A local, interactive research simulation dashboard for the six-CubeSat partial-failure problem. The browser UI is connected to the Python simulation engine; the charts, telemetry, events, role maps, coverage, and recovery checks update from the current model state. This is not a static animation or a flight-certified spacecraft simulator.

## Run on Windows (Command Prompt)

Extract the ZIP, open **CMD** in the extracted project folder, and run each command separately:

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e "[test]"
python -m pytest
python dashboard_app.py
```

The dashboard opens at `http://127.0.0.1:8765/`. If the browser does not open automatically, copy that address into a browser. Use **Ctrl+C** in CMD to stop the local server. The dashboard has no spacecraft command interface and does not need a network connection for its simulation logic.

If you need to suppress automatic browser opening:

```bat
python dashboard_app.py --no-browser
```

## Dashboard sections

- **Mission overview:** operational count, computed region coverage, inter-satellite connectivity, formation RMS error, heartbeat delivery, event log, trajectories, and workflow status.
- **Satellite fleet:** each spacecraft's position, velocity, role, energy proxy, delta-v budget, capabilities, nearest-neighbour distance, anomalies, and unique sensor contribution.
- **Failure monitor:** inject bus, sensor-payload, or thruster faults immediately or at a future simulation time; inject one or two seeded random bus failures; review heartbeat suspicions and confirmed model faults separately.
- **Autonomy & roles:** role assignments, capability feasibility, role history, and an auditable score table for distance, role-switch penalty, energy, delta-v, communications, manoeuvring authority, and marginal coverage.
- **Formation control:** target locations, trajectories, formation error, control input, pairwise separation, and movement progress.
- **Coverage models:** view the grid, sensor footprints, observation-point coverage, communication connectivity, total covered/uncovered cells, and each active sensor's unique contribution.
- **Communications:** live range-limited ISL graph, heartbeat counts, packet-loss simulation, temporary link isolation, and restoration. Temporary communication loss creates suspicions; it does not automatically declare the bus failed.
- **Recovery verification:** explicit pass/fail checks for active roles, critical capabilities, connectivity, separation threshold, configured coverage target, and formation convergence.
- **Mission settings:** change simulation duration, integration step, playback speed, heartbeat parameters, sensor radius, communication range, safe-distance threshold, coverage target, packet-loss probability, random seed, and each satellite's initial state, energy, delta-v, acceleration limit, and capabilities.

The header **SIM SPEED** control changes speed while running. Start, pause, resume, and reset operate on the same simulation state. Current summary (JSON) and time-series metrics (CSV) are downloadable from the dashboard.

## Default end-to-end scenario

- Six spacecraft start with defined initial positions, velocities, capabilities, and roles.
- `S2` receives a bus-failure fault at simulated `t = 20 s`.
- `S5` receives a bus-failure fault at simulated `t = 40 s`.
- The role manager recomputes a capability-feasible assignment from the four survivors or reports infeasibility.
- The dashboard computes coverage from the current satellite positions and configured circular sensor footprints, then checks recovery criteria.

To run the default scenario from the command line and generate CSV, JSON, and PNG outputs:

```bat
python run_simulation.py --duration 120
```

Custom CLI faults can be added with repeated `--fail TIME_S SAT_ID` arguments:

```bat
python run_simulation.py --duration 90 --fail 15 S2 --fail 35 S5 --fail 55 S3
```

## Test and validation

Run the full unit, integration, end-to-end and local HTTP API suite:

```bat
python -m pytest
```

The current suite includes dynamics and integrator tests, fault-event timing, heartbeat loss and recovery, capability-feasible role assignment, grid coverage, dashboard configuration validation, live API responses, and the default two-fault 120-second scenario. The suite verifies the software model and configured scenarios; it does not verify orbital accuracy against flight data or prove physical spacecraft safety.

## Numerical model and limitations

- **Dynamics:** planar LVLH relative motion using linearized Clohessy–Wiltshire/Hill equations and an RK4 integration step. It is not full 3-D orbit propagation with perturbations.
- **Coverage:** abstract local-frame observation grid, not Earth-surface or geographic coverage. The grid/area and sensor-footprint percentages are related measurements derived from the same footprint union; observation-point coverage is separately evaluated.
- **Communication:** range-limited software ISL graph, simulated heartbeat packets and independent packet loss; not RF propagation, antenna pattern, link margin, or a radio protocol implementation.
- **Energy:** normalized software proxy that decreases with simulated control effort; not a battery, solar array, eclipse, thermal or power-system model.
- **Role assignment:** exact enumeration over feasible assignments for a six-spacecraft swarm. It accounts for distance, prior role, energy, delta-v, communications, manoeuvring authority, and marginal sensor contribution. Its weights are heuristic software policy values, not trained or flight-validated constants.
- **Control and safety:** bounded feedback and a heuristic close-approach response; safe-distance checks are diagnostics, not a formal collision-avoidance guarantee.
- **Fault confirmation:** injected bus faults are known model events. A heartbeat timeout indicates an unreachable/suspected satellite; it does not, by itself, distinguish a failed spacecraft from a temporary link outage. Reassignments are based on modeled operational/capability state, not on a real spacecraft diagnostic bus.

## Project structure

```text
cubesat_swarm/
  dynamics/hcw.py              HCW equations and RK4 integrator
  simulation/                  event queue, integration engine, heartbeat network
  recovery/assignment.py       capability-constrained assignment and score terms
  recovery/controller.py       role manager and bounded feedback control
  recovery/coverage.py         grid/footprint coverage evaluator
  dashboard/server.py          local HTTP server and simulation-state API
dashboard/
  index.html                   interactive dashboard screens
  app.js                       live API binding, rendering and controls
  style.css                    responsive dashboard styling
dashboard_app.py               browser dashboard launcher
run_simulation.py              command-line runner and output export
tests/                         unit, integration, end-to-end and API tests
```

## Formation animation: six-spacecraft hexagon to four-spacecraft recovery polygon

The default LVLH starting geometry is now a regular six-vertex hexagon. The formation
plot draws a live boundary around operational spacecraft and a separate dashed target
boundary. The dashboard uses current telemetry for both boundaries; these are not
static decorative shapes.

With the default scenario, S2 fails at 20 s and S5 fails at 40 s. When each bus failure
is applied, the formation controller recalculates target vertices from the surviving
membership. After the second failure, the four survivors (S1, S3, S4, S6) receive four
evenly spaced target vertices, producing a quadrilateral/square-like formation. The
HCW feedback controller then steers each operational spacecraft toward its target
while the dashboard continuously redraws the live polygon and motion trails.

The target polygon is a planning reference, not a collision-safety guarantee. The
simulation remains a planar LVLH research model; inspect minimum-separation telemetry
and the model limitations before interpreting the animation as flight guidance.
