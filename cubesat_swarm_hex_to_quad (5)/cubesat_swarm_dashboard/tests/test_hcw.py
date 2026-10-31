import numpy as np
import pytest

from cubesat_swarm.dynamics.hcw import DynamicsError, hcw_derivative, rk4_step


def test_zero_state_and_zero_control_have_zero_derivative():
    np.testing.assert_allclose(hcw_derivative(np.zeros(4), np.zeros(2), 0.001), np.zeros(4))


def test_hcw_derivative_signs_and_coupling():
    n = 0.01
    state = np.array([100.0, -20.0, 0.5, -0.25])
    control = np.array([0.02, -0.03])
    derivative = hcw_derivative(state, control, n)
    expected = np.array([0.5, -0.25, 3*n*n*100 + 2*n*(-0.25) + 0.02, -2*n*0.5 - 0.03])
    np.testing.assert_allclose(derivative, expected)


def test_rk4_matches_constant_acceleration_when_mean_motion_is_zero():
    state = np.array([2.0, -3.0, 4.0, 5.0])
    control = np.array([0.5, -0.25])
    dt = 2.0
    actual = rk4_step(state, control, 0.0, dt)
    expected = np.array([2 + 4*dt + 0.5*0.5*dt**2,
                         -3 + 5*dt + 0.5*(-0.25)*dt**2,
                         4 + 0.5*dt,
                         5 - 0.25*dt])
    np.testing.assert_allclose(actual, expected, atol=1e-12)


def test_zero_radial_coordinate_static_along_track_solution_is_equilibrium():
    state = np.array([0.0, 12_500.0, 0.0, 0.0])
    actual = rk4_step(state, np.zeros(2), 0.0011, 10.0)
    np.testing.assert_allclose(actual, state, atol=1e-10)


@pytest.mark.parametrize("state,control,n,dt", [
    (np.zeros(3), np.zeros(2), 0.001, 0.1),
    (np.zeros(4), np.zeros(3), 0.001, 0.1),
    (np.array([np.nan, 0, 0, 0]), np.zeros(2), 0.001, 0.1),
    (np.zeros(4), np.zeros(2), -0.1, 0.1),
    (np.zeros(4), np.zeros(2), 0.1, 0.0),
])
def test_invalid_dynamics_inputs_raise(state, control, n, dt):
    with pytest.raises(DynamicsError):
        rk4_step(state, control, n, dt)
