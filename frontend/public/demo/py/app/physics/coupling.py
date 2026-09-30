"""Coupled well-to-surface model: CSS heated zone <-> inflow <-> sucker-rod pump.

The same `advance()` function drives
  * the synthetic "plant" (hidden true parameters, used to generate field telemetry),
  * the digital twin (calibrated parameters, runs in lock-step with the plant),
  * the what-if simulator and the optimiser (whole-cycle simulations).

Coupling rule: actual liquid rate = min(reservoir deliverability, effective pump capacity).
When the pump is the bottleneck the fluid level rises and the flowing bottom-hole pressure
is back-calculated from the inflow equation; when the reservoir is the bottleneck the pump
runs pumped-off and fillage = inflow / capacity.
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass, field

import numpy as np

from .. import config
from . import fluid, reservoir, srp, wellbore

RES = config.RESERVOIR
WB = config.WELLBORE
PU = config.PUMPING_UNIT
ECON = config.ECONOMICS
B_O = 1.05
MD_TO_M2 = 9.869233e-16
SPM_KNOT_FRACTIONS = [0.0, 0.15, 0.4, 1.0]
POC_TRIGGER_FILL = 0.70
M3_TO_BBL = 6.2898
EPS_BBL = 1e-3                    # guards SOR / energy-per-barrel against zero oil
SURFACE_KWH_PER_M3_LIQ = 0.8      # SYNTHETIC_ASSUMPTION: surface pumping / heating of produced liquid
COOLDOWN_T_C = 120.0              # production is labelled COOLDOWN once the heated zone is below this
RODFALL_LIMIT = 0.9          # max SPM as a fraction of the free rod-fall speed limit
MIN_AVG_FILLAGE = 0.6
POC_RUN_FILL = 0.85


@dataclass
class WellParams:
    well_id: str = "BGW-000"
    depth_m: float = RES["depth_mid_perf_m"]
    net_pay_m: float = RES["net_pay_m"]
    perm_md: float = RES["permeability_md"]
    porosity: float = RES["porosity"]
    so_initial: float = RES["so_initial"]
    so_residual: float = RES["so_residual"]
    p_res_mpa: float = RES["pressure_mpa"]
    t_res_c: float = RES["temperature_c"]
    r_e: float = RES["drainage_radius_m"]
    r_w: float = RES["wellbore_radius_m"]
    skin_cold: float = RES["skin_cold"]
    # calibration multipliers (history-matched by the twin)
    pi_mult: float = 1.0
    u_mult: float = 1.0
    cool_mult: float = 1.0
    # equipment condition (plant truth; the twin infers it from dynacards)
    tv_leak: float = 0.0
    rod_parted: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class CycleDesign:
    steam_t: float = 1200.0
    inj_rate_tpd: float = 200.0
    quality: float = config.STEAM["surface_quality"]
    soak_days: float = 5.0
    prod_days: float = 150.0
    spm_knots: list = field(default_factory=lambda: [4.0, 4.0, 4.0, 4.0])
    stroke_m: float = 2.54
    pump_depth_m: float = 950.0
    vfd_auto: bool = False  # closed-loop fillage control instead of the knot schedule
    poc: bool = False       # pump-off controller: idle the unit when the barrel cannot fill

    @property
    def inj_days(self) -> float:
        return self.steam_t / max(self.inj_rate_tpd, 1e-6)

    @property
    def total_days(self) -> float:
        return self.inj_days + self.soak_days + self.prod_days

    def spm_at(self, t_prod_d: float) -> float:
        xs = [f * self.prod_days for f in SPM_KNOT_FRACTIONS]
        return float(np.interp(t_prod_d, xs, self.spm_knots))

    def to_dict(self):
        d = asdict(self)
        d["inj_days"] = self.inj_days
        d["total_days"] = self.total_days
        return d


@dataclass
class CycleState:
    phase: str = "injection"
    t_phase_d: float = 0.0
    day_in_cycle: float = 0.0
    cycle_no: int = 1
    steam_injected_t: float = 0.0
    heat_injected_j: float = 0.0
    r_h: float = 0.0
    t_s: float = RES["temperature_c"]
    p_sf: float = RES["pressure_mpa"]
    x_bh: float = 0.0
    w_rem0: float = 0.0
    w_rem: float = 0.0
    heat_removed_j: float = 0.0
    np_total_m3: float = 0.0
    np_cycle: float = 0.0
    wp_cycle: float = 0.0
    energy_kwh_cycle: float = 0.0
    last_q_liq: float = 5.0
    rod_damage: float = 0.0
    fail_hazard_cycle: float = 0.0
    due_for_steam: bool = False
    soak_elapsed_d: float = 0.0

    def copy(self) -> "CycleState":
        return copy.copy(self)

    def to_dict(self):
        return asdict(self)


def phase_label(phase: str, t_avg_c: float) -> str:
    """Build Bible cycle phase: INJECTION / SOAK / PRODUCTION / COOLDOWN (production below COOLDOWN_T_C)."""
    if phase == "production" and t_avg_c < COOLDOWN_T_C:
        return "COOLDOWN"
    return phase.upper()


def start_new_cycle(state: CycleState) -> None:
    state.phase = "injection"
    state.t_phase_d = 0.0
    state.day_in_cycle = 0.0
    state.cycle_no += 1
    state.steam_injected_t = 0.0
    state.heat_injected_j = 0.0
    state.r_h = 0.0
    state.w_rem0 = state.w_rem = 0.0
    state.heat_removed_j = 0.0
    state.np_cycle = state.wp_cycle = 0.0
    state.energy_kwh_cycle = 0.0
    state.fail_hazard_cycle = 0.0
    state.due_for_steam = False
    state.soak_elapsed_d = 0.0


# ----------------------------------------------------------------------------- helpers


def _so_factor(p: WellParams, state: CycleState) -> tuple[float, float]:
    pv = math.pi * p.r_e ** 2 * p.net_pay_m * p.porosity
    so = max(p.so_initial - state.np_total_m3 * B_O / pv, p.so_residual + 0.01)
    s_norm = (so - p.so_residual) / (p.so_initial - p.so_residual)
    return so, s_norm


def _oil_pi(p: WellParams, r_h: float, mu_hot_cp: float, s_norm: float) -> float:
    """Oil productivity index, m3/s/Pa, composite hot/cold radial Darcy flow."""
    k = p.perm_md * MD_TO_M2 * p.pi_mult * s_norm ** 2
    mu_c = fluid.oil_viscosity_cp(p.t_res_c) / 1000.0
    mu_h = mu_hot_cp / 1000.0
    if r_h <= p.r_w * 1.05:
        denom = mu_c * (math.log(p.r_e / p.r_w) + p.skin_cold)
    else:
        rh = min(r_h, p.r_e)
        denom = mu_h * math.log(rh / p.r_w) + mu_c * math.log(p.r_e / rh)
    return 2.0 * math.pi * k * p.net_pay_m / (B_O * max(denom, 1e-9))


def _vapour_density(p_mpa: float) -> float:
    t_k = fluid.t_sat(p_mpa) + 273.15
    return p_mpa * 1e6 * 0.018015 / (0.8 * 8.314 * t_k)


def failure_rate_per_year(goodman: float, pound_sev: float, rodfloat: float) -> float:
    return ECON["base_failure_rate_per_year"] * math.exp(4.0 * (goodman - 0.75)) * (1.0 + 3.0 * pound_sev) * (1.0 + 1.5 * rodfloat)


def cycles_to_failure(g_eff: float) -> float:
    return 1e7 * max(g_eff, 0.05) ** -8


# ----------------------------------------------------------------------------- production operating point


def production_point(p: WellParams, state: CycleState, spm: float, stroke: float, pump_depth: float) -> dict:
    """Instantaneous coupled operating point during the production phase (no state change)."""
    t_since_s = _time_since_injection_end_s(state)  # soak + production time
    alpha = RES["rock_conductivity"] / RES["rock_heat_capacity"] * p.cool_mult
    d_t0 = max(state.t_s - p.t_res_c, 1e-6)
    heat_cap = math.pi * max(state.r_h, 1e-3) ** 2 * p.net_pay_m * RES["rock_heat_capacity"] * d_t0
    delta = 0.5 * state.heat_removed_j / heat_cap if state.r_h > 0 else 0.0
    t_avg, vr, vz = reservoir.boberg_lantz_temperature(p.t_res_c, state.t_s, state.r_h, p.net_pay_m, alpha, t_since_s, delta)
    mu_o = fluid.oil_viscosity_cp(t_avg)
    so, s_norm = _so_factor(p, state)
    j = _oil_pi(p, state.r_h, mu_o, s_norm)
    rem = state.w_rem / state.w_rem0 if state.w_rem0 > 0 else 0.0
    p_eff = p.p_res_mpa + RES["steam_pressure_boost_mpa"] * rem
    wor = 0.15 + 1.5 * (1.0 - s_norm) + RES["wor_condensate_initial"] * rem
    wc = wor / (1.0 + wor)
    t_prod = t_avg
    time_s = max(state.t_phase_d, 0.05) * 86400.0
    t_wh, t_tub = wellbore.production_temperatures(t_prod, state.last_q_liq, wc, pump_depth, p.t_res_c, time_s)
    mu_pump = fluid.mixture_viscosity_cp(t_prod, wc)
    mu_tub = fluid.mixture_viscosity_cp(t_tub, wc)
    rho = srp.liquid_density(t_tub, wc)
    dz_perf = max(p.depth_m - pump_depth, 0.0)
    pwf_min = WB["casing_pressure_mpa"] + rho * config.G * (dz_perf + WB["min_submergence_m"]) / 1e6
    q_o_max = max(j * (p_eff - pwf_min) * 1e6 * 86400.0, 0.0)
    q_liq_deliv = q_o_max * (1.0 + wor)
    pip_off = pwf_min - rho * config.G * dz_perf / 1e6
    if spm <= 0.0:
        return dict(t_avg=t_avg, v_r=vr, v_z=vz, mu_oil_cp=mu_o, so=so, wor=wor, wc=wc, q_oil_deliv=q_o_max,
                    q_liq_deliv=q_liq_deliv, q_liq=0.0, q_oil=0.0, q_water=0.0, cap=0.0, fillage=0.0,
                    pwf=p_eff, pip=p_eff - rho * config.G * dz_perf / 1e6, p_eff=p_eff, t_wh=WB["surface_temp_c"],
                    t_tub=t_tub, mu_pump=mu_pump, mu_tub=mu_tub, rho=rho, flash_sev=0.0, viscous_fill=1.0,
                    limiting="shut-in", pump_limited=False, loads=None, pi=j)
    q0 = srp.quick_srp(spm, stroke, pump_depth, pip_off, rho, mu_tub, 1.0)
    f_visc = srp.viscous_fill_limit(mu_pump, spm)
    cap_geom = 0.0 if p.rod_parted else q0["capacity"] * (1.0 - 0.6 * p.tv_leak)
    rho_g_dz = rho * config.G * dz_perf / 1e6

    def at_rate(q_liq_try: float):
        """Pressures, steam-flash severity and effective pump capacity when producing q_liq_try.
        A higher rate draws the fluid level down, lowering intake pressure and promoting flashing."""
        pwf_q = max(p_eff - q_liq_try / (1.0 + wor) / 86400.0 / max(j, 1e-18) / 1e6, pwf_min)
        pip_q = pwf_q - rho_g_dz
        flash_q = min(max((t_prod - fluid.t_sat(max(pip_q, 0.1)) + 5.0) / 30.0, 0.0), 1.0) if t_prod > 100.0 else 0.0
        return cap_geom * f_visc * (1.0 - 0.5 * flash_q), flash_q, pwf_q, pip_q

    cap_eff, flash, pwf, pip = at_rate(q_liq_deliv)
    if cap_eff >= q_liq_deliv:
        q_liq = q_liq_deliv
        fill = q_liq / max(cap_geom, 1e-9)
        pump_limited = False
        limiting = "reservoir inflow"
    else:
        # pump-limited: solve q = cap_eff(q) (cap_eff decreases monotonically with q)
        lo, hi = 0.0, q_liq_deliv
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            if at_rate(mid)[0] >= mid:
                lo = mid
            else:
                hi = mid
        q_liq = lo
        cap_eff, flash, pwf, pip = at_rate(q_liq)
        fill = f_visc * (1.0 - 0.5 * flash)
        pump_limited = True
        if p.rod_parted:
            limiting = "parted rods"
        elif flash > 0.3 and (1.0 - 0.5 * flash) < f_visc:
            limiting = "steam flashing at pump intake"
        elif f_visc < 0.85:
            limiting = "viscous barrel filling"
        else:
            limiting = "pump capacity"
    loads = srp.quick_srp(spm, stroke, pump_depth, max(pip, 0.1), rho, mu_tub, min(fill, 1.0))
    q_o = q_liq / (1.0 + wor)
    return dict(
        t_avg=t_avg, v_r=vr, v_z=vz, mu_oil_cp=mu_o, so=so, wor=wor, wc=wc,
        q_oil_deliv=q_o_max, q_liq_deliv=q_liq_deliv, q_liq=q_liq, q_oil=q_o, q_water=q_liq - q_o,
        cap=cap_geom, fillage=min(fill, 1.0), pwf=pwf, pip=pip, p_eff=p_eff, t_wh=t_wh, t_tub=t_tub,
        mu_pump=mu_pump, mu_tub=mu_tub, rho=rho, flash_sev=flash, viscous_fill=f_visc,
        limiting=limiting, pump_limited=pump_limited, loads=loads, pi=j,
    )


def _time_since_injection_end_s(state: CycleState) -> float:
    if state.phase == "soak":
        return state.t_phase_d * 86400.0
    if state.phase == "production":
        return (state.soak_elapsed_d + state.t_phase_d) * 86400.0
    return 0.0


def vfd_target_spm(p: WellParams, state: CycleState, design: CycleDesign, current_spm: float, target_fill: float = 0.875) -> float:
    """SPM that brings pump fillage to the target, within equipment and rod-fall limits."""
    spm = max(current_spm, PU["spm_min"])
    for _ in range(4):
        op = production_point(p, state, spm, design.stroke_m, design.pump_depth_m)
        if op["loads"] is None:
            return spm
        cap_per_spm = op["cap"] / spm
        f_lim = op["viscous_fill"] * (1.0 - 0.5 * op["flash_sev"])
        # desired: q_liq_deliv = cap_per_spm * spm * target  (never ask more than the barrel can fill)
        need = op["q_liq_deliv"] / max(cap_per_spm * min(target_fill, f_lim), 1e-9)
        hi = min(PU["spm_max"], 0.9 * op["loads"]["spm_rodfall"])
        spm = float(np.clip(need, PU["spm_min"], max(hi, PU["spm_min"])))
    return spm


def evaluate_production(p: WellParams, state: CycleState, design: CycleDesign, spm: float) -> tuple[dict, dict]:
    """Operating point plus pump-off control, rod loads and failure hazard at a given SPM.
    Pure function of the state; shared by advance() and the advisor's what-if checks."""
    op = production_point(p, state, spm, design.stroke_m, design.pump_depth_m)
    ld = op["loads"]
    runtime = 1.0
    if ld is not None and (design.poc or design.vfd_auto) and not op["pump_limited"] and op["fillage"] < POC_TRIGGER_FILL:
        # pump-off control: run at full barrel fill, idle while the annulus refills
        runtime = max(op["fillage"] / POC_RUN_FILL, 0.05)
        op["fillage"] = POC_RUN_FILL
        ld = srp.quick_srp(spm, design.stroke_m, design.pump_depth_m, max(op["pip"], 0.1), op["rho"], op["mu_tub"], POC_RUN_FILL)
    ev = dict(runtime=runtime, loads=ld, pound=0.0, rodfloat=0.0, failure_rate=0.0, g_eff=0.0, motor_kw=0.0)
    if ld is not None:
        rodfloat = float(np.clip((spm - 0.85 * ld["spm_rodfall"]) / (0.3 * ld["spm_rodfall"]), 0.0, 1.0))
        if ld["mprl"] < 0:
            rodfloat = max(rodfloat, min(-ld["mprl"] / 10000.0, 1.0))
        pound = ld["pound_severity"] if op["fillage"] < 0.95 else 0.0
        ev.update(pound=pound, rodfloat=rodfloat,
                  failure_rate=failure_rate_per_year(ld["goodman"], pound, rodfloat) * (0.3 + 0.7 * runtime),
                  g_eff=ld["goodman"] * (1.0 + 0.5 * pound), motor_kw=ld["motor_kw"] * runtime + 0.3 * (1.0 - runtime))
    return op, ev


