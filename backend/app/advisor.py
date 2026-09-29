"""Decision support: alerts (with root cause + fix), explainable recommendations, the
closed-loop VFD controller and the actions taken when an engineer approves something.

Nothing here changes a well by itself: recommendations sit in `pending` until approved, and
the VFD loop only runs on wells where an engineer has switched it on."""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from . import config
from .physics import coupling, fluid, srp

if TYPE_CHECKING:
    from .field import Field, WellRuntime

PU = config.PUMPING_UNIT
ECON = config.ECONOMICS
VFD_TARGET_FILL = 0.875
PRICE = ECON["oil_price_inr_per_m3"]


# ----------------------------------------------------------------------------- what-if at the current state


def instant_eval(w: "WellRuntime", spm: float, design: coupling.CycleDesign | None = None) -> dict | None:
    """Evaluate the twin's current state at a different SPM (no state change)."""
    if w.twin.phase != "production":
        return None
    d = design or w.design
    op, ev = coupling.evaluate_production(w.twin_p, w.twin, d, spm)
    if ev["loads"] is None:
        return None
    out = dict(q_oil=op["q_oil"], q_water=op["q_water"], steam_rate_tpd=0.0, motor_kw=ev["motor_kw"], failure_rate=ev["failure_rate"])
    return dict(spm=spm, q_oil=op["q_oil"], q_liq=op["q_liq"], fillage=op["fillage"], runtime=ev["runtime"], goodman=ev["loads"]["goodman"],
                pprl=ev["loads"]["pprl"], mprl=ev["loads"]["mprl"], torque=ev["loads"]["torque"], motor_kw=ev["motor_kw"],
                failure_rate=ev["failure_rate"], spm_rodfall=ev["loads"]["spm_rodfall"], pound=ev["pound"], limiting=op["limiting"],
                cash=coupling.daily_economics(out, 1.0, d), t_avg=op["t_avg"], mu=op["mu_oil_cp"], deliv=op["q_liq_deliv"], cap=op["cap"])


def _feasible(e: dict) -> bool:
    return e["goodman"] <= 0.9 and e["torque"] <= 0.95 * PU["gearbox_rating_nm"] and e["spm"] <= coupling.RODFALL_LIMIT * e["spm_rodfall"]


def best_spm(w: "WellRuntime") -> tuple[dict | None, dict | None]:
    cur_spm = w.commanded_spm(0.0) or 0.0
    cur = instant_eval(w, cur_spm) if cur_spm > 0 else None
    best = None
    for s in np.arange(PU["spm_min"], PU["spm_max"] + 1e-9, 0.25):
        e = instant_eval(w, float(s))
        if e is None or not _feasible(e):
            continue
        if best is None or e["cash"] > best["cash"] + 1e-6:
            best = e
    return cur, best


def leak_evidence(w: "WellRuntime") -> float:
    """Share of the last 10 downhole cards the CNN diagnosed as travelling-valve / plunger leak."""
    last = list(w.card_hist)[-10:]
    return sum(1 for c in last if c["diagnosis"] == "tv_leak") / len(last) if last else 0.0


# ----------------------------------------------------------------------------- VFD controller


def vfd_controller(w: "WellRuntime", meas: dict, tw: dict) -> float:
    """Keep the pump filled to ~85-90 % of its displacement. Uses the measured card fillage and
    POC run-time; limited to equipment constraints predicted by the twin. Max 0.25 SPM per step."""
    spm = w.vfd_spm or meas.get("spm") or w.design.spm_at(w.plant.t_phase_d)
    fills = list(w.fill_window)[-6:] or [meas.get("fillage", 0.0)]
    use = float(np.mean(fills)) * max(meas.get("runtime", 1.0), 0.05)  # fraction of continuous displacement used
    hi = PU["spm_max"]
    if tw.get("spm_rodfall"):
        hi = min(hi, coupling.RODFALL_LIMIT * tw["spm_rodfall"])
    if tw.get("goodman", 0.0) > 0.9 or tw.get("torque", 0.0) > 0.95 * PU["gearbox_rating_nm"]:
        hi = min(hi, spm)
    if use >= 0.95 and tw.get("q_liq_deliv", 0.0) > tw.get("q_liq", 0.0) * 1.05:
        target = spm + 0.25                         # pump-limited: speed up
    else:
        target = spm * use / VFD_TARGET_FILL         # proportional correction toward the fillage target
    target = float(np.clip(target, spm - 0.25, spm + 0.25))
    return float(np.clip(target, PU["spm_min"], max(hi, PU["spm_min"])))


