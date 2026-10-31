# Phase 5 Engineering Decisions

1. **Coordinate frame:** planar Hill/LVLH frame; radial x and along-track y.
2. **Units:** SI throughout the engine. Convert all configuration values at the boundary, not in dynamics functions.
3. **State vector:** `[x, y, vx, vy]`.
4. **Propagation:** classical RK4 with constant acceleration over each substep.
5. **Timing:** the nominal step is fixed. When an event falls inside a step, split propagation at the event timestamp; continue through the remainder so fault timing is exact.
6. **State ownership:** the simulation engine advances truth state; the dashboard will eventually consume snapshots and will not own physics.
7. **Failure model:** bus failures make a craft non-operational and remove its commands but preserve its physical state for unpowered propagation; payload loss and thruster derating do not imply bus failure.
8. **Control interface:** the engine accepts an optional control-law callback to isolate dynamics from controller implementation. It defaults to zero control.
9. **Resource approximation:** accumulated `norm(u) * dt` is a simplified delta-v expenditure proxy; no mass flow or detailed battery dynamics are claimed.
10. **Scope boundary:** this phase is only the physical simulation and event-scheduling foundation. Autonomous diagnosis, communications, assignment, coverage recovery and dashboard integration are future work.

## Source-derived vs proposed

- The supplied CubeSat swarm documents recommend planar HCW relative dynamics, heartbeat-based fault detection, a Hungarian assignment solver, and Voronoi/grid coverage.
- This phase implements only the HCW foundation, state models and event scheduling.
- RK4, the SI-unit convention, the callback interface, exact-time event splitting and the initial software capability profile are engineering implementation choices for this repository. They are not represented as results from the supplied papers.
