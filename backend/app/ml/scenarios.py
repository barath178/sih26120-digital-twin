"""Random well / design sampling shared by the ML training pipelines."""
from __future__ import annotations

import numpy as np

from .. import config
from ..physics import coupling

PU = config.PUMPING_UNIT

# design-variable bounds used by the surrogate and optimiser
DESIGN_BOUNDS = dict(
    steam_t=(500.0, 2000.0),
    inj_rate_tpd=(120.0, 250.0),
    soak_days=(2.0, 12.0),
    prod_days=(60.0, 240.0),
    spm1=(PU["spm_min"], PU["spm_max"]),
    spm2=(PU["spm_min"], PU["spm_max"]),
    spm3=(PU["spm_min"], PU["spm_max"]),
    spm4=(PU["spm_min"], PU["spm_max"]),
    stroke_m=(PU["min_stroke_m"], PU["max_stroke_m"]),
    pump_offset_m=(10.0, 250.0),   # pump set this far above mid-perforation
)
DESIGN_KEYS = list(DESIGN_BOUNDS)

WELL_BOUNDS = dict(
    perm_eff_md=(300.0, 1600.0),
    net_pay_m=(8.0, 23.0),
    depth_m=(1050.0, 1300.0),
    np_total_m3=(0.0, 6000.0),
    u_mult=(0.6, 1.6),
    cool_mult=(0.6, 1.6),
)
WELL_KEYS = list(WELL_BOUNDS)


def sample_well(rng: np.random.Generator) -> dict:
    return {k: float(rng.uniform(*b)) for k, b in WELL_BOUNDS.items()}


def sample_design_vec(rng: np.random.Generator) -> dict:
    return {k: float(rng.uniform(*b)) for k, b in DESIGN_BOUNDS.items()}


def well_from_features(w: dict, well_id: str = "SYN") -> tuple[coupling.WellParams, coupling.CycleState]:
    p = coupling.WellParams(well_id=well_id, depth_m=w["depth_m"], net_pay_m=w["net_pay_m"], perm_md=w["perm_eff_md"],
                            u_mult=w["u_mult"], cool_mult=w["cool_mult"])
    st = coupling.CycleState(np_total_m3=w["np_total_m3"])
    return p, st


def well_features(p: coupling.WellParams, state: coupling.CycleState) -> dict:
    return dict(perm_eff_md=p.perm_md * p.pi_mult, net_pay_m=p.net_pay_m, depth_m=p.depth_m,
                np_total_m3=state.np_total_m3, u_mult=p.u_mult, cool_mult=p.cool_mult)


def design_from_vec(v: dict, depth_m: float, poc: bool = True) -> coupling.CycleDesign:
    return coupling.CycleDesign(
        steam_t=v["steam_t"], inj_rate_tpd=v["inj_rate_tpd"], soak_days=v["soak_days"], prod_days=v["prod_days"],
        spm_knots=[v["spm1"], v["spm2"], v["spm3"], v["spm4"]], stroke_m=v["stroke_m"],
        pump_depth_m=depth_m - v["pump_offset_m"], poc=poc,
    )


def vec_from_design(d: coupling.CycleDesign, depth_m: float) -> dict:
    return dict(steam_t=d.steam_t, inj_rate_tpd=d.inj_rate_tpd, soak_days=d.soak_days, prod_days=d.prod_days,
                spm1=d.spm_knots[0], spm2=d.spm_knots[1], spm3=d.spm_knots[2], spm4=d.spm_knots[3],
                stroke_m=d.stroke_m, pump_offset_m=depth_m - d.pump_depth_m)
