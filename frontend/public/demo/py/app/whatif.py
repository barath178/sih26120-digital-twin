"""What-if simulation of one well's next cycle.

Imports only the physics and config (numpy + scipy.special), so the same code runs in the API
and in the browser (Pyodide) for the static Vercel demo.
"""
from __future__ import annotations

import math

import numpy as np

from .physics import coupling


def design_from_payload(d: dict) -> coupling.CycleDesign:
    return coupling.CycleDesign(steam_t=float(d["steam_t"]), inj_rate_tpd=float(d["inj_rate_tpd"]), quality=float(d.get("quality", 0.8)),
                                soak_days=float(d["soak_days"]), prod_days=float(d["prod_days"]),
                                spm_knots=[float(x) for x in d["spm_knots"]], stroke_m=float(d["stroke_m"]),
                                pump_depth_m=float(d["pump_depth_m"]), vfd_auto=bool(d.get("vfd_auto", False)),
                                poc=bool(d.get("poc", False)))


def sanitize(obj):
    """Make results JSON-safe (no NaN/inf, numpy types)."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def compare(base: dict, new: dict) -> dict:
    cycle_ratio = 365.0
    return dict(
        oil_per_day=new["oil_per_day"] - base["oil_per_day"],
        oil_per_day_pct=100.0 * (new["oil_per_day"] / max(base["oil_per_day"], 1e-9) - 1.0),
        sor=new["sor"] - base["sor"],
        failure_prob=new["failure_prob"] - base["failure_prob"],
        npv_per_day=new["npv_per_day"] - base["npv_per_day"],
        npv_per_year_inr=(new["npv_per_day"] - base["npv_per_day"]) * cycle_ratio,
        avg_fillage=new["avg_fillage"] - base["avg_fillage"],
        pound_days=new["pound_days"] - base["pound_days"],
        max_goodman=new["max_goodman"] - base["max_goodman"],
        energy_kwh=new["energy_kwh"] - base["energy_kwh"],
    )


def design_dict(d: coupling.CycleDesign) -> dict:
    out = d.to_dict()
    out["spm_schedule"] = [dict(day=round(fr * d.prod_days, 1), spm=s) for fr, s in zip(coupling.SPM_KNOT_FRACTIONS, d.spm_knots)]
    return out


def simulate(params: coupling.WellParams, state: coupling.CycleState, current: coupling.CycleDesign,
             design: dict, compare_current: bool = True) -> dict:
    """Simulate `design` from the well's current state; optionally also the current plan for comparison.
    Raises ValueError for a physically invalid design."""
    d = design_from_payload(design)
    if d.pump_depth_m >= params.depth_m:
        raise ValueError(f"pump must be set above mid-perforation depth ({params.depth_m:.0f} m)")
    for s in d.spm_knots:
        if not (0.5 <= s <= 12):
            raise ValueError("SPM must be between 0.5 and 12")
    res = coupling.simulate_cycle(params, d, state0=state.copy())
    out = dict(design=design_dict(d), summary=res["summary"], series=res["series"])
    if compare_current:
        base = coupling.simulate_cycle(params, current, state0=state.copy())
        out["current"] = dict(design=design_dict(current), summary=base["summary"], series=base["series"])
        out["delta"] = compare(base["summary"], res["summary"])
    return out


def simulate_json(ctx: dict, body: dict) -> dict:
    """Browser entry point: `ctx` is a well's recorded {params, state, current} (plain dicts)."""
    params = coupling.WellParams(**ctx["params"])
    state = coupling.CycleState(**ctx["state"])
    current = design_from_payload(ctx["current"])
    return sanitize(simulate(params, state, current, body["design"], bool(body.get("compare_current", True))))
