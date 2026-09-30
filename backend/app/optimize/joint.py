"""Joint CSS + SRP optimiser.

Decision variables (10): steam volume, injection rate, soak time, production-cycle length,
4 SPM knots (a time-varying VFD schedule over the cycle), stroke length, pump setting depth.
Objectives: maximise cycle-average oil rate, minimise steam-oil ratio, minimise failure risk.
Constraints: modified-Goodman rod loading, gearbox torque, motor power, steam-generator
capacity (variable bound), minimum average pump fillage, rod-fall speed, positive NPV.

NSGA-II (pymoo) searches on the ML surrogate; every Pareto point is then re-simulated with
the full physics model and only physics-feasible designs can be recommended.
Bayesian optimisation (Optuna TPE) directly on the physics model is offered as an alternative.
"""
from __future__ import annotations

import math
import time

import numpy as np

from .. import config
from ..ml import scenarios
from ..ml.surrogate import surrogate
from ..physics import coupling
from ..whatif import compare, design_from_payload, sanitize  # noqa: F401  (re-exported)

PU = config.PUMPING_UNIT
GOODMAN_LIMIT = 0.95


def _bounds():
    lo = np.array([scenarios.DESIGN_BOUNDS[k][0] for k in scenarios.DESIGN_KEYS])
    hi = np.array([scenarios.DESIGN_BOUNDS[k][1] for k in scenarios.DESIGN_KEYS])
    gen_cap = max(g["capacity_tpd"] for g in config.STEAM["generators"])
    hi[scenarios.DESIGN_KEYS.index("inj_rate_tpd")] = min(hi[scenarios.DESIGN_KEYS.index("inj_rate_tpd")], gen_cap)
    return lo, hi


def physics_summary(params: coupling.WellParams, state: coupling.CycleState, design: coupling.CycleDesign, record=False):
    return coupling.simulate_cycle(params, design, state0=state, dt_d=1.0, record=record)


DEFAULT_LIMITS = dict(  # USER_CONFIGURED (editable on the optimiser page); none is a verified field limit
    min_fillage=coupling.MIN_AVG_FILLAGE, max_goodman=GOODMAN_LIMIT, max_risk=0.35, max_steam_t=2000.0,
    max_torque_frac=1.0, max_motor_kw=PU["motor_rating_kw"], max_rodfall_ratio=coupling.RODFALL_LIMIT,
    min_npv_gain=0.0,  # INR/day: a recommended plan may never earn less than the current plan
)
# Build Bible 8: w_oil, w_sor, w_energy, w_risk. Oil is the primary objective; with equal weights the search happily trades a
# third of the oil for steam savings and can lower net value, so oil is weighted highest by default.
DEFAULT_WEIGHTS = dict(oil=3.0, sor=1.0, energy=0.5, risk=0.5)
LIMIT_TEXT = dict(
    min_fillage="average pump fillage", max_goodman="peak rod loading (Goodman)", max_risk="cycle failure risk",
    max_steam_t="steam volume", max_torque_frac="gearbox torque", max_motor_kw="motor power", max_rodfall_ratio="SPM vs rod-fall limit",
)


def merged_limits(limits: dict | None) -> dict:
    out = dict(DEFAULT_LIMITS)
    if limits:
        out.update({k: float(v) for k, v in limits.items() if k in DEFAULT_LIMITS and v is not None})
    return out


def merged_weights(weights: dict | None) -> dict:
    out = dict(DEFAULT_WEIGHTS)
    if weights:
        out.update({k: max(float(v), 0.0) for k, v in weights.items() if k in DEFAULT_WEIGHTS and v is not None})
    return out


