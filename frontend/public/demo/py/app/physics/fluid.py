"""Fluid properties: Walther (ASTM D341) viscosity-temperature model, water, saturated steam."""
from __future__ import annotations

import math

import numpy as np

from .. import config

# ----------------------------------------------------------------------------- density

RHO_OIL_15C = 141.5 / (131.5 + config.API_GRAVITY) * 999.0  # kg/m3


def oil_density(t_c: float) -> float:
    return RHO_OIL_15C / (1.0 + config.OIL_THERMAL_EXPANSION * (t_c - 15.0))


def water_density(t_c: float) -> float:
    # Kell-type fit, adequate 0-300 C for liquid water at moderate pressure
    return 1000.0 * (1.0 - (t_c + 288.9414) / (508929.2 * (t_c + 68.12963)) * (t_c - 3.9863) ** 2)


# ----------------------------------------------------------------------------- Walther fit
# ASTM D341: log10(log10(nu + 0.7)) = A - B * log10(T[K]),  nu in cSt


def _fit_walther(table):
    t = np.array([row[0] for row in table], dtype=float)
    mu = np.array([row[1] for row in table], dtype=float)
    rho = np.array([oil_density(x) for x in t]) / 1000.0
    nu = mu / rho
    y = np.log10(np.log10(nu + 0.7))
    x = np.log10(t + 273.15)
    slope, intercept = np.polyfit(x, y, 1)
    pred = intercept + slope * x
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return float(intercept), float(-slope), 1.0 - ss_res / ss_tot


WALTHER_A, WALTHER_B, WALTHER_R2 = _fit_walther(config.VISCOSITY_TABLE)


def set_viscosity_table(table):
    """Refit the Walther coefficients to a new (T C, cP) table, e.g. uploaded lab PVT."""
    global WALTHER_A, WALTHER_B, WALTHER_R2
    WALTHER_A, WALTHER_B, WALTHER_R2 = _fit_walther(table)
    return WALTHER_A, WALTHER_B, WALTHER_R2


def oil_viscosity_cp(t_c: float) -> float:
    """Dead-oil dynamic viscosity (cP) from the fitted Walther equation."""
    t_k = max(t_c, 0.0) + 273.15
    nu = 10.0 ** (10.0 ** (WALTHER_A - WALTHER_B * math.log10(t_k))) - 0.7
    return max(nu, 0.5) * oil_density(t_c) / 1000.0


def water_viscosity_cp(t_c: float) -> float:
    """Vogel-type correlation for liquid water viscosity (cP)."""
    t_k = t_c + 273.15
    return 2.414e-5 * 10.0 ** (247.8 / (t_k - 140.0)) * 1000.0


def mixture_viscosity_cp(t_c: float, water_cut: float) -> float:
    """Log-mixing rule for produced oil/water in the tubing and pump barrel."""
    wc = min(max(water_cut, 0.0), 1.0)
    return math.exp((1.0 - wc) * math.log(oil_viscosity_cp(t_c)) + wc * math.log(water_viscosity_cp(t_c)))


# ----------------------------------------------------------------------------- steam tables
# Saturated water/steam (IAPWS-IF97 values): P MPa, Tsat C, hf kJ/kg, hfg kJ/kg
_SAT = np.array([
    [0.10, 99.61, 417.5, 2257.5],
    [0.20, 120.21, 504.7, 2201.6],
    [0.40, 143.61, 604.7, 2133.4],
    [0.60, 158.83, 670.4, 2085.8],
    [0.80, 170.41, 721.0, 2047.5],
    [1.00, 179.88, 762.5, 2014.6],
    [1.50, 198.29, 844.6, 1946.4],
    [2.00, 212.38, 908.5, 1889.8],
    [3.00, 233.85, 1008.3, 1794.9],
    [4.00, 250.35, 1087.4, 1713.5],
    [5.00, 263.94, 1154.5, 1639.7],
    [6.00, 275.59, 1213.7, 1570.8],
    [7.00, 285.83, 1267.4, 1504.9],
    [8.00, 295.01, 1317.1, 1441.1],
    [9.00, 303.35, 1363.7, 1378.1],
    [10.0, 311.00, 1408.0, 1317.1],
    [11.0, 318.08, 1450.3, 1255.8],
    [12.0, 324.68, 1491.3, 1193.6],
    [13.0, 330.85, 1531.4, 1130.3],
    [14.0, 336.67, 1571.0, 1066.0],
    [15.0, 342.16, 1610.3, 1000.5],
])
_LOG_P = np.log(_SAT[:, 0])


def t_sat(p_mpa: float) -> float:
    p = min(max(p_mpa, 0.1), 15.0)
    return float(np.interp(math.log(p), _LOG_P, _SAT[:, 1]))


def p_sat(t_c: float) -> float:
    t = min(max(t_c, 99.61), 342.16)
    return float(math.exp(np.interp(t, _SAT[:, 1], _LOG_P)))


def h_f(p_mpa: float) -> float:
    """Saturated-liquid enthalpy, J/kg."""
    p = min(max(p_mpa, 0.1), 15.0)
    return float(np.interp(math.log(p), _LOG_P, _SAT[:, 2])) * 1e3


def h_fg(p_mpa: float) -> float:
    """Latent heat of vaporisation, J/kg."""
    p = min(max(p_mpa, 0.1), 15.0)
    return float(np.interp(math.log(p), _LOG_P, _SAT[:, 3])) * 1e3


def h_water(t_c: float) -> float:
    """Liquid water enthalpy relative to 0 C, J/kg (compressed-liquid approximation)."""
    return config.WATER_HEAT_CAPACITY * t_c


def viscosity_curve(t_min: float = 40.0, t_max: float = 300.0, n: int = 53):
    ts = np.linspace(t_min, t_max, n)
    return [{"t_c": float(t), "mu_cp": oil_viscosity_cp(float(t))} for t in ts]
