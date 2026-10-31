"""Planar Hill-Clohessy-Wiltshire dynamics and fixed-input RK4 integration.

Units: position [m], velocity [m/s], mean motion [rad/s], acceleration [m/s^2],
time [s]. State order: [x, y, vx, vy].
"""

from __future__ import annotations

import numpy as np


class DynamicsError(ValueError):
    """Raised when dynamics inputs are invalid."""


def _validate_inputs(state: np.ndarray, mean_motion_rad_s: float, control_mps2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(state, dtype=float)
    u = np.asarray(control_mps2, dtype=float)
    if x.shape != (4,):
        raise DynamicsError(f"state must have shape (4,), got {x.shape}")
    if u.shape != (2,):
        raise DynamicsError(f"control_mps2 must have shape (2,), got {u.shape}")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(u)):
        raise DynamicsError("state and control must be finite")
    if not np.isfinite(mean_motion_rad_s) or mean_motion_rad_s < 0:
        raise DynamicsError("mean_motion_rad_s must be finite and >= 0")
    return x, u


def hcw_derivative(state: np.ndarray, control_mps2: np.ndarray, mean_motion_rad_s: float) -> np.ndarray:
    """Evaluate the planar HCW state derivative.

    x_ddot = 3*n^2*x + 2*n*y_dot + u_x
    y_ddot = -2*n*x_dot + u_y
    """
    x, u = _validate_inputs(state, mean_motion_rad_s, control_mps2)
    px, py, vx, vy = x
    n = float(mean_motion_rad_s)
    ux, uy = u
    return np.array(
        [vx, vy, 3.0 * n * n * px + 2.0 * n * vy + ux, -2.0 * n * vx + uy],
        dtype=float,
    )


def rk4_step(
    state: np.ndarray,
    control_mps2: np.ndarray,
    mean_motion_rad_s: float,
    dt_s: float,
) -> np.ndarray:
    """Propagate one state by ``dt_s`` using classical RK4 and constant control."""
    x, u = _validate_inputs(state, mean_motion_rad_s, control_mps2)
    if not np.isfinite(dt_s) or dt_s <= 0.0:
        raise DynamicsError("dt_s must be finite and > 0")

    dt = float(dt_s)
    k1 = hcw_derivative(x, u, mean_motion_rad_s)
    k2 = hcw_derivative(x + 0.5 * dt * k1, u, mean_motion_rad_s)
    k3 = hcw_derivative(x + 0.5 * dt * k2, u, mean_motion_rad_s)
    k4 = hcw_derivative(x + dt * k3, u, mean_motion_rad_s)
    result = x + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
    if not np.all(np.isfinite(result)):
        raise FloatingPointError("HCW integration produced a non-finite state")
    return result