# ----------------------------------------------------------------------------- alerts


def _fmt(x, nd=1):
    return f"{x:,.{nd}f}"


def well_alerts(field: "Field", w: "WellRuntime") -> list[dict]:
    alerts = []
    tw = w.last_twin or {}
    m = w.last_meas or {}
    card = w.card or {}
    diag = card.get("diagnosis")
    conf = card.get("confidence", 0.0)
    producing = w.plant.phase == "production"
    running = producing and (m.get("spm", 0.0) > 0.3)
    fill_avg = float(np.mean(w.fill_window)) if len(w.fill_window) else m.get("fillage", 0.0)
    proj = w.projection or {}
    hl = w.health or {}
    recent_card = card and (field.sim_day - card.get("t", -99)) < 1.0

    if w.down_until is not None:
        alerts.append(dict(type="workover", severity="info", title="Workover in progress",
                           root_cause="Well shut in for rod/pump workover approved by engineer.",
                           fix=f"Unit restarts automatically on {field.sim_iso(w.down_until)}.", value=w.down_until))
        return alerts

    # parted rods ---------------------------------------------------------
    pprl_twin = tw.get("pprl", 0.0) / 1000.0
    load_drop = running and pprl_twin > 5 and m.get("pprl_kn", 0.0) < 0.55 * pprl_twin
    if producing and ((recent_card and diag == "rod_parted" and conf > 0.5) or load_drop):
        secs = srp.rod_string(w.design.pump_depth_m)
        _, w_rf = srp.rod_weights(secs, tw.get("rho", 960.0) or 960.0)
        est_depth = min(max(m.get("pprl_kn", 0.0) * 1000.0 / max(w_rf, 1.0), 0.0), 1.0) * w.design.pump_depth_m
        alerts.append(dict(
            type="rod_parted", severity="critical", title="Parted rod string",
            root_cause=(f"Polished-rod load fell to {_fmt(m.get('pprl_kn', 0.0))} kN (twin expects {_fmt(pprl_twin)} kN) and the downhole "
                        f"card has collapsed (CNN: {card.get('label', 'n/a')}, {conf * 100:.0f}%). Load equals the weight of about "
                        f"{est_depth:,.0f} m of rods, so the break is near that depth. Liquid rate dropped to {_fmt(m.get('liquid_rate_m3d', 0.0))} m3/d."),
            fix="Stop the unit, call a pulling rig and fish the rods; inspect the pump in the same job. Approve the workover recommendation.",
            value=est_depth))

    parted = any(a["type"] == "rod_parted" for a in alerts)  # twin's own detection, not plant truth

    # fluid pound ---------------------------------------------------------
    if running and m.get("runtime", 1.0) >= 0.99 and not parted and (
            fill_avg < 0.65 or (recent_card and diag == "fluid_pound" and conf > 0.6)):
        new_spm = max(PU["spm_min"], (m.get("spm", 0.0)) * max(fill_avg, 0.05) / VFD_TARGET_FILL)
        cap_eff = tw.get("cap", 0.0)
        alerts.append(dict(
            type="fluid_pound", severity="warning", title="Fluid pound - incomplete pump fillage",
            root_cause=(f"Heated zone has cooled to {_fmt(tw.get('t_avg', 0.0), 0)} C, so oil viscosity is {_fmt(tw.get('mu_oil_cp', 0.0), 0)} cP. "
                        f"Reservoir inflow is {_fmt(tw.get('q_liq_deliv', 0.0))} m3/d but the pump displaces {_fmt(cap_eff)} m3/d at "
                        f"{_fmt(m.get('spm', 0.0))} SPM, so the barrel is only {fill_avg * 100:.0f}% full and the plunger hits the fluid "
                        f"on every downstroke (shock loads on rods and pump)."),
            fix=(f"Reduce speed to about {_fmt(new_spm)} SPM to restore ~{VFD_TARGET_FILL * 100:.0f}% fillage, or enable the closed-loop VFD with "
                 f"pump-off control." + (f" Economic re-steam point is in {proj['optimal_resteam_in_days']} days." if proj.get("optimal_resteam_in_days") is not None else "")),
            value=fill_avg))

    # steam flashing / gas interference ------------------------------------
    flash = tw.get("flash_sev", 0.0)
    if running and not parted and (flash > 0.35 or (recent_card and diag == "gas_interference" and conf > 0.6)):
        pip = max(tw.get("pip", 0.5) or 0.5, 0.1)
        t_prod = tw.get("t_avg", 0.0)
        tsat = fluid.t_sat(pip)
        p_req = fluid.p_sat(t_prod + 5.0) if t_prod > 100 else pip
        extra_m = max((p_req - pip) * 1e6 / (max(tw.get("rho", 900.0), 500.0) * config.G), 0.0)
        alerts.append(dict(
            type="gas_interference", severity="warning", title="Steam flashing at pump intake (gas interference)",
            root_cause=(f"Produced fluid at {_fmt(t_prod, 0)} C is hotter than the saturation temperature ({_fmt(tsat, 0)} C) at the pump "
                        f"intake pressure of {_fmt(pip, 2)} MPa, so water flashes to steam inside the barrel and the pump gas-locks "
                        f"(CNN card: {card.get('label', 'n/a')})."),
            fix=(f"Keep more fluid above the pump (about {extra_m:,.0f} m more submergence) - lower the pump at the next pull or hold a "
                 f"higher fluid level until the zone cools; vent casing gas to the flow line."),
            value=flash))

    # rod overload ----------------------------------------------------------
    g = tw.get("goodman", 0.0)
    tq = tw.get("torque", 0.0)
    if running and (g > 0.9 or tq > 0.9 * PU["gearbox_rating_nm"]):
        mu_t = tw.get("mu_tub", 0.0)
        alerts.append(dict(
            type="overload", severity="critical" if g > 1.0 or tq > PU["gearbox_rating_nm"] else "warning",
            title="Rod / gearbox overload",
            root_cause=(f"Peak rod loading is {g * 100:.0f}% of the modified-Goodman allowable and peak gearbox torque is "
                        f"{tq / 1000:.1f} kN.m ({tq / PU['gearbox_rating_nm'] * 100:.0f}% of rating). Tubing fluid viscosity of {_fmt(mu_t, 0)} cP "
                        f"adds viscous rod drag on top of the {tw.get('fo', 0.0) / 1000:.1f} kN fluid load."),
            fix="Reduce SPM or stroke length, re-check counterbalance from the latest card, or re-steam to thin the oil.", value=g))

    # rod float -------------------------------------------------------------
    if running and (m.get("mprl_kn", 1.0) < 0 or tw.get("rodfloat", 0.0) > 0.3):
        lim = tw.get("spm_rodfall", 0.0)
        alerts.append(dict(
            type="rod_float", severity="warning", title="Rod float (viscous drag on downstroke)",
            root_cause=(f"Minimum polished-rod load {_fmt(m.get('mprl_kn', 0.0))} kN: rods cannot fall fast enough through "
                        f"{_fmt(tw.get('mu_tub', 0.0), 0)} cP fluid (free-fall limit about {_fmt(lim)} SPM)."),
            fix=f"Keep speed below {_fmt(coupling.RODFALL_LIMIT * lim)} SPM or use a slower downstroke (VFD) to avoid carrier-bar separation.",
            value=m.get("mprl_kn", 0.0)))

    # pump wear (needs two independent pieces of evidence: load-retention trend + CNN leak diagnoses)
    rul_p = hl.get("pump_rul_days")
    tv_frac = leak_evidence(w)
    if producing and not parted and ((rul_p is not None and rul_p < 45 and tv_frac >= 0.3) or (recent_card and diag == "tv_leak" and conf > 0.6)):
        nxt = proj.get("planned_days_left")
        if rul_p is not None and rul_p <= 0.5:
            title = "Pump worn beyond workover threshold"
        elif rul_p is not None:
            title = f"Pump failure predicted in {rul_p:.0f} days"
        else:
            title = "Pump leakage detected"
        alerts.append(dict(
            type="pump_wear", severity="critical" if (rul_p is not None and rul_p < 10) else "warning",
            title=title,
            root_cause=(f"Upstroke load retention on the downhole card is {hl.get('pump_retention') or 0:.2f} (healthy about 0.9)"
                        + (f" and falling {abs(hl.get('pump_retention_slope') or 0) * 100:.2f}%/day" if hl.get("pump_retention_slope") else "")
                        + ": fluid is slipping past a worn travelling valve or plunger."),
            fix=("Plan a pump change" + (f"; the next steam cycle starts in about {nxt:.0f} days, when rods are pulled anyway" if nxt else "")
                 + f" - combining the jobs avoids a separate INR {ECON['workover_cost_inr'] / 1e5:.0f} lakh workover."),
            value=rul_p))

    # standing valve leak ---------------------------------------------------
    sv_votes = sum(1 for c in list(w.card_hist)[-4:] if c["diagnosis"] == "sv_leak")
    if producing and recent_card and diag == "sv_leak" and conf > 0.6 and sv_votes >= 2:
        alerts.append(dict(type="sv_leak", severity="warning", title="Standing valve leak",
                           root_cause="Downhole card unloads slowly on the downstroke: the standing valve is not holding pressure.",
                           fix="Check with a valve test; replace the standing valve at the next pull.", value=conf))

    # rod fatigue -----------------------------------------------------------
    rul_r = hl.get("rod_rul_days")
    if producing and not parted and rul_r is not None and rul_r < 60:
        alerts.append(dict(
            type="rod_fatigue", severity="critical" if rul_r < 20 else "warning", title=f"Rod failure predicted in {rul_r:.0f} days",
            root_cause=(f"Cumulative fatigue damage (Miner's rule) is {hl.get('rod_damage', 0) * 100:.1f}% of rod life and grows "
                        f"{hl.get('rod_damage_rate_per_day', 0) * 100:.3f}%/day at {g * 100:.0f}% Goodman loading"
                        + (" with fluid-pound shocks" if (tw.get("pound_sev", 0) or 0) > 0.05 else "") + "."),
            fix="Lower the stress range now (slower SPM / pump-off control) and replace the worn taper at the next pull.", value=rul_r))

    # anomaly ---------------------------------------------------------------
    if w.anomaly.get("consecutive", 0) >= 6 and not any(a["type"] in ("rod_parted", "pump_wear") for a in alerts):
        z = w.anomaly.get("z", {})
        top = sorted(z.items(), key=lambda kv: -abs(kv[1]))[:2]
        names = {"liquid_rate": "liquid rate", "pprl": "peak rod load", "mprl": "min rod load", "motor_kw": "motor power",
                 "wht": "wellhead temperature", "fillage": "pump fillage"}
        desc = ", ".join(f"{names[k]} {'above' if v > 0 else 'below'} twin ({v:+.1f} sd)" for k, v in top)
        alerts.append(dict(type="anomaly", severity="warning", title="Unexplained deviation from the twin",
                           root_cause=f"Isolation-forest anomaly on twin residuals for {w.anomaly['consecutive']} consecutive readings: {desc}.",
                           fix="Verify sensors and run a well test; inspect for a developing mechanical fault.", value=w.anomaly.get("score")))

    # calibration -------------------------------------------------------------
    if w.calib.get("status") == "calibrated" and (w.calib.get("n_days") or 0) >= 14 and w.calib.get("confidence", 1.0) < 0.4:
        alerts.append(dict(type="calibration", severity="info", title="Twin confidence low",
                           root_cause=f"Recent oil-rate error is {(w.calib.get('mape') or 0) * 100:.0f}% after history matching.",
                           fix="Check well-test data quality; upload recent test data to recalibrate.", value=w.calib.get("confidence")))

    # cycle end ---------------------------------------------------------------
    k = proj.get("optimal_resteam_in_days")
    if producing and k is not None and k <= 10 and not parted:
        alerts.append(dict(type="cycle_end", severity="info", title=f"Re-steam {w.id} in {k} days",
                           root_cause=(f"Projected daily net value falls below the cycle-average value in {k} days (marginal-value rule); "
                                       f"planned end of cycle is in {proj.get('planned_days_left', 0):.0f} days."),
                           fix="Confirm a slot on the steam calendar; approve the re-steam recommendation.", value=k))
    return alerts