# ----------------------------------------------------------------------------- stepping


def advance(p: WellParams, state: CycleState, design: CycleDesign, dt_d: float, spm: float | None = None) -> dict:
    """Advance the well by dt_d days. Returns the outputs at the start of the step."""
    out: dict = dict(phase=state.phase, cycle_no=state.cycle_no, day_in_cycle=state.day_in_cycle, t_phase_d=state.t_phase_d,
                     r_h=state.r_h, steam_rate_tpd=0.0, steam_quality_wh=0.0, x_bh=state.x_bh, q_oil=0.0, q_water=0.0,
                     q_liq=0.0, spm=0.0, stroke=design.stroke_m, pump_depth=design.pump_depth_m, fillage=0.0,
                     pprl=0.0, mprl=0.0, goodman=0.0, torque=0.0, motor_kw=0.0, heat_loss_mw=0.0,
                     failure_rate=0.0, pound_sev=0.0, flash_sev=0.0, limiting="", rodfloat=0.0)
    ts_res = p.t_res_c
    if state.phase == "injection":
        rate = design.inj_rate_tpd
        remaining = design.steam_t - state.steam_injected_t
        dt_eff = min(dt_d, remaining / rate) if rate > 0 else dt_d
        p_sf = min(p.p_res_mpa + RES["injectivity_mpa_per_tpd"] * rate, RES["frac_pressure_mpa"])
        t_mid = (state.t_phase_d + dt_eff / 2.0) * 86400.0
        wb = wellbore.steam_downhole(p_sf, design.quality, rate, p.depth_m, ts_res, t_mid, p.u_mult)
        h_in = wb["enthalpy"] - fluid.h_water(ts_res)
        heat_rate = rate * 1000.0 / 86400.0 * h_in
        state.heat_injected_j += heat_rate * dt_eff * 86400.0
        state.steam_injected_t += rate * dt_eff
        state.w_rem0 = state.w_rem = state.steam_injected_t * RES["condensate_recoverable_frac"]
        state.t_s = wb["temp_c"]
        state.p_sf = p_sf
        state.x_bh = wb["quality"]
        elapsed = (state.t_phase_d + dt_eff) * 86400.0
        area = reservoir.marx_langenheim_area(state.heat_injected_j / elapsed, elapsed, p.net_pay_m, RES["rock_heat_capacity"],
                                              RES["overburden_conductivity"], RES["overburden_heat_capacity"],
                                              state.t_s - ts_res)
        state.r_h = min(math.sqrt(area / math.pi), p.r_e)
        x_mix = max(wb["quality"], design.quality) * 0.5 + 0.5 * wb["quality"]
        rho_col = 1.0 / (max(x_mix, 0.01) / _vapour_density(p_sf) + (1 - x_mix) / 750.0)
        p_wh = max(p_sf - rho_col * config.G * p.depth_m / 1e6, 0.5)
        out.update(steam_rate_tpd=rate, steam_quality_wh=design.quality, x_bh=wb["quality"], t_bh=wb["temp_c"],
                   heat_loss_mw=wb["heat_loss_w"] / 1e6, whp=p_wh, wht=fluid.t_sat(p_wh), p_sf=p_sf,
                   t_avg=state.t_s, mu_oil_cp=fluid.oil_viscosity_cp(state.t_s), heat_rate_mw=heat_rate / 1e6)
        state.t_phase_d += dt_eff
        state.day_in_cycle += dt_eff
        if state.steam_injected_t >= design.steam_t - 1e-9:
            state.phase = "soak"
            state.t_phase_d = 0.0
        return out

    if state.phase == "soak":
        alpha = RES["rock_conductivity"] / RES["rock_heat_capacity"] * p.cool_mult
        t_avg, _, _ = reservoir.boberg_lantz_temperature(ts_res, state.t_s, state.r_h, p.net_pay_m, alpha, state.t_phase_d * 86400.0, 0.0)
        decay = math.exp(-state.t_phase_d / 3.0)
        whp = 0.8 + (state.p_sf - 0.9 - 0.8) * decay
        out.update(t_avg=t_avg, mu_oil_cp=fluid.oil_viscosity_cp(t_avg), whp=whp, wht=max(fluid.t_sat(max(whp, 0.1)) * decay + 35 * (1 - decay), 35.0))
        state.t_phase_d += dt_d
        state.day_in_cycle += dt_d
        if state.t_phase_d >= design.soak_days - 1e-9:
            state.soak_elapsed_d = state.t_phase_d
            state.phase = "production"
            state.t_phase_d = 0.0
        return out

    # ------------------------------------------------------------------ production
    if spm is None:
        spm = vfd_target_spm(p, state, design, design.spm_at(state.t_phase_d)) if design.vfd_auto else design.spm_at(state.t_phase_d)
    spm = float(np.clip(spm, 0.0, PU["spm_max"]))
    op, ev = evaluate_production(p, state, design, spm)
    out["runtime"] = ev["runtime"]
    if ev["loads"] is not None:
        ld = ev["loads"]
        state.rod_damage += spm * 1440.0 * dt_d * ev["runtime"] / cycles_to_failure(ev["g_eff"])
        out.update(pprl=ld["pprl"], mprl=ld["mprl"], goodman=ld["goodman"], torque=ld["torque"], motor_kw=ev["motor_kw"],
                   fo=ld["fo"], spm_rodfall=ld["spm_rodfall"], pound_sev=ev["pound"], rodfloat=ev["rodfloat"],
                   failure_rate=ev["failure_rate"], n_over_n0=ld["n_over_n0"], fo_over_skr=ld["fo_over_skr"], sp=ld["sp"])
        state.fail_hazard_cycle += ev["failure_rate"] * dt_d / 365.0
        state.energy_kwh_cycle += ev["motor_kw"] * 24.0 * dt_d
    out.update(spm=spm, q_oil=op["q_oil"], q_water=op["q_water"], q_liq=op["q_liq"], fillage=op["fillage"], t_avg=op["t_avg"],
               mu_oil_cp=op["mu_oil_cp"], q_liq_deliv=op["q_liq_deliv"], q_oil_deliv=op["q_oil_deliv"], cap=op["cap"],
               pwf=op["pwf"], pip=op["pip"], wc=op["wc"], wht=op["t_wh"], whp=WB["wellhead_pressure_mpa"], t_tub=op["t_tub"],
               mu_tub=op["mu_tub"], mu_pump=op["mu_pump"], flash_sev=op["flash_sev"], viscous_fill=op["viscous_fill"],
               limiting=op["limiting"], pump_limited=op["pump_limited"], so=op["so"], v_r=op["v_r"], v_z=op["v_z"],
               p_eff=op["p_eff"], pi=op["pi"], rho=op["rho"])
    # state updates
    dt_s = dt_d * 86400.0
    q_o_s = op["q_oil"] / 86400.0
    q_w_s = op["q_water"] / 86400.0
    h_f = (q_o_s * fluid.oil_density(op["t_avg"]) * config.OIL_HEAT_CAPACITY + q_w_s * 1000.0 * config.WATER_HEAT_CAPACITY) * max(op["t_avg"] - ts_res, 0.0)
    state.heat_removed_j += h_f * dt_s
    rem = state.w_rem / state.w_rem0 if state.w_rem0 > 0 else 0.0
    q_w_cond = op["q_oil"] * RES["wor_condensate_initial"] * rem
    state.w_rem = max(state.w_rem - q_w_cond * dt_d, 0.0)
    state.np_total_m3 += op["q_oil"] * dt_d
    state.np_cycle += op["q_oil"] * dt_d
    state.wp_cycle += op["q_water"] * dt_d
    state.last_q_liq = max(op["q_liq"], 0.05)
    state.t_phase_d += dt_d
    state.day_in_cycle += dt_d
    if state.t_phase_d >= design.prod_days - 1e-9:
        state.due_for_steam = True
    return out


