"""Field-level intelligence built on the per-well twins:

* mission_summary   - headline impact numbers and value-at-stake breakdown for the front page
* maintenance_plan  - predictive maintenance / rig planner (failure forecast + re-steam alignment + job batching)
* run_field_plan    - optimise every well and allocate a shared steam budget by value per tonne
* build_report      - printable field report (HTML, print to PDF)

All economics use config.ECONOMICS and the SYNTHETIC maintenance assumptions listed in config.PROVENANCE."""
from __future__ import annotations

import html
import time
from dataclasses import replace

from . import advisor, config
from .optimize import joint
from .physics import coupling

ECON = config.ECONOMICS
PRICE = ECON["oil_price_inr_per_m3"]
M3_TO_BBL = coupling.M3_TO_BBL


def _lost_oil_m3d(w) -> float:
    tw = w.last_twin or {}
    return float(tw.get("q_oil_deliv") or tw.get("q_oil") or 1.0)


# ------------------------------------------------------------------ maintenance / rig planner
def maintenance_plan(f) -> dict:
    """Which wells need a rig, when, and what a planned job saves compared with running to failure."""
    jobs = []
    for w in f.wells:
        hl = w.health or {}
        rod, pump = hl.get("rod_rul_days"), hl.get("pump_rul_days")
        failed = f._mechanical_fault_open(w) or w.down_until is not None
        reasons, rul = [], None
        if failed:
            reasons.append("failure detected: well is down" if w.down_until is None else "workover in progress")
            rul = 0.0
        if rod is not None and rod < ECON["maint_horizon_days"] and not failed:
            reasons.append(f"rod fatigue: {rod:.0f} days of life left ({(hl.get('rod_damage') or 0) * 100:.0f}% used)")
            rul = rod if rul is None else min(rul, rod)
        if pump is not None and pump < ECON["maint_horizon_days"] and advisor.leak_evidence(w) >= 0.3 and not failed:
            reasons.append(f"pump wear: worn in about {pump:.0f} days (load retention {hl.get('pump_retention') or 0:.2f})")
            rul = pump if rul is None else min(rul, pump)
        if not reasons:
            continue
        proj = w.projection or {}
        steam_in = proj.get("planned_days_left") if w.plant.phase == "production" else None
        lost = _lost_oil_m3d(w)
        day_loss = lost * PRICE
        if failed:
            when, combined = 0.0, False
        elif steam_in is not None and steam_in <= (rul or 0) * 0.95:
            when, combined = float(steam_in), True   # rods are pulled at re-steaming anyway
        else:
            when, combined = max(0.8 * (rul or 0.0), 0.0), False
        job_cost = ECON["workover_cost_inr"] * (1.0 - (ECON["combined_job_saving"] if combined else 0.0))
        planned = job_cost + ECON["workover_days"] * day_loss
        unplanned = ECON["workover_cost_inr"] + ECON["unplanned_down_days"] * day_loss
        jobs.append(dict(
            well_id=w.id, reasons=reasons, rul_days=rul, do_in_days=when, combined_with_resteam=combined, failed=failed,
            planned_cost_inr=planned, run_to_failure_cost_inr=unplanned, saving_inr=0.0 if failed else max(unplanned - planned, 0.0),
            lost_oil_m3d=lost, downtime_days=ECON["workover_days"],
            action="Pull rods and pump now (well is down)" if failed else
                   ("Combine with the re-steam pull" if combined else "Schedule a stand-alone workover"),
        ))
    jobs.sort(key=lambda j: j["do_in_days"])
    # batch jobs that fall within the rig window: one mobilisation instead of several
    batches, cur = [], []
    for j in jobs:
        if cur and j["do_in_days"] - cur[0]["do_in_days"] > ECON["rig_group_window_days"]:
            batches.append(cur)
            cur = []
        cur.append(j)
    if cur:
        batches.append(cur)
    out_batches = []
    for i, b in enumerate(batches):
        mob = ECON["rig_mobilisation_inr"] * (len(b) - 1)
        out_batches.append(dict(batch=i + 1, start_in_days=b[0]["do_in_days"], wells=[j["well_id"] for j in b], mobilisation_saving_inr=mob))
    total_save = sum(j["saving_inr"] for j in jobs) + sum(b["mobilisation_saving_inr"] for b in out_batches)
    return dict(jobs=jobs, batches=out_batches, total_saving_inr=total_save, n_jobs=len(jobs),
                assumptions=dict(unplanned_down_days=ECON["unplanned_down_days"], planned_down_days=ECON["workover_days"],
                                 combined_job_saving=ECON["combined_job_saving"], rig_mobilisation_inr=ECON["rig_mobilisation_inr"],
                                 group_window_days=ECON["rig_group_window_days"], status="SYNTHETIC_ASSUMPTION"))