def feasibility(s: dict, design: coupling.CycleDesign, limits: dict, ref_npv: float | None = None) -> tuple[bool, list[str]]:
    """PASS/FAIL with reasons for one full-physics candidate summary."""
    why = []
    if ref_npv is not None and s["npv_per_day"] < ref_npv + limits["min_npv_gain"]:
        why.append(f"net value {s['npv_per_day'] / 1000:.1f}k < current plan {ref_npv / 1000:.1f}k INR/day")
    if s["avg_fillage"] < limits["min_fillage"]:
        why.append(f"{LIMIT_TEXT['min_fillage']} {s['avg_fillage'] * 100:.0f}% < {limits['min_fillage'] * 100:.0f}%")
    if s["max_goodman"] > limits["max_goodman"]:
        why.append(f"{LIMIT_TEXT['max_goodman']} {s['max_goodman'] * 100:.0f}% > {limits['max_goodman'] * 100:.0f}%")
    if s["failure_prob"] > limits["max_risk"]:
        why.append(f"{LIMIT_TEXT['max_risk']} {s['failure_prob'] * 100:.0f}% > {limits['max_risk'] * 100:.0f}%")
    if design.steam_t > limits["max_steam_t"] + 1e-6:
        why.append(f"steam {design.steam_t:.0f} t > budget {limits['max_steam_t']:.0f} t")
    if s["max_torque_nm"] > limits["max_torque_frac"] * PU["gearbox_rating_nm"]:
        why.append(f"gearbox torque {s['max_torque_nm'] / 1000:.1f} kN.m > {limits['max_torque_frac'] * PU['gearbox_rating_nm'] / 1000:.1f}")
    if s["max_motor_kw"] > limits["max_motor_kw"]:
        why.append(f"motor {s['max_motor_kw']:.1f} kW > {limits['max_motor_kw']:.0f} kW")
    if s["rodfall_ratio"] > limits["max_rodfall_ratio"]:
        why.append(f"speed {s['rodfall_ratio'] * 100:.0f}% of rod-fall limit > {limits['max_rodfall_ratio'] * 100:.0f}%")
    if design.inj_rate_tpd > max(g["capacity_tpd"] for g in config.STEAM["generators"]) + 1e-6:
        why.append("injection rate exceeds generator capacity")
    if s["npv_per_day"] <= 0:
        why.append("net value not positive")
    return (not why), why


def feasible(s: dict) -> bool:  # backwards-compatible default-limit check
    return not [1 for k, ok in dict(
        g=s["max_goodman"] <= GOODMAN_LIMIT, t=s["max_torque_nm"] <= PU["gearbox_rating_nm"], m=s["max_motor_kw"] <= PU["motor_rating_kw"],
        f=s["avg_fillage"] >= coupling.MIN_AVG_FILLAGE, r=s["rodfall_ratio"] <= coupling.RODFALL_LIMIT, n=s["npv_per_day"] > 0).items() if not ok]


def objective(s: dict, ref: dict, w: dict) -> float:
    """Build Bible 8 objective (lower is better): -w_oil*oil + w_sor*SOR + w_energy*energy/bbl + w_risk*risk,
    each term normalised by the deterministic baseline so the weights are comparable."""
    return (-w["oil"] * s["oil_per_day"] / max(ref["oil_per_day"], 1e-6) + w["sor"] * s["sor"] / max(ref["sor"], 1e-6)
            + w["energy"] * s["energy_per_bbl_kwh"] / max(ref["energy_per_bbl_kwh"], 1e-6)
            + w["risk"] * s["failure_prob"] / max(ref["failure_prob"], 1e-3))


def uncertainty(params: coupling.WellParams, state: coupling.CycleState, design: coupling.CycleDesign) -> dict:
    """Interval from re-simulating with +/-15 % productivity and +/-15 % heated-zone cooling rate."""
    from dataclasses import replace

    oils, sors = [], []
    for pi in (0.85, 1.15):
        for cm in (0.85, 1.15):
            q = replace(params, pi_mult=params.pi_mult * pi, cool_mult=params.cool_mult * cm)
            r = physics_summary(q, state, design)["summary"]
            oils.append(r["oil_per_day"])
            sors.append(r["sor"])
    return dict(oil_low=min(oils), oil_high=max(oils), sor_low=min(sors), sor_high=max(sors),
                basis="+/-15% productivity and +/-15% heated-zone cooling rate re-simulated with full physics")