# ----------------------------------------------------------------------------- recommendations


def well_recommendations(field: "Field", w: "WellRuntime") -> list[dict]:
    recs = []
    if w.plant.phase != "production" or w.down_until is not None:
        return recs
    base = dict(well_id=w.id, cycle_no=w.plant.cycle_no, source="rules")

    parted = field._mechanical_fault_open(w)
    if parted or ((w.health.get("pump_rul_days") is not None) and w.health["pump_rul_days"] < 10 and leak_evidence(w) >= 0.3):
        what = "parted rods" if parted else "worn pump"
        lost = (w.last_twin or {}).get("q_oil_deliv", 0.0)
        recs.append(dict(base, type="workover", title=f"Workover {w.id}: repair {what}",
                         detail=[f"Diagnosis: {what} (dynacard CNN + load trend).",
                                 f"Well is losing about {lost:.1f} m3/d of oil deliverability until repaired.",
                                 f"Estimated job: INR {ECON['workover_cost_inr'] / 1e5:.0f} lakh, {ECON['workover_days']:.0f} days."],
                         payload=dict(kind=what), gain_inr_per_day=lost * PRICE))
        return recs

    if not w.vfd_closed_loop:
        vfd_rec = None
        fill_days = [d["fillage"] for d in w.daily[-3:]]
        if len(fill_days) == 3 and max(fill_days) < 0.65 and not w.design.poc:
            cur_e = instant_eval(w, w.commanded_spm(field.sim_day) or 1.0)
            vfd_design = replace(w.design, poc=True)
            tgt = coupling.vfd_target_spm(w.twin_p, w.twin, vfd_design, cur_e["spm"] if cur_e else 3.0, VFD_TARGET_FILL)
            new_e = instant_eval(w, tgt, vfd_design)
            if cur_e and new_e and new_e["cash"] > cur_e["cash"]:
                vfd_rec = dict(base, type="vfd", title=f"Enable closed-loop VFD + pump-off control on {w.id}",
                               detail=[f"Heated zone at {cur_e['t_avg']:.0f} C, oil viscosity {cur_e['mu']:,.0f} cP: pump fillage has stayed below "
                                       f"65% for 3 days (last {fill_days[-1] * 100:.0f}%) at {cur_e['spm']:.1f} SPM - fluid pound on every stroke.",
                                       f"The controller holds fillage near {VFD_TARGET_FILL * 100:.0f}% (about {tgt:.1f} SPM now) and idles the unit while "
                                       f"the annulus refills; it follows the well as the zone keeps cooling. Engineer can switch it off at any time.",
                                       f"Oil {cur_e['q_oil']:.2f} -> {new_e['q_oil']:.2f} m3/d, failure rate {cur_e['failure_rate']:.2f} -> "
                                       f"{new_e['failure_rate']:.2f} /yr, motor {cur_e['motor_kw']:.1f} -> {new_e['motor_kw']:.1f} kW."],
                               payload=dict(target_fillage=VFD_TARGET_FILL), gain_inr_per_day=new_e["cash"] - cur_e["cash"])
                recs.append(vfd_rec)
        cur, best = best_spm(w)
        if (cur and best and abs(best["spm"] - cur["spm"]) >= 0.4 and best["cash"] - cur["cash"] > 300
                and not (vfd_rec and best["spm"] < cur["spm"])):
            why = []
            if cur["limiting"] != "reservoir inflow" and best["spm"] > cur["spm"]:
                why.append(f"Pump is the bottleneck ({cur['limiting']}): reservoir can deliver {cur['deliv']:.1f} m3/d of liquid, the pump lifts "
                           f"{cur['q_liq']:.1f} m3/d at {cur['spm']:.1f} SPM.")
            if cur["fillage"] < 0.75 and best["spm"] < cur["spm"]:
                why.append(f"Heated zone at {cur['t_avg']:.0f} C, viscosity {cur['mu']:,.0f} cP: fillage has dropped to {cur['fillage'] * 100:.0f}%.")
            why.append(f"At {best['spm']:.1f} SPM: oil {cur['q_oil']:.2f} -> {best['q_oil']:.2f} m3/d, fillage {cur['fillage'] * 100:.0f}% -> "
                       f"{best['fillage'] * 100:.0f}%, rod loading {cur['goodman'] * 100:.0f}% -> {best['goodman'] * 100:.0f}% Goodman, "
                       f"failure rate {cur['failure_rate']:.2f} -> {best['failure_rate']:.2f} /yr.")
            recs.append(dict(base, type="spm", title=f"{'Increase' if best['spm'] > cur['spm'] else 'Reduce'} {w.id} to {best['spm']:.1f} SPM",
                             detail=why, payload=dict(spm=best["spm"], from_spm=cur["spm"]),
                             gain_inr_per_day=best["cash"] - cur["cash"]))

    proj = w.projection or {}
    k_opt, left = proj.get("optimal_resteam_in_days"), proj.get("planned_days_left")
    if k_opt is not None and left is not None and proj.get("avg_cash_rate_opt") is not None and proj.get("avg_cash_rate") is not None:
        gain = proj["avg_cash_rate_opt"] - proj["avg_cash_rate"]
        # Only shortening the cycle is proposed. "Extend the cycle" would mostly reflect the model's decline assumptions
        # rather than evidence, so the economic optimum is shown for information on the twin page instead.
        if left - k_opt >= 7 and gain > 200:
            recs.append(dict(base, type="resteam", title=f"Advance {w.id} cycle: re-steam in {k_opt} days",
                             detail=[f"Twin projection: daily net value drops below the cycle-average value in {k_opt} days "
                                     f"(planned: {left:.0f} days).",
                                     f"Cycle-average net value {proj['avg_cash_rate'] / 1000:,.1f} -> {proj['avg_cash_rate_opt'] / 1000:,.1f} "
                                     f"thousand INR/day.",
                                     "Steam calendar will be updated; generator capacity is checked by the field scheduler."],
                             payload=dict(in_days=int(k_opt), planned_days_left=left), gain_inr_per_day=gain))
    return recs