# ------------------------------------------------------------------ mission control summary
def mission_summary(f, models: dict) -> dict:
    wells = [f.well_summary(w) for w in f.wells]
    pending = [r for r in f.recommendations.values() if r["status"] == "pending"]
    action_year = sum(max(r["gain_inr_per_day"], 0.0) for r in pending) * 365.0
    fail_cost, gap_cost, gap_rows = 0.0, 0.0, []
    for w in f.wells:
        tw = w.last_twin or {}
        lost = _lost_oil_m3d(w)
        fail_cost += (w.health or {}).get("failure_rate_per_year", 0.0) * (ECON["workover_cost_inr"] + ECON["unplanned_down_days"] * lost * PRICE)
        down = f._mechanical_fault_open(w) or w.down_until is not None
        if down:
            gap = lost
            cause = "well down"
        elif w.plant.phase == "production" and tw.get("pump_limited"):
            gap = max(float(tw.get("q_oil_deliv") or 0.0) - float(tw.get("q_oil") or 0.0), 0.0)
            cause = str(tw.get("limiting") or "pump-limited")
        else:
            continue
        gap_cost += gap * PRICE * 365.0
        gap_rows.append(dict(well_id=w.id, cause=cause, oil_gap_m3d=gap, inr_per_year=gap * PRICE * 365.0))
    maint = maintenance_plan(f)
    # steam-efficiency gap: excess steam of wells whose projected SOR is worse than the field's best quartile
    sor_rows = [(w, w.design.steam_t / w.expected_cycle_oil) for w in f.wells if w.expected_cycle_oil and w.expected_cycle_oil > 25.0]
    steam_gap, steam_wells = 0.0, 0
    if len(sor_rows) >= 4:
        bench = sorted(s for _, s in sor_rows)[len(sor_rows) // 4]
        for w, sor in sor_rows:
            if sor > bench * 1.1:
                steam_wells += 1
                steam_gap += (sor - bench) * w.expected_cycle_oil * config.STEAM["fuel_cost_inr_per_tonne"] * 365.0 / max(w.design.total_days, 1.0)
    mapes = [w.calib.get("mape") for w in f.wells if w.calib.get("mape") is not None]
    acc = 1.0 - sum(mapes) / len(mapes) if mapes else None
    # the well an engineer should open first: nearest predicted maintenance, else the biggest pending action, else worst status
    if maint["jobs"]:
        focus = next(s for s in wells if s["id"] == maint["jobs"][0]["well_id"])
    elif pending:
        focus = next(s for s in wells if s["id"] == max(pending, key=lambda r: r["gain_inr_per_day"])["well_id"])
    else:
        focus = max(wells, key=lambda s: ({"down": 3, "critical": 3, "warning": 1, "ok": 0}[s["status"]], s["alerts"]))
    fw = f.by_id[focus["id"]]
    tw = fw.last_twin or {}
    chain = dict(well_id=fw.id, steam_t=fw.design.steam_t, zone_c=focus["t_avg"], viscosity_cp=focus["mu_cp"], fillage=focus["fillage"],
                 pprl_kn=focus["pprl"], goodman=focus["goodman"], oil_m3d=focus["oil"], energy_bbl=focus["energy_bbl"], sor=focus["sor_cycle"],
                 phase=focus["phase"], limiting=str(tw.get("limiting") or ""), deliv=tw.get("q_liq_deliv"), cap=tw.get("cap"))
    return dict(
        wells=len(wells), producing=sum(1 for s in wells if s["phase"] == "production"), oil_m3d=sum(s["oil"] for s in wells),
        oil_bpd=sum(s["oil"] for s in wells) * M3_TO_BBL,
        value_at_stake=[
            dict(key="actions", label="Recoverable by approving the twin's recommendations", inr_per_year=action_year,
                 detail=f"{len(pending)} pending actions", kind="upside"),
            dict(key="leak", label="Oil deliverable but not lifted (pump-limited or down wells)", inr_per_year=gap_cost,
                 detail=f"{len(gap_rows)} well(s)", kind="loss"),
            dict(key="steam", label="Excess steam vs the field's best-quartile steam-oil ratio", inr_per_year=steam_gap,
                 detail=f"{steam_wells} well(s) above the benchmark", kind="loss"),
            dict(key="failure", label="Expected annual failure cost if nothing changes", inr_per_year=fail_cost,
                 detail="rod and pump failures at current loading", kind="risk"),
            dict(key="maint", label="Saved by planning workovers instead of running to failure", inr_per_year=maint["total_saving_inr"],
                 detail=f"{maint['n_jobs']} predicted job(s)", kind="upside"),
        ],
        total_at_stake_inr=action_year + gap_cost + steam_gap + fail_cost + maint["total_saving_inr"],
        leak_rows=sorted(gap_rows, key=lambda r: -r["inr_per_year"]),
        top_actions=sorted(pending, key=lambda r: -r["gain_inr_per_day"])[:3],
        failures_predicted=maint["n_jobs"], maint_jobs=maint["jobs"][:3], twin_accuracy=acc,
        alerts_active=sum(1 for a in f.alerts.values() if a["status"] == "active"),
        models=models, chain=chain, priority_well=focus["id"], sim_time=f.sim_iso(),
    )


# ------------------------------------------------------------------ field-wide optimisation with a steam budget
LEAN_FRACTION = 0.75   # the steam-lean option caps a well at this share of its current steam slug
STEP_T = 5.0           # steam discretisation (tonnes) for the allocation


def allocate_steam(results: list[dict], budget_t: float | None) -> dict:
    """Multiple-choice knapsack: pick ONE option per well (keep current / steam-lean plan / full plan) to maximise the total
    net-value gain while the total steam stays within `budget_t`. Exact dynamic programme on a 5-tonne grid."""
    import math

    opts = []
    for r in results:
        o = [dict(name="baseline", steam_t=r["base_steam_t"], gain=0.0, metrics=None, design=None)]
        o += [dict(name=x["name"], steam_t=x["steam_t"], gain=x["gain_inr_per_year"], metrics=x.get("metrics"), design=x.get("design"))
              for x in r.get("options", [])]
        opts.append(o)
    cost = lambda t: int(math.ceil(t / STEP_T - 1e-9))
    min_total = sum(min(o["steam_t"] for o in row) for row in opts)
    note = None
    if budget_t is None:
        pick = [max(row, key=lambda o: o["gain"]) for row in opts]
    else:
        eff = budget_t
        if budget_t < min_total - 1e-6:
            note = f"budget is below the lowest steam any plan can use ({min_total:,.0f} t); showing the lowest-steam feasible allocation"
            eff = min_total
        B = cost(eff)
        NEG = -1e30
        dp = [0.0] * (B + 1)                      # "at most j units": slack allowed
        choice = []
        for row in opts:
            new_dp, ch = [NEG] * (B + 1), [0] * (B + 1)
            for j in range(B + 1):
                for k, o in enumerate(row):
                    c = cost(o["steam_t"])
                    if c <= j and dp[j - c] > NEG / 2 and dp[j - c] + o["gain"] > new_dp[j]:
                        new_dp[j], ch[j] = dp[j - c] + o["gain"], k
            dp = new_dp
            choice.append(ch)
        j, pick = B, [None] * len(opts)
        for i in range(len(opts) - 1, -1, -1):
            k = choice[i][j]
            pick[i] = opts[i][k]
            j -= cost(pick[i]["steam_t"])
    labels = {"baseline": "keep the current plan", "full": "adopt the full plan", "lean": "adopt the steam-lean plan (less steam)"}
    for r, o in zip(results, pick):
        r["choice"] = o["name"]
        r["chosen"] = o["name"] != "baseline"
        r["plan_steam_t"] = o["steam_t"]
        r["plan"] = o["metrics"]
        r["design"] = o["design"]
        r["gain_inr_per_year"] = o["gain"]
        r["reason"] = labels[o["name"]]
        r["alt"] = [dict(name=x["name"], steam_t=x["steam_t"], gain_inr_per_year=x["gain_inr_per_year"]) for x in r.get("options", [])]
    return dict(total_steam_t=sum(r["plan_steam_t"] for r in results), base_steam_t=sum(r["base_steam_t"] for r in results),
                total_gain_inr_per_year=sum(r["gain_inr_per_year"] for r in results), min_steam_t=min_total, note=note)


def _plan_option(res: dict, name: str) -> dict | None:
    if not res.get("recommended"):
        return None
    d = res["recommended"]["design"]
    return dict(name=name, steam_t=d["steam_t"], gain_inr_per_year=res["gain"]["npv_per_year_inr"], metrics=res["recommended"]["metrics"], design=d)


def run_field_plan(f, store: dict, well_ids: list[str] | None, budget_pct: float, pop: int, gens: int) -> None:
    """Runs in a worker thread; progress is written into `store`. Two searches per well: unconstrained, and steam-lean."""
    ids = well_ids or [w.id for w in f.wells]
    store.update(status="running", progress=0.0, results=[], error=None, started=time.time(), finished=None, well_ids=ids)
    results, skipped = [], []
    saved = f.__dict__.setdefault("_field_plan_results", {})
    saved.clear()
    try:
        for i, wid in enumerate(ids):
            w = f.by_id[wid]
            store["current"] = wid
            if f._mechanical_fault_open(w) or w.down_until is not None:
                skipped.append(dict(well_id=wid, reason="well is down: repair it first (see the maintenance planner)"))
                store["progress"] = (i + 1) / len(ids)
                continue
            params, state, cur = replace(w.twin_p), w.twin.copy(), w.next_design or w.design
            full = joint.optimize(params, state, cur, method="nsga2", pop=pop, gens=gens)
            lean = joint.optimize(params, state, cur, method="nsga2", pop=pop, gens=gens,
                                  limits=dict(max_steam_t=max(cur.steam_t * LEAN_FRACTION, 520.0)))
            options, saved[wid] = [], {}
            for name, res in (("full", full), ("lean", lean)):
                opt = _plan_option(res, name)
                if opt and not any(abs(opt["steam_t"] - o["steam_t"]) < 1.0 and abs(opt["gain_inr_per_year"] - o["gain_inr_per_year"]) < 1e3 for o in options):
                    options.append(opt)
                    saved[wid][name] = res
            results.append(dict(well_id=wid, base_steam_t=cur.steam_t, base=full["baseline"]["metrics"], options=options, feasible=bool(options),
                                baseline_design=full["baseline"]["design"]))
            store["progress"] = (i + 1) / len(ids)
        base_total = sum(r["base_steam_t"] for r in results)
        budget = base_total * budget_pct / 100.0
        alloc = allocate_steam(results, budget)
        store.update(status="done", results=results, skipped=skipped, budget_t=budget, budget_pct=budget_pct, allocation=alloc, finished=time.time())
    except Exception as e:  # surfaced in the UI
        store.update(status="error", error=str(e), finished=time.time())
        raise


def submit_field_plan(f, store: dict, by: str) -> list[dict]:
    """Create one pending 'design' recommendation per adopted well; engineers still approve each one."""
    recs = []
    for r in store.get("results", []):
        if not r.get("chosen"):
            continue
        res = f.__dict__.get("_field_plan_results", {}).get(r["well_id"], {}).get(r["choice"])
        if not res:
            continue
        w = f.by_id[r["well_id"]]
        kind = "steam-lean" if r["choice"] == "lean" else "full"
        rec = f.upsert_recommendation(dict(
            well_id=w.id, type="design", source="optimizer", cycle_no=w.plant.cycle_no,
            title=f"Field plan ({kind}): joint CSS + SRP design for {w.id}", detail=res["explanation"],
            payload=dict(design=res["recommended"]["design"], gain=res["gain"]), gain_inr_per_day=res["gain"]["npv_per_day"]))
        recs.append(rec)
    f.storage.audit(f.sim_iso(), by, "submit_field_plan", None, dict(wells=[r["well_id"] for r in recs], count=len(recs)))
    return recs


# ------------------------------------------------------------------ printable report
def _inr(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e7:
        return f"{sign}Rs {a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}Rs {a / 1e5:.2f} L"
    return f"{sign}Rs {a:,.0f}"


def build_report(f, mission: dict) -> str:
    e = html.escape
    snap = f.snapshot()
    rows = "".join(
        f"<tr><td>{e(w['id'])}</td><td>{e(w['phase'])}</td><td>{w['oil']:.2f}</td><td>{w['fillage'] * 100:.0f}%</td><td>{w['t_avg']:.0f}</td>"
        f"<td>{w['mu_cp']:.0f}</td><td>{'' if w['energy_bbl'] is None else f'{w['energy_bbl']:.0f}'}</td><td>{e(w['status'])}</td></tr>" for w in snap["wells"])
    acts = "".join(f"<li><b>{e(r['title'])}</b> ({_inr(r['gain_inr_per_day'])}/day)<br><small>{e('; '.join(r['detail'][:2]))}</small></li>"
                   for r in sorted((r for r in f.recommendations.values() if r["status"] == "pending"), key=lambda r: -r["gain_inr_per_day"])[:10])
    alerts = "".join(f"<li><b>{e(a['well_id'])}</b> - {e(a['title'])} <small>[{e(a['severity'])}]</small><br><small>{e(a['fix'])}</small></li>"
                     for a in sorted((a for a in f.alerts.values() if a["status"] == "active"), key=lambda a: {"critical": 0, "warning": 1, "info": 2}[a["severity"]])[:14])
    maint = maintenance_plan(f)
    mrows = "".join(f"<tr><td>{e(j['well_id'])}</td><td>{e('; '.join(j['reasons']))}</td><td>{j['do_in_days']:.0f} d</td><td>{e(j['action'])}</td><td>{_inr(j['saving_inr'])}</td></tr>" for j in maint["jobs"])
    stake = "".join(f"<tr><td>{e(v['label'])}</td><td>{_inr(v['inr_per_year'])}/yr</td></tr>" for v in mission["value_at_stake"])
    k = snap["kpis"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Baghewala field report</title><style>
body{{font:13px/1.45 system-ui,Segoe UI,Arial,sans-serif;color:#111;margin:28px auto;max-width:900px;padding:0 16px}}
h1{{font-size:20px;margin:0}}h2{{font-size:14px;margin:22px 0 6px;border-bottom:1px solid #999;padding-bottom:3px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #bbb;padding:3px 6px;text-align:left;font-size:12px}}th{{background:#eee}}
.kpis{{display:flex;gap:10px;flex-wrap:wrap}}.kpi{{border:1px solid #bbb;padding:6px 10px;min-width:120px}}.kpi b{{display:block;font-size:17px}}
.warn{{background:#fff3cd;border:1px solid #d4a017;padding:6px 10px;font-weight:600}}small{{color:#555}}li{{margin-bottom:5px}}
@media print{{.noprint{{display:none}}}}</style></head><body>
<button class="noprint" onclick="window.print()">Print / save as PDF</button>
<h1>Baghewala CSS + SRP digital twin: field report</h1>
<div><small>Simulated time {e(snap['sim_time'])} - model {e(config.MODEL_VERSION)} - SIH26120, Oil India Limited</small></div>
<p class="warn">SYNTHETIC DEMO DATA - NOT LIVE OIL INDIA DATA. Decision support only; nothing here controls equipment.</p>
<h2>Field status</h2><div class="kpis">
<div class="kpi"><small>Oil</small><b>{k['oil_m3d']:.1f} m3/d</b>{k['oil_bpd']:.0f} bbl/d</div>
<div class="kpi"><small>Steam injection</small><b>{k['steam_tpd']:.0f} t/d</b></div>
<div class="kpi"><small>Projected cycle SOR</small><b>{(k['sor'] or 0):.2f}</b>t/m3</div>
<div class="kpi"><small>Active alerts</small><b>{k['alerts_active']}</b>{k['alerts_critical']} critical</div>
<div class="kpi"><small>Twin accuracy (oil rate)</small><b>{'' if mission['twin_accuracy'] is None else f"{mission['twin_accuracy'] * 100:.0f}%"}</b></div></div>
<h2>Value at stake</h2><table>{stake}<tr><th>Total</th><th>{_inr(mission['total_at_stake_inr'])}/yr</th></tr></table>
<h2>Actions awaiting engineer approval</h2><ol>{acts or '<li>None</li>'}</ol>
<h2>Open alerts</h2><ul>{alerts or '<li>None</li>'}</ul>
<h2>Maintenance plan</h2><table><tr><th>Well</th><th>Why</th><th>When</th><th>Action</th><th>Saving vs run-to-failure</th></tr>{mrows or '<tr><td colspan=5>No interventions predicted.</td></tr>'}</table>
<small>Maintenance costs use synthetic assumptions (config.py).</small>
<h2>Wells</h2><table><tr><th>Well</th><th>Phase</th><th>Oil m3/d</th><th>Fillage</th><th>Zone C</th><th>Viscosity cP</th><th>kWh/bbl</th><th>Status</th></tr>{rows}</table>
</body></html>"""