# ----------------------------------------------------------------------------- whole cycle


def daily_economics(out: dict, dt_d: float, design: CycleDesign) -> float:
    rev = out["q_oil"] * dt_d * ECON["oil_price_inr_per_m3"]
    steam = out["steam_rate_tpd"] * dt_d * config.STEAM["fuel_cost_inr_per_tonne"]
    power = out["motor_kw"] * 24.0 * dt_d * ECON["electricity_inr_per_kwh"]
    water = out["q_water"] * dt_d * ECON["water_handling_inr_per_m3"]
    fail = out["failure_rate"] / 365.0 * dt_d * (ECON["workover_cost_inr"] + out["q_oil"] * ECON["workover_days"] * ECON["oil_price_inr_per_m3"])
    return rev - steam - power - water - fail


def simulate_cycle(p: WellParams, design: CycleDesign, state0: CycleState | None = None, dt_d: float = 1.0,
                   record: bool = True) -> dict:
    """Simulate one complete CSS cycle (injection -> soak -> production)."""
    if state0 is None:
        state = CycleState()
    else:  # continue the well's history (depletion, rod damage) with a fresh cycle
        state = state0.copy()
        start_new_cycle(state)
    series = {k: [] for k in ("day", "phase", "q_oil", "q_water", "q_liq", "q_liq_deliv", "cap", "fillage", "t_avg", "mu_oil_cp",
                              "spm", "pprl", "mprl", "goodman", "torque", "motor_kw", "r_h", "steam_rate_tpd", "sor_cum",
                              "wht", "failure_rate", "cash_inr", "limiting", "flash_sev")}
    steam_total = 0.0
    oil_total = 0.0
    water_total = 0.0
    npv = 0.0
    fill_sum = 0.0
    fill_n = 0
    max_goodman = 0.0
    max_torque = 0.0
    max_motor = 0.0
    rodfall_ratio = 0.0
    pound_days = 0.0
    daily_r = (1.0 + ECON["discount_rate_annual"]) ** (1.0 / 365.0) - 1.0
    t = 0.0
    guard = 0
    while guard < 5000:
        guard += 1
        if state.phase == "production" and state.t_phase_d >= design.prod_days - 1e-9:
            break
        step = dt_d
        if state.phase == "injection":
            step = min(dt_d, max((design.steam_t - state.steam_injected_t) / design.inj_rate_tpd, 1e-6))
        out = advance(p, state, design, step)
        steam_total += out["steam_rate_tpd"] * step
        oil_total += out["q_oil"] * step
        water_total += out["q_water"] * step
        cash = daily_economics(out, step, design)
        npv += cash / (1.0 + daily_r) ** t
        if out["phase"] == "production":
            fill_sum += out["fillage"] * step
            fill_n += step
            max_goodman = max(max_goodman, out["goodman"])
            max_torque = max(max_torque, out["torque"])
            max_motor = max(max_motor, out["motor_kw"])
            if out.get("spm_rodfall"):
                rodfall_ratio = max(rodfall_ratio, out["spm"] / out["spm_rodfall"])
            if out["fillage"] < 0.75:
                pound_days += step
        if record:
            series["day"].append(t)
            series["phase"].append(out["phase"])
            for k in ("q_oil", "q_water", "q_liq", "q_liq_deliv", "cap", "fillage", "t_avg", "mu_oil_cp", "spm", "pprl", "mprl",
                      "goodman", "torque", "motor_kw", "r_h", "steam_rate_tpd", "wht", "failure_rate", "flash_sev"):
                series[k].append(float(out.get(k, 0.0) or 0.0))
            series["limiting"].append(out.get("limiting", ""))
            series["sor_cum"].append(steam_total / oil_total if oil_total > 1e-6 else None)
            series["cash_inr"].append(cash / step)
        t += step
    total_days = max(t, 1e-6)
    fail_prob = 1.0 - math.exp(-state.fail_hazard_cycle)
    summary = dict(
        cum_oil_m3=oil_total, cum_water_m3=water_total, steam_t=steam_total,
        sor=steam_total / oil_total if oil_total > 1e-6 else 99.0,
        oil_per_day=oil_total / total_days, cycle_days=total_days,
        avg_fillage=fill_sum / fill_n if fill_n else 0.0, pound_days=pound_days,
        max_goodman=max_goodman, max_torque_nm=max_torque, max_motor_kw=max_motor,
        rodfall_ratio=rodfall_ratio, failure_prob=fail_prob, npv_inr=npv, npv_per_day=npv / total_days,
        energy_kwh=state.energy_kwh_cycle, peak_oil=max(series["q_oil"]) if record and series["q_oil"] else 0.0,
    )
    # energy accounting (Build Bible 6.6): pumping + steam generation (proxy) + surface handling (proxy)
    bbl = max(oil_total, 0.0) * M3_TO_BBL
    steam_kwh = steam_total * config.STEAM_ENERGY_KWH_PER_T
    surface_kwh = SURFACE_KWH_PER_M3_LIQ * (oil_total + water_total)
    summary.update(steam_kwh=steam_kwh, surface_kwh=surface_kwh, total_energy_kwh=state.energy_kwh_cycle + steam_kwh + surface_kwh,
                   energy_per_bbl_kwh=(state.energy_kwh_cycle + steam_kwh + surface_kwh) / max(bbl, EPS_BBL),
                   cum_oil_bbl=bbl)
    summary["constraints"] = constraint_report(summary, design)
    return dict(summary=summary, series=series if record else None, end_state=state)


def constraint_report(summary: dict, design: CycleDesign) -> dict:
    gens_cap = max(g["capacity_tpd"] for g in config.STEAM["generators"])
    return dict(
        goodman=dict(value=summary["max_goodman"], limit=1.0, ok=summary["max_goodman"] <= 1.0),
        gearbox_torque=dict(value=summary["max_torque_nm"], limit=PU["gearbox_rating_nm"], ok=summary["max_torque_nm"] <= PU["gearbox_rating_nm"]),
        motor_power=dict(value=summary["max_motor_kw"], limit=PU["motor_rating_kw"], ok=summary["max_motor_kw"] <= PU["motor_rating_kw"]),
        steam_generator=dict(value=design.inj_rate_tpd, limit=gens_cap, ok=design.inj_rate_tpd <= gens_cap),
        pump_fillage=dict(value=summary["avg_fillage"], limit=MIN_AVG_FILLAGE, ok=summary["avg_fillage"] >= MIN_AVG_FILLAGE),
        rod_fall=dict(value=summary["rodfall_ratio"], limit=RODFALL_LIMIT, ok=summary["rodfall_ratio"] <= RODFALL_LIMIT),
    )