def _design_payload(d: coupling.CycleDesign) -> dict:
    out = d.to_dict()
    out["spm_schedule"] = [dict(day=round(f * d.prod_days, 1), spm=round(s, 2))
                           for f, s in zip(coupling.SPM_KNOT_FRACTIONS, d.spm_knots)]
    return out


def _summary_payload(s: dict) -> dict:
    return {k: v for k, v in s.items() if k != "constraints"} | {"constraints": s["constraints"]}


def explain(base_design, new_design, base_res, new_res) -> list[str]:
    """Plain-language reasons for the recommended changes, computed from the two simulations."""
    b, n = base_res["summary"], new_res["summary"]
    bs, ns = base_res["series"], new_res["series"]
    msgs = []
    # early pump limitation
    prod_idx = [i for i, ph in enumerate(bs["phase"]) if ph == "production"]
    if prod_idx:
        early = prod_idx[: max(1, len(prod_idx) // 6)]
        lim = [bs["limiting"][i] for i in early]
        deliv = np.mean([bs["q_liq_deliv"][i] for i in early])
        liq = np.mean([bs["q_liq"][i] for i in early])
        if any(x and x != "reservoir inflow" for x in lim) and deliv > liq * 1.05:
            main = max(set(lim), key=lim.count)
            msgs.append(f"Early production is pump-limited ({main}): the heated zone can deliver {deliv:.1f} m3/d of liquid "
                        f"but the pump lifts only {liq:.1f} m3/d at {base_design.spm_knots[0]:.1f} SPM. The new schedule starts at "
                        f"{new_design.spm_knots[0]:.1f} SPM" + (f" with the pump set {new_design.pump_depth_m - base_design.pump_depth_m:+.0f} m deeper "
                        "to add submergence and suppress steam flashing." if abs(new_design.pump_depth_m - base_design.pump_depth_m) > 5 else "."))
        late = prod_idx[-max(1, len(prod_idx) // 4):]
        t_late = np.mean([bs["t_avg"][i] for i in late])
        mu_late = np.mean([bs["mu_oil_cp"][i] for i in late])
        fill_late = np.mean([bs["fillage"][i] for i in late])
        if fill_late < 0.75:
            s_old, s_new = base_design.spm_knots[-1], new_design.spm_knots[-1]
            verb = "slows the pump to" if s_new < s_old - 0.05 else ("runs the pump at" if abs(s_new - s_old) <= 0.05 else "sets the pump to")
            msgs.append(f"Late in the cycle the heated zone cools to about {t_late:.0f} C, oil viscosity climbs to about {mu_late:,.0f} cP and pump "
                        f"fillage falls to {fill_late * 100:.0f}% at {s_old:.1f} SPM (fluid pound). The recommendation {verb} "
                        f"{s_new:.1f} SPM" + (" under pump-off control (the unit idles while the annulus refills)" if new_design.poc else "") +
                        f", cutting pound days from {b['pound_days']:.0f} to {n['pound_days']:.0f}.")
    if abs(new_design.steam_t - base_design.steam_t) > 50:
        msgs.append(f"Steam slug {base_design.steam_t:,.0f} t -> {new_design.steam_t:,.0f} t at {new_design.inj_rate_tpd:.0f} t/d; "
                    f"cycle steam-oil ratio {b['sor']:.2f} -> {n['sor']:.2f}.")
    if abs(new_design.soak_days - base_design.soak_days) > 0.5:
        msgs.append(f"Soak {base_design.soak_days:.1f} -> {new_design.soak_days:.1f} days to balance heat distribution against lost production time.")
    if abs(new_design.prod_days - base_design.prod_days) > 5:
        msgs.append(f"Production period {base_design.prod_days:.0f} -> {new_design.prod_days:.0f} days: re-steam when the oil rate falls "
                    f"below the cycle-average rate (marginal-value rule).")
    if abs(new_design.stroke_m - base_design.stroke_m) > 0.1:
        msgs.append(f"Stroke length {base_design.stroke_m / 0.0254:.0f} -> {new_design.stroke_m / 0.0254:.0f} in (peak rod loading {b['max_goodman'] * 100:.0f}% -> "
                    f"{n['max_goodman'] * 100:.0f}% of Goodman allowable).")
    msgs.append(f"Expected cycle failure probability {b['failure_prob'] * 100:.1f}% -> {n['failure_prob'] * 100:.1f}%; "
                f"net value {b['npv_per_day'] / 1000:,.1f} -> {n['npv_per_day'] / 1000:,.1f} thousand INR per day.")
    return msgs


# ----------------------------------------------------------------------------- NSGA-II


def _search_bounds(params, limits):
    lo, hi = _bounds()
    hi = hi.copy()
    hi[-1] = min(hi[-1], params.depth_m - 700.0)
    k = scenarios.DESIGN_KEYS.index("steam_t")
    hi[k] = max(min(hi[k], limits["max_steam_t"]), lo[k] + 1.0)
    return lo, hi


def optimize_nsga2(params: coupling.WellParams, state: coupling.CycleState, base_design: coupling.CycleDesign,
                   pop: int = 80, gens: int = 60, seed: int = 1, limits: dict | None = None, weights: dict | None = None) -> dict:
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import Problem
    from pymoo.optimize import minimize

    t0 = time.time()
    limits, weights = merged_limits(limits), merged_weights(weights)
    surrogate.ensure()
    wf = scenarios.well_features(params, state)
    wvec = np.array([wf[k] for k in scenarios.WELL_KEYS])
    lo, hi = _search_bounds(params, limits)
    base_res = physics_summary(params, state, base_design, record=True)
    ref_npv = base_res["summary"]["npv_per_day"]

    class CSSProblem(Problem):
        def __init__(self):
            super().__init__(n_var=len(lo), n_obj=4, n_ieq_constr=8, xl=lo, xu=hi)

        def _evaluate(self, x, out, *args, **kwargs):
            full = np.hstack([x, np.tile(wvec, (len(x), 1))])
            y = surrogate.predict(full)
            out["F"] = np.column_stack([-y["oil_per_day"], y["sor"], y["failure_prob"], y["energy_per_bbl_kwh"]])
            out["G"] = np.column_stack([
                y["max_goodman"] - limits["max_goodman"],
                (y["max_torque_nm"] - limits["max_torque_frac"] * PU["gearbox_rating_nm"]) / PU["gearbox_rating_nm"],
                (y["max_motor_kw"] - limits["max_motor_kw"]) / PU["motor_rating_kw"],
                limits["min_fillage"] - y["avg_fillage"],
                y["rodfall_ratio"] - limits["max_rodfall_ratio"],
                -y["npv_per_day"] / 1e4,
                y["failure_prob"] - limits["max_risk"],
                (ref_npv + limits["min_npv_gain"] - y["npv_per_day"]) / 1e4,
            ])

    res = minimize(CSSProblem(), NSGA2(pop_size=pop), ("n_gen", gens), seed=seed, verbose=False)
    t_search = time.time() - t0
    xs = res.X if res.X is not None else np.empty((0, len(lo)))
    if xs.ndim == 1:
        xs = xs[None, :]
    points = []
    for x in xs:
        v = dict(zip(scenarios.DESIGN_KEYS, x.tolist()))
        d = scenarios.design_from_vec(v, params.depth_m, poc=True)
        s = physics_summary(params, state, d)["summary"]
        points.append(dict(design=d, summary=s))
    return _finish("NSGA-II on ML surrogate + physics verification", params, state, base_design, base_res, points, limits, weights,
                   dict(pop=pop, generations=gens, surrogate_evaluations=pop * gens, search_seconds=round(t_search, 2), seed=seed), t0)


# ----------------------------------------------------------------------------- Bayesian


def optimize_bayes(params: coupling.WellParams, state: coupling.CycleState, base_design: coupling.CycleDesign,
                   n_trials: int = 120, seed: int = 1, limits: dict | None = None, weights: dict | None = None) -> dict:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    t0 = time.time()
    limits, weights = merged_limits(limits), merged_weights(weights)
    lo, hi = _search_bounds(params, limits)
    base_res = physics_summary(params, state, base_design, record=True)
    ref = base_res["summary"]
    points = []

    def objective_fn(trial):
        v = {k: trial.suggest_float(k, float(lo[i]), float(hi[i])) for i, k in enumerate(scenarios.DESIGN_KEYS)}
        d = scenarios.design_from_vec(v, params.depth_m, poc=True)
        s = physics_summary(params, state, d)["summary"]
        points.append(dict(design=d, summary=s))
        ok, why = feasibility(s, d, limits, ref["npv_per_day"])
        return objective(s, ref, weights) + (0.0 if ok else 5.0 + len(why))

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    base_vec = scenarios.vec_from_design(base_design, params.depth_m)
    study.enqueue_trial({k: float(np.clip(base_vec[k], lo[i], hi[i])) for i, k in enumerate(scenarios.DESIGN_KEYS)})
    study.optimize(objective_fn, n_trials=n_trials)
    return _finish("Bayesian optimisation (Optuna TPE) on physics", params, state, base_design, base_res, points, limits, weights,
                   dict(trials=n_trials, search_seconds=round(time.time() - t0, 2), seed=seed), t0)


# ----------------------------------------------------------------------------- common


def _non_dominated(points):
    """Indices of points on the Pareto front (max oil; min SOR, risk, energy per barrel)."""
    keys = lambda p: (-p["oil_per_day"], p["sor"], p["failure_prob"], p["energy_per_bbl_kwh"])
    idx = []
    for i, a in enumerate(points):
        ka = keys(a)
        dominated = any(all(y <= x for x, y in zip(ka, keys(b))) and any(y < x for x, y in zip(ka, keys(b)))
                        for j, b in enumerate(points) if j != i)
        if not dominated:
            idx.append(i)
    return idx


def _metrics(s: dict) -> dict:
    return dict(oil_per_day=s["oil_per_day"], oil_bopd=s["oil_per_day"] * coupling.M3_TO_BBL, cum_oil_bbl=s["cum_oil_bbl"], sor=s["sor"],
                energy_per_bbl_kwh=s["energy_per_bbl_kwh"], failure_prob=s["failure_prob"], avg_fillage=s["avg_fillage"],
                max_goodman=s["max_goodman"], npv_per_day=s["npv_per_day"], pound_days=s["pound_days"])


def _finish(method, params, state, base_design, base_res, points, limits, weights, info, t0):
    b = base_res["summary"]
    verdicts = []
    reasons_tally: dict[str, int] = {}
    for p in points:
        ok, why = feasibility(p["summary"], p["design"], limits, b["npv_per_day"])
        p["feasible"], p["why"] = ok, why
        p.update(oil_per_day=p["summary"]["oil_per_day"], sor=p["summary"]["sor"], failure_prob=p["summary"]["failure_prob"],
                 npv_per_day=p["summary"]["npv_per_day"], energy_per_bbl_kwh=p["summary"]["energy_per_bbl_kwh"])
        p["J"] = objective(p["summary"], b, weights)
        for r in why:
            key = r.split(" ")[0] + " " + r.split(" ")[1] if len(r.split(" ")) > 1 else r
            reasons_tally[key] = reasons_tally.get(key, 0) + 1
        verdicts.append(ok)
    feas = sorted([p for p in points if p["feasible"]], key=lambda p: p["J"])
    front_idx = set(_non_dominated(feas)) if feas else set()
    best = feas[0] if feas else None
    result = dict(method=method, well_id=params.well_id, info=info, limits=limits, weights=weights, model_version=config.MODEL_VERSION,
                  data_mode=config.DATA_MODE,
                  baseline=dict(design=_design_payload(base_design), summary=_summary_payload(b), series=base_res["series"],
                                metrics=_metrics(b)))
    result["pareto"] = [dict(oil_per_day=p["oil_per_day"], sor=p["sor"], failure_prob=p["failure_prob"], npv_per_day=p["npv_per_day"],
                             energy_per_bbl_kwh=p["energy_per_bbl_kwh"], front=i in front_idx, recommended=p is best, J=p["J"],
                             design=_design_payload(p["design"])) for i, p in enumerate(feas)]
    result["n_candidates"] = len(points)
    result["n_feasible"] = len(feas)
    result["rejected"] = reasons_tally
    # ranked, transparent candidate table (feasible only) - top 8 with uncertainty
    table = []
    seen = set()
    for i, p in enumerate(feas):
        sig = (round(p["oil_per_day"], 2), round(p["sor"], 2), round(p["failure_prob"], 3))
        if sig in seen:
            continue
        seen.add(sig)
        table.append(p)
        if len(table) == 8:
            break
    result["candidates"] = [dict(candidate_id=f"C{i + 1:02d}", design=_design_payload(p["design"]), metrics=_metrics(p["summary"]),
                                 objective=p["J"], feasibility=dict(status="PASS", reasons=[]),
                                 uncertainty=uncertainty(params, state, p["design"]) if i < 6 else None,
                                 constraints=p["summary"]["constraints"]) for i, p in enumerate(table)]
    if best is not None:
        rec_res = physics_summary(params, state, best["design"], record=True)
        result["recommended"] = dict(design=_design_payload(best["design"]), summary=_summary_payload(rec_res["summary"]),
                                     series=rec_res["series"], metrics=_metrics(rec_res["summary"]))
        result["gain"] = compare(b, rec_res["summary"])
        result["gain"]["energy_per_bbl_kwh"] = rec_res["summary"]["energy_per_bbl_kwh"] - b["energy_per_bbl_kwh"]
        result["explanation"] = explain(base_design, best["design"], base_res, rec_res)
        result["factors"] = factor_contributions(b, rec_res["summary"], weights)
    else:
        result["recommended"] = None
        result["gain"] = None
        result["factors"] = []
        result["explanation"] = ["No candidate satisfied every constraint you set; keep current settings or relax a limit "
                                 "(see the rejection reasons)."]
    result["info"]["total_seconds"] = round(time.time() - t0, 2)
    result["disclaimer"] = "Simulated on synthetic data with reduced-order models - not a verified field recommendation."
    return result


def factor_contributions(b: dict, n: dict, w: dict) -> list[dict]:
    """Contribution of each objective term to the improvement (positive = better than baseline)."""
    ref = b
    terms = [
        ("Oil production", w["oil"] * (n["oil_per_day"] - b["oil_per_day"]) / max(ref["oil_per_day"], 1e-6), f"{b['oil_per_day']:.2f} -> {n['oil_per_day']:.2f} m3/d"),
        ("Steam-oil ratio", w["sor"] * (b["sor"] - n["sor"]) / max(ref["sor"], 1e-6), f"{b['sor']:.2f} -> {n['sor']:.2f} t/m3"),
        ("Energy per barrel", w["energy"] * (b["energy_per_bbl_kwh"] - n["energy_per_bbl_kwh"]) / max(ref["energy_per_bbl_kwh"], 1e-6),
         f"{b['energy_per_bbl_kwh']:.0f} -> {n['energy_per_bbl_kwh']:.0f} kWh/bbl"),
        ("Failure risk", w["risk"] * (b["failure_prob"] - n["failure_prob"]) / max(ref["failure_prob"], 1e-3),
         f"{b['failure_prob'] * 100:.1f}% -> {n['failure_prob'] * 100:.1f}%"),
    ]
    return [dict(factor=t, contribution=float(c), detail=d) for t, c, d in terms]


def optimize(params, state, base_design, method: str = "nsga2", **kw) -> dict:
    if method == "bayes":
        return optimize_bayes(params, state, base_design, **kw)
    return optimize_nsga2(params, state, base_design, **kw)