def still_relevant(field: "Field", w: "WellRuntime", r: dict) -> bool:
    if r.get("source") == "optimizer":
        return True
    if r.get("cycle_no") is not None and r["cycle_no"] != w.plant.cycle_no:
        return False
    if w.plant.phase != "production":
        return False
    if r["type"] == "spm":
        return not w.vfd_closed_loop and not field._mechanical_fault_open(w)
    if r["type"] == "vfd":
        return not w.vfd_closed_loop
    if r["type"] == "workover":
        return field._mechanical_fault_open(w) or (w.health.get("pump_rul_days") is not None and w.health["pump_rul_days"] < 15)
    return True


def apply_recommendation(field: "Field", w: "WellRuntime", r: dict):
    t = r["type"]
    p = r["payload"]
    if t == "spm":
        w.spm_override = float(p["spm"])
    elif t == "vfd":
        field.set_vfd(w, True, r.get("decided_by", "engineer"))
    elif t == "resteam":
        if w.next_design is None:
            w.next_design = replace(w.design)
        w.design = replace(w.design, prod_days=max(w.plant.t_phase_d + float(p["in_days"]), w.plant.t_phase_d + 0.1))
        w.plant.due_for_steam = w.plant.t_phase_d >= w.design.prod_days
        w.twin.due_for_steam = w.plant.due_for_steam
    elif t == "workover":
        w.down_until = field.sim_day + ECON["workover_days"]
        w.true.rod_parted = False
        w.true.tv_leak = 0.0
        w.tv_leak_rate = 0.0
        w.plant.rod_damage = 0.0
        w.twin.rod_damage = 0.0
        w.rod_damage_meas = 0.0
        w.card_hist.clear()
        w.damage_hist.clear()
    elif t == "design":
        from .optimize.joint import design_from_payload

        new = design_from_payload(p["design"])
        w.next_design = new
        if w.plant.phase == "production":
            # SPM schedule and pump-off control can change today; steam, soak, stroke and pump depth at the next cycle
            w.design = replace(w.design, spm_knots=list(new.spm_knots), poc=new.poc)
            w.spm_override = None
    else:
        raise ValueError(t)
