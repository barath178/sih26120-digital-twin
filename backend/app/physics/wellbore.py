"""Wellbore heat transfer.

* Steam injection: Ramey (1962) / Willhite (1967) radial heat loss per unit length
      q' = 2*pi*r_to*U*k_e*(T_s - T_e(z)) / (k_e + r_to*U*f(t))
  with the Hasan-Kabir (1991) transient time function f(t_D), valid at all times.
  Steam quality is marched down the tubing: dx/dz = -q' / (w * h_fg).
* Production: Ramey's analytic flowing-temperature profile gives the wellhead temperature
  and the mean tubing temperature (which controls rod-drag viscosity).
"""
from __future__ import annotations

import math

from .. import config
from . import fluid

WB = config.WELLBORE


def geothermal_temp(depth_m: float, depth_ref_m: float, t_res_c: float) -> float:
    grad = (t_res_c - WB["surface_temp_c"]) / depth_ref_m
    return WB["surface_temp_c"] + grad * depth_m


def hasan_kabir_f(t_s: float, alpha: float = WB["earth_diffusivity"], r_h: float = config.RESERVOIR["wellbore_radius_m"]) -> float:
    t_d = max(alpha * max(t_s, 1.0) / r_h ** 2, 1e-6)
    return math.log(math.exp(-0.2 * t_d) + (1.5 - 0.3719 * math.exp(-t_d)) * math.sqrt(t_d))


def steam_downhole(
    p_mpa: float,
    quality_surface: float,
    rate_tpd: float,
    depth_m: float,
    t_res_c: float,
    time_s: float,
    u_mult: float = 1.0,
    n_seg: int = 40,
):
    """March steam quality down the injection string.

    Returns dict(quality, temp_c, heat_loss_w, enthalpy_j_per_kg)."""
    w = max(rate_tpd, 1e-3) * 1000.0 / 86400.0  # kg/s
    u = WB["u_injection"] * u_mult
    r_to = WB["tubing_od_m"] / 2.0
    k_e = WB["earth_conductivity"]
    f_t = hasan_kabir_f(time_s)
    coeff = 2.0 * math.pi * r_to * u * k_e / (k_e + r_to * u * f_t)  # W/m/K
    ts = fluid.t_sat(p_mpa)
    hf = fluid.h_f(p_mpa)
    hfg = fluid.h_fg(p_mpa)
    h = hf + quality_surface * hfg  # J/kg
    dz = depth_m / n_seg
    q_total = 0.0
    temp = ts
    for i in range(n_seg):
        z = (i + 0.5) * dz
        te = geothermal_temp(z, depth_m, t_res_c)
        q_seg = coeff * max(temp - te, 0.0) * dz
        q_total += q_seg
        h -= q_seg / w
        if h >= hf:
            temp = ts
        else:  # fully condensed: hot water cooling below saturation
            temp = ts - (hf - h) / config.WATER_HEAT_CAPACITY
    quality = max((h - hf) / hfg, 0.0)
    return dict(quality=quality, temp_c=temp, heat_loss_w=q_total, enthalpy=h)


def production_temperatures(
    t_bottom_c: float,
    q_liq_m3d: float,
    water_cut: float,
    depth_m: float,
    t_res_c: float,
    time_s: float,
):
    """Ramey flowing temperature profile from the pump to surface.

    Returns (wellhead temperature C, mean tubing temperature C)."""
    q = max(q_liq_m3d, 0.05) / 86400.0
    rho = (1 - water_cut) * fluid.oil_density(t_bottom_c) + water_cut * 1000.0
    cp = (1 - water_cut) * config.OIL_HEAT_CAPACITY + water_cut * config.WATER_HEAT_CAPACITY
    w = q * rho
    u = WB["u_production"]
    r_to = WB["tubing_od_m"] / 2.0
    k_e = WB["earth_conductivity"]
    f_t = hasan_kabir_f(time_s)
    a = w * cp * (k_e + r_to * u * f_t) / (2.0 * math.pi * r_to * u * k_e)  # relaxation length, m
    grad = (t_res_c - WB["surface_temp_c"]) / depth_m
    t_eb = t_res_c

    def t_at(y):  # y = height above the pump
        return t_eb - grad * y + grad * a * (1.0 - math.exp(-y / a)) + (t_bottom_c - t_eb) * math.exp(-y / a)

    t_wh = t_at(depth_m)
    n = 20
    t_mean = sum(t_at((i + 0.5) * depth_m / n) for i in range(n)) / n
    return t_wh, t_mean
