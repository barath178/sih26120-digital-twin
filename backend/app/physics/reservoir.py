"""Heated-zone models for Cyclic Steam Stimulation.

Marx & Langenheim (1959): heated area for constant heat-injection rate H0 with conduction
losses to over/underburden
    A(t) = H0 * M_R * h * a_ob / (4 * K_ob^2 * dT) * G(t_D)
    t_D  = 4 * K_ob^2 * t / (M_R^2 * h^2 * a_ob)
    G    = exp(t_D) * erfc(sqrt(t_D)) + 2*sqrt(t_D/pi) - 1

Boberg & Lantz (1966): average heated-zone temperature after injection
    T_avg = T_R + (T_s - T_R) * [ V_r * V_z * (1 - delta) - delta ]
V_z is the closed-form mean temperature of a slab cooling into an infinite medium; V_r
(the cylinder equivalent) is computed here exactly by numerically integrating the
Green's function of the 2-D heat equation, then tabulated for fast interpolation.
delta is the energy removed with produced fluids.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.special import erfcx, i0e


# ----------------------------------------------------------------------------- Marx-Langenheim


def ml_g(t_d: float) -> float:
    s = math.sqrt(max(t_d, 0.0))
    return float(erfcx(s)) + 2.0 * s / math.sqrt(math.pi) - 1.0


def marx_langenheim_area(heat_rate_w: float, time_s: float, h: float, m_r: float, k_ob: float, m_ob: float, d_t: float) -> float:
    if heat_rate_w <= 0 or time_s <= 0 or d_t <= 0:
        return 0.0
    a_ob = k_ob / m_ob
    t_d = 4.0 * k_ob ** 2 * time_s / (m_r ** 2 * h ** 2 * a_ob)
    return heat_rate_w * m_r * h * a_ob / (4.0 * k_ob ** 2 * d_t) * ml_g(t_d)


def marx_langenheim_efficiency(time_s: float, h: float, m_r: float, k_ob: float, m_ob: float) -> float:
    """Fraction of injected heat still in the heated zone (thermal efficiency)."""
    a_ob = k_ob / m_ob
    t_d = 4.0 * k_ob ** 2 * time_s / (m_r ** 2 * h ** 2 * a_ob)
    return ml_g(t_d) / t_d if t_d > 1e-9 else 1.0


# ----------------------------------------------------------------------------- Boberg-Lantz


def v_z(theta_z: float) -> float:
    """Mean temperature of a unit slab, theta_z = 4*alpha*t/h^2."""
    if theta_z <= 1e-12:
        return 1.0
    return math.erf(1.0 / math.sqrt(theta_z)) - math.sqrt(theta_z / math.pi) * (1.0 - math.exp(-1.0 / theta_z))


def _build_vr_table():
    """Mean temperature of an infinite unit cylinder (initially 1, surroundings 0).

    T(r,t) = int_0^1 (rho / 2tau) exp(-(r^2+rho^2)/4tau) I0(r rho / 2tau) drho,  tau = alpha t / R^2
    V_r    = 2 int_0^1 r T(r,t) dr
    """
    n = 700
    x = (np.arange(n) + 0.5) / n  # midpoint rule on [0,1]
    r = x[:, None]
    rho = x[None, :]
    thetas = np.concatenate([[0.0], np.logspace(-4, 3, 120)])
    vals = [1.0]
    for tau in thetas[1:]:
        arg = r * rho / (2.0 * tau)
        kern = (rho / (2.0 * tau)) * np.exp(-((r - rho) ** 2) / (4.0 * tau)) * i0e(arg)
        t_r = kern.sum(axis=1) / n
        vals.append(float(2.0 * np.sum(x * t_r) / n))
    vals = np.clip(np.array(vals), 0.0, 1.0)
    return thetas, vals


_VR_THETA, _VR_VAL = _build_vr_table()
_VR_LOGT = np.log10(np.maximum(_VR_THETA[1:], 1e-12))


def v_r(theta_r: float) -> float:
    """theta_r = alpha * t / r_h^2."""
    if theta_r <= _VR_THETA[1]:
        return float(np.interp(theta_r, _VR_THETA[:2], _VR_VAL[:2]))
    if theta_r >= _VR_THETA[-1]:
        return float(_VR_VAL[-1] * _VR_THETA[-1] / theta_r)  # far-field 1/theta decay
    return float(np.interp(math.log10(theta_r), _VR_LOGT, _VR_VAL[1:]))


def boberg_lantz_temperature(t_res: float, t_s: float, r_h: float, h: float, alpha: float, t_since_s: float, delta: float) -> tuple[float, float, float]:
    """Return (T_avg, V_r, V_z)."""
    if r_h <= 0.0:
        return t_res, 0.0, 0.0
    vr = v_r(alpha * t_since_s / r_h ** 2)
    vz = v_z(4.0 * alpha * t_since_s / h ** 2)
    t_avg = t_res + (t_s - t_res) * (vr * vz * (1.0 - delta) - delta)
    return max(t_avg, t_res), vr, vz
