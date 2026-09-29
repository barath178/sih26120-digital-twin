"""FastAPI application: REST + WebSocket API of the Baghewala CSS/SRP digital twin."""
from __future__ import annotations

import asyncio
import io
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import replace

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field as PField

from . import advisor, config
from .bus import make_bus
from .field import Field
from .ml import calibration, health
from .ml.anomaly import detector
from .ml.dynacard import classifier
from .ml.forecaster import forecaster
from .ml.risk import risk_model
from . import dataio, exports, fleet
from .ml.surrogate import surrogate
from .optimize import joint
from .physics import coupling, fluid, reservoir, srp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("twin")

field: Field | None = None
clients: set[WebSocket] = set()


async def broadcast(msg: dict):
    if not clients:
        return
    data = json.dumps(joint.sanitize(msg))
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks: list[asyncio.Task] = []
    if config.BACKGROUND_START:
        # small hosts: accept connections (health checks) at once and build the twin behind them
        tasks.append(asyncio.create_task(_start_twin(tasks)))
    else:
        await _start_twin(tasks)
    yield
    for t in tasks:
        t.cancel()
    if field is not None:
        if getattr(field, "bus", None) is not None:
            field.bus.close()
        field.storage.close()


async def _start_twin(tasks: list):
    global field
    loop = asyncio.get_running_loop()
    # models: load from disk, train any that are missing (first run only)
    for name, m in (("dynacard CNN", classifier), ("forecaster", forecaster), ("surrogate", surrogate), ("anomaly detector", detector),
                    ("risk classifier", risk_model)):
        if not m.load():
            log.info("training %s (first run) ...", name)
            await loop.run_in_executor(None, m.train)
    if not exports.exists():
        log.info("generating synthetic datasets (first run) ...")
        await loop.run_in_executor(None, exports.generate)
    field = Field()
    if config.PAUSE_WHEN_IDLE:
        field.has_viewers = lambda: bool(clients)
    log.info("bootstrapping field history ...")
    await loop.run_in_executor(None, field.bootstrap, None)
    field.bus = make_bus(loop)
    field.bus.subscribe(field._on_message)
    log.info("history-matching twins ...")
    await loop.run_in_executor(None, field.calibrate_all_sync)
    for w in field.wells:
        field._daily_tasks(w)
    field._recompute_schedule()
    field._evaluate_recommendations()
    log.info("twin ready: %d wells, bus=%s", len(field.wells), field.bus.status)
    tasks.append(asyncio.create_task(field.run(broadcast)))


app = FastAPI(title="Baghewala CSS + SRP Digital Twin", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def F() -> Field:
    if field is None or not field.ready:
        raise HTTPException(503, "twin is starting")
    return field


def well_or_404(wid: str):
    f = F()
    if wid not in f.by_id:
        raise HTTPException(404, f"unknown well {wid}")
    return f.by_id[wid]


def ok(obj):
    return joint.sanitize(obj)


# ----------------------------------------------------------------------------- general


@app.get("/api/health")
def api_health():
    return dict(status="ok" if field and field.ready else "starting", sim_time=field.sim_iso() if field else None)


@app.get("/api/config")
def api_config():
    return ok(dict(
        reservoir=config.RESERVOIR, wellbore=config.WELLBORE, pump=config.PUMP, pumping_unit=config.PUMPING_UNIT,
        rod_tapers=config.ROD_TAPERS, steam=config.STEAM, economics=config.ECONOMICS, api_gravity=config.API_GRAVITY,
        viscosity_table=[dict(t_c=t, mu_cp=m) for t, m in config.VISCOSITY_TABLE],
        walther=dict(A=fluid.WALTHER_A, B=fluid.WALTHER_B, r2=fluid.WALTHER_R2), viscosity_curve=fluid.viscosity_curve(),
        published=["API gravity 17-19", "viscosity 10,000-13,000 cP at 50 C", "reservoir temperature 46-48 C", "Jodhpur Sandstone"],
    ))


@app.get("/api/field")
def api_field():
    f = F()
    return ok(f.snapshot())


@app.post("/api/sim/control")
def api_sim_control(body: dict):
    f = F()
    if "paused" in body:
        f.paused = bool(body["paused"])
    if "hours_per_tick" in body:
        f.hours_per_tick = float(np.clip(float(body["hours_per_tick"]), 0.25, 12.0))
    f.storage.audit(f.sim_iso(), "operator", "sim_control", None, body)
    return dict(paused=f.paused, hours_per_tick=f.hours_per_tick)


@app.get("/api/audit")
def api_audit(limit: int = 200):
    return F().storage.audit_log(limit)


# ----------------------------------------------------------------------------- wells


def _design_dict(d: coupling.CycleDesign):
    out = d.to_dict()
    out["spm_schedule"] = [dict(day=round(fr * d.prod_days, 1), spm=s) for fr, s in zip(coupling.SPM_KNOT_FRACTIONS, d.spm_knots)]
    return out


@app.get("/api/wells/{wid}")
def api_well(wid: str):
    f = F()
    w = well_or_404(wid)
    tw = w.last_twin or {}
    return ok(dict(
        summary=f.well_summary(w), design=_design_dict(w.design), next_design=_design_dict(w.next_design) if w.next_design else None,
        twin_params={k: getattr(w.twin_p, k) for k in ("depth_m", "net_pay_m", "perm_md", "porosity", "p_res_mpa", "t_res_c", "r_e",
                                                         "pi_mult", "u_mult", "cool_mult")},
        twin_state=dict(phase=w.twin.phase, t_phase_d=w.twin.t_phase_d, r_h=w.twin.r_h, t_s=w.twin.t_s, x_bh=w.twin.x_bh,
                        steam_injected_t=w.twin.steam_injected_t, np_cycle=w.twin.np_cycle, np_total=w.twin.np_total_m3,
                        heat_injected_gj=w.twin.heat_injected_j / 1e9, w_rem=w.twin.w_rem, so=tw.get("so")),
        twin_now={k: tw.get(k) for k in ("q_oil", "t_avg", "mu_oil_cp", "q_oil_deliv", "q_liq_deliv", "cap", "q_liq", "fillage", "pwf", "pip",
                                         "limiting", "flash_sev", "viscous_fill", "goodman", "torque", "pprl", "mprl", "fo",
                                         "spm_rodfall", "n_over_n0", "fo_over_skr", "sp", "mu_tub", "t_tub", "wc", "failure_rate",
                                         "runtime", "x_bh", "heat_loss_mw", "pound_sev", "v_r", "v_z", "p_eff")},
        measured=w.last_meas, calibration={k: v for k, v in w.calib.items() if k != "fit_series"}, health=w.health,
        projection={k: v for k, v in (w.projection or {}).items() if k not in ("days", "q_oil")}, anomaly=w.anomaly,
        vfd=dict(enabled=w.vfd_closed_loop, spm=w.vfd_spm, target_fillage=advisor.VFD_TARGET_FILL),
        spm_override=w.spm_override, cycles=w.cycles_log[-10:],
        alerts=[a for a in f.alerts.values() if a["well_id"] == wid and a["status"] != "resolved"],
        recommendations=[r for r in f.recommendations.values() if r["well_id"] == wid and r["status"] == "pending"],
    ))


def _round(obj, nd: int = 3):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


@app.get("/api/wells/{wid}/history")
def api_history(wid: str, n: int = 1200, max_points: int = 600):
    """Last `n` telemetry records (2-hourly by default), thinned to at most `max_points`
    (the newest record is always included)."""
    w = well_or_404(wid)
    h = list(w.history)[-max(1, min(n, 4000)):]
    step = max(1, -(-len(h) // max(max_points, 10)))
    thinned = h[::-1][::step][::-1]
    return ok(_round(thinned))


@app.get("/api/wells/{wid}/card")
def api_card(wid: str):
    w = well_or_404(wid)
    return ok(dict(card=w.card, history=list(w.card_hist)[-120:], classes=srp.CARD_CLASSES, labels=srp.CARD_CLASS_LABELS))


@app.get("/api/wells/{wid}/forecast")
def api_forecast(wid: str, horizon: int = 90):
    f = F()
    w = well_or_404(wid)
    if w.plant.phase != "production" or len(w.daily) < 3:
        return ok(dict(available=False, reason="forecast starts after 3 days of production", history=w.daily))
    d = w.daily
    ml = forecaster.forecast([x["q_oil"] for x in d], [x["q_water"] for x in d], [x["wht"] for x in d], [x["spm"] for x in d],
                             w.design.steam_t, w.design.soak_days, w.design.inj_rate_tpd, w.cycle_start_twin.np_total_m3,
                             horizon_days=horizon)
    proj = health.forward_projection(w.twin_p, w.twin, w.design, spm_override=w.spm_override, max_days=horizon + 60)
    today = len(d)
    return ok(dict(
        available=True, today_prod_day=today, history=d,
        ml=[dict(day=today + x["horizon"] - 1, **{k: x[k] for k in ("p10", "p50", "p90")}) for x in ml],
        physics=[dict(day=today + k, q_oil=q) for k, q in zip(proj["days"][:horizon], proj["q_oil"][:horizon])],
        optimal_resteam_in_days=proj["optimal_resteam_in_days"], planned_days_left=proj["planned_days_left"],
        model=forecaster.metrics.get("model"), sim_time=f.sim_iso(),
    ))


@app.get("/api/wells/{wid}/calibration")
def api_calibration(wid: str):
    w = well_or_404(wid)
    return ok(w.calib)


@app.post("/api/wells/{wid}/calibrate")
async def api_calibrate_now(wid: str):
    f = F()
    w = well_or_404(wid)
    if len(w.daily) < 5:
        raise HTTPException(400, "need at least 5 production days in the current cycle")
    await f._calibrate_async(w)
    f.storage.audit(f.sim_iso(), "engineer", "calibrate", wid, dict(params=w.calib.get("params")))
    return ok({k: v for k, v in w.calib.items()})


class VfdBody(BaseModel):
    enabled: bool
    by: str = "Field Engineer"


@app.post("/api/wells/{wid}/vfd")
def api_vfd(wid: str, body: VfdBody):
    f = F()
    w = well_or_404(wid)
    if body.enabled and w.plant.phase != "production":
        raise HTTPException(400, "closed-loop VFD can only be enabled on a producing well")
    f.set_vfd(w, body.enabled, body.by)
    return dict(enabled=w.vfd_closed_loop, spm=w.vfd_spm)


class FaultBody(BaseModel):
    kind: str
    by: str = "demo"


@app.post("/api/wells/{wid}/fault")
def api_fault(wid: str, body: FaultBody):
    f = F()
    w = well_or_404(wid)
    try:
        f.inject_fault(w, body.kind, body.by)
    except ValueError:
        raise HTTPException(400, "kind must be rod_parted, tv_leak or clear")
    return dict(ok=True)


# ----------------------------------------------------------------------------- what-if simulator


class DesignBody(BaseModel):
    steam_t: float = PField(ge=100, le=4000)
    inj_rate_tpd: float = PField(ge=50, le=400)
    quality: float = PField(0.8, ge=0.3, le=1.0)
    soak_days: float = PField(ge=0, le=30)
    prod_days: float = PField(ge=10, le=400)
    spm_knots: list[float] = PField(min_length=4, max_length=4)
    stroke_m: float = PField(ge=1.0, le=4.5)
    pump_depth_m: float = PField(ge=300, le=1200)
    vfd_auto: bool = False
    poc: bool = False


class SimBody(BaseModel):
    well_id: str
    design: DesignBody
    compare_current: bool = True


@app.post("/api/simulate")
async def api_simulate(body: SimBody):
    f = F()
    w = well_or_404(body.well_id)
    d = joint.design_from_payload(body.design.model_dump())
    if d.pump_depth_m >= w.twin_p.depth_m:
        raise HTTPException(400, f"pump must be set above mid-perforation depth ({w.twin_p.depth_m:.0f} m)")
    for s in d.spm_knots:
        if not (0.5 <= s <= 12):
            raise HTTPException(400, "SPM must be between 0.5 and 12")
    loop = asyncio.get_running_loop()
    params, state, cur = replace(w.twin_p), w.twin.copy(), w.next_design or w.design

    def run():
        res = coupling.simulate_cycle(params, d, state0=state)
        base = coupling.simulate_cycle(params, cur, state0=state) if body.compare_current else None
        return res, base

    res, base = await loop.run_in_executor(None, run)
    out = dict(design=_design_dict(d), summary=res["summary"], series=res["series"])
    if base:
        out["current"] = dict(design=_design_dict(cur), summary=base["summary"], series=base["series"])
        out["delta"] = joint.compare(base["summary"], res["summary"])
    out["sim_time"] = f.sim_iso()
    out["scenario_id"] = f.storage.log_scenario(body.well_id, "what-if", dict(design=body.design.model_dump()),
                                                dict(summary={k: v for k, v in res["summary"].items() if k != "constraints"}),
                                                config.MODEL_VERSION, config.DATA_MODE)
    return ok(out)


# ----------------------------------------------------------------------------- optimiser


class OptBody(BaseModel):
    method: str = "nsga2"
    pop: int = PField(80, ge=20, le=200)
    gens: int = PField(60, ge=10, le=200)
    trials: int = PField(120, ge=20, le=400)
    limits: dict[str, float] | None = None
    weights: dict[str, float] | None = None


LAST_OPT: dict[str, dict] = {}


@app.post("/api/wells/{wid}/optimize")
async def api_optimize(wid: str, body: OptBody):
    f = F()
    w = well_or_404(wid)
    params, state, cur = replace(w.twin_p), w.twin.copy(), w.next_design or w.design
    loop = asyncio.get_running_loop()
    kw = dict(limits=body.limits, weights=body.weights)
    if body.method == "bayes":
        res = await loop.run_in_executor(None, lambda: joint.optimize(params, state, cur, method="bayes", n_trials=body.trials, **kw))
    else:
        res = await loop.run_in_executor(None, lambda: joint.optimize(params, state, cur, method="nsga2", pop=body.pop, gens=body.gens, **kw))
    res["sim_time"] = f.sim_iso()
    res["scenario_id"] = f.storage.log_scenario(
        wid, "optimization", dict(method=body.method, limits=res["limits"], weights=res["weights"], baseline=res["baseline"]["design"]),
        dict(n_candidates=res["n_candidates"], n_feasible=res["n_feasible"], gain=res.get("gain"), rejected=res["rejected"],
             candidates=[dict(id=c["candidate_id"], design=c["design"], metrics=c["metrics"]) for c in res["candidates"]]),
        config.MODEL_VERSION, config.DATA_MODE)
    LAST_OPT[wid] = res
    f.storage.audit(f.sim_iso(), "engineer", "optimize", wid, dict(method=res["method"], gain=res.get("gain"), scenario=res["scenario_id"]))
    return ok(res)


@app.get("/api/scenarios")
def api_scenarios(limit: int = 100):
    return ok(F().storage.scenarios(limit))


@app.get("/api/wells/{wid}/sensitivity")
async def api_sensitivity(wid: str):
    """One-at-a-time sensitivity (tornado) of net value and oil to each decision, +/-15 % of its search range."""
    from .ml import scenarios as sc

    f = F()
    w = well_or_404(wid)
    params, state, cur = replace(w.twin_p), w.twin.copy(), w.next_design or w.design
    loop = asyncio.get_running_loop()

    def run():
        base_vec = sc.vec_from_design(cur, params.depth_m)
        base = coupling.simulate_cycle(params, cur, state0=state, record=False)["summary"]
        rows = []
        for k, (lo, hi) in sc.DESIGN_BOUNDS.items():
            outs = []
            for sign in (-1, 1):
                v = dict(base_vec)
                v[k] = float(min(max(v[k] + sign * 0.15 * (hi - lo), lo), hi))
                d = sc.design_from_vec(v, params.depth_m, poc=cur.poc)
                s = coupling.simulate_cycle(params, d, state0=state, record=False)["summary"]
                outs.append(dict(value=v[k], npv_per_day=s["npv_per_day"], oil_per_day=s["oil_per_day"], sor=s["sor"], failure_prob=s["failure_prob"]))
            rows.append(dict(variable=k, base=base_vec[k], low=outs[0], high=outs[1],
                             swing=abs(outs[1]["npv_per_day"] - outs[0]["npv_per_day"])))
        rows.sort(key=lambda r: -r["swing"])
        return dict(base=dict(npv_per_day=base["npv_per_day"], oil_per_day=base["oil_per_day"], sor=base["sor"], failure_prob=base["failure_prob"]), rows=rows)

    res = await loop.run_in_executor(None, run)
    res["well_id"] = wid
    return ok(res)


class SubmitBody(BaseModel):
    by: str = "Field Engineer"


@app.post("/api/wells/{wid}/optimize/submit")
def api_opt_submit(wid: str, body: SubmitBody):
    f = F()
    w = well_or_404(wid)
    res = LAST_OPT.get(wid)
    if not res or not res.get("recommended"):
        raise HTTPException(400, "run the optimiser first")
    rec = f.upsert_recommendation(dict(
        well_id=wid, type="design", source="optimizer", cycle_no=w.plant.cycle_no,
        title=f"Joint CSS + SRP design for {wid} ({res['method'].split(' ')[0]})",
        detail=res["explanation"], payload=dict(design=res["recommended"]["design"], gain=res["gain"]),
        gain_inr_per_day=res["gain"]["npv_per_day"],
    ))
    f.storage.audit(f.sim_iso(), body.by, "submit_for_approval", wid, dict(recommendation=rec["id"]))
    return ok(rec)


# ----------------------------------------------------------------------------- field-level


@app.get("/api/steam-schedule")
def api_steam():
    f = F()
    f._recompute_schedule()
    return ok(dict(schedule=f.steam_schedule, generators=config.STEAM["generators"], sim_time=f.sim_iso(),
                   wells=[dict(id=w.id, phase=w.plant.phase, due_in=(w.design.prod_days - w.plant.t_phase_d) if w.plant.phase == "production" else None,
                               optimal_in=(w.projection or {}).get("optimal_resteam_in_days"),
                               value_per_t=f._value_per_tonne(w)) for w in f.wells]))


@app.get("/api/alerts")
def api_alerts(status: str | None = None):
    f = F()
    items = sorted(f.alerts.values(), key=lambda a: a["updated"], reverse=True)
    if status:
        items = [a for a in items if a["status"] == status]
    return ok(items)


@app.post("/api/alerts/{aid}/ack")
def api_ack(aid: str, body: SubmitBody):
    f = F()
    a = f.alerts.get(aid)
    if not a:
        raise HTTPException(404, "unknown alert")
    if a["status"] == "active":
        a["status"] = "acknowledged"
        a["ack_by"] = body.by
        f.storage.upsert("alerts", a)
        f.storage.audit(f.sim_iso(), body.by, "acknowledge_alert", a["well_id"], dict(alert=aid, title=a["title"]))
    return ok(a)


@app.get("/api/recommendations")
def api_recs(status: str | None = None):
    f = F()
    items = sorted(f.recommendations.values(), key=lambda r: r["updated"], reverse=True)
    if status:
        items = [r for r in items if r["status"] == status]
    return ok(items)


class DecideBody(BaseModel):
    by: str = "Field Engineer"
    note: str = ""


@app.post("/api/recommendations/{rid}/approve")
def api_approve(rid: str, body: DecideBody):
    f = F()
    if rid not in f.recommendations:
        raise HTTPException(404, "unknown recommendation")
    try:
        return ok(f.decide(rid, True, body.by, body.note))
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/recommendations/{rid}/reject")
def api_reject(rid: str, body: DecideBody):
    f = F()
    if rid not in f.recommendations:
        raise HTTPException(404, "unknown recommendation")
    try:
        return ok(f.decide(rid, False, body.by, body.note))
    except ValueError as e:
        raise HTTPException(409, str(e))


# ----------------------------------------------------------------------------- models / data upload


# ----------------------------------------------------------------------------- mission control, maintenance, field plan, report


def _model_summary() -> dict:
    return dict(
        dynacard_accuracy=classifier.metrics.get("accuracy"), risk_macro_f1=risk_model.metrics.get("macro_f1"),
        forecast_mape=forecaster.metrics.get("mape_p50"), surrogate_r2_oil=surrogate.metrics.get("r2", {}).get("oil_per_day"),
        n_tests=89)


@app.get("/api/mission")
def api_mission():
    return ok(fleet.mission_summary(F(), _model_summary()))


@app.get("/api/maintenance-plan")
def api_maintenance():
    return ok(fleet.maintenance_plan(F()))


@app.post("/api/maintenance/{wid}/schedule")
def api_schedule_job(wid: str, body: dict):
    f = F()
    well_or_404(wid)
    f.storage.audit(f.sim_iso(), str(body.get("by") or "engineer"), "schedule_workover", wid,
                    dict(do_in_days=body.get("do_in_days"), reasons=body.get("reasons")))
    return dict(ok=True)


FIELD_PLAN: dict = dict(status="idle", progress=0.0, results=[])


class FieldPlanBody(BaseModel):
    budget_pct: float = PField(100.0, ge=50, le=130)
    wells: list[str] | None = None
    pop: int = PField(40, ge=20, le=120)
    gens: int = PField(25, ge=8, le=100)


@app.post("/api/field/plan")
def api_field_plan_start(body: FieldPlanBody):
    import threading

    f = F()
    if FIELD_PLAN.get("status") == "running":
        raise HTTPException(409, "a field plan is already running")
    for wid in body.wells or []:
        if wid not in f.by_id:
            raise HTTPException(404, f"unknown well {wid}")
    FIELD_PLAN.clear()
    FIELD_PLAN.update(status="running", progress=0.0, results=[])
    f.storage.audit(f.sim_iso(), "engineer", "field_plan_started", None, dict(budget_pct=body.budget_pct, wells=body.wells))
    threading.Thread(target=fleet.run_field_plan, args=(f, FIELD_PLAN, body.wells, body.budget_pct, body.pop, body.gens), daemon=True).start()
    return dict(status="running")


@app.get("/api/field/plan")
def api_field_plan():
    out = {k: v for k, v in FIELD_PLAN.items() if k != "results"}
    out["results"] = [{k: v for k, v in r.items() if k not in ("baseline_design", "options")} for r in FIELD_PLAN.get("results", [])]
    return ok(out)


@app.post("/api/field/plan/reallocate")
def api_field_plan_realloc(body: dict):
    """Re-run only the steam-budget allocation (instant) after the engineer moves the budget slider."""
    if FIELD_PLAN.get("status") != "done":
        raise HTTPException(400, "run the field plan first")
    pct = float(body.get("budget_pct", 100.0))
    base_total = sum(r["base_steam_t"] for r in FIELD_PLAN["results"])
    alloc = fleet.allocate_steam(FIELD_PLAN["results"], base_total * pct / 100.0)
    FIELD_PLAN.update(allocation=alloc, budget_pct=pct, budget_t=base_total * pct / 100.0)
    return api_field_plan()


@app.post("/api/field/plan/submit")
def api_field_plan_submit(body: SubmitBody):
    f = F()
    if FIELD_PLAN.get("status") != "done":
        raise HTTPException(400, "run the field plan first")
    recs = fleet.submit_field_plan(f, FIELD_PLAN, body.by)
    return ok(dict(submitted=len(recs), recommendations=recs))


@app.get("/api/report", response_class=HTMLResponse)
def api_report():
    f = F()
    return HTMLResponse(fleet.build_report(f, fleet.mission_summary(f, _model_summary())))


@app.get("/api/provenance")
def api_provenance():
    return ok(dict(
        data_mode=config.DATA_MODE, model_version=config.MODEL_VERSION, disclaimer=config.DISCLAIMER, safety=config.SAFETY_WORDING,
        parameters=[dict(parameter=a, value=b, status=c, source=d, note=e) for a, b, c, d, e in config.PROVENANCE],
        sources=[dict(id=k, title=v[0], url=v[1]) for k, v in config.SOURCES.items()],
        status_codes=dict(VERIFIED_FIELD="Directly supplied by an approved OIL dataset or document (none used yet)",
                          PUBLIC_REFERENCE="Published external source used for context, not a live operating limit",
                          HISTORICAL_REFERENCE="Older technical/tender document used as an engineering envelope",
                          SYNTHETIC_ASSUMPTION="Generated for prototype behaviour", USER_CONFIGURED="Entered by developer/operator"),
        data_labels=dict(MEASURED="Directly measured by a sensor (here: synthetic SCADA)", MODELLED="Computed by the twin",
                         SYNTHETIC="Produced by the demo field simulator", **{"USER-ENTERED": "Typed or uploaded by the operator"}),
        non_goals=["No full-field reservoir simulator or commercial simulator replacement",
                   "No command is ever sent to steam equipment, VFDs, pumps or valves",
                   "Synthetic data are not claimed to reproduce actual OIL field behaviour",
                   "Limits shown are user-configured or synthetic, not verified field limits",
                   "No LLM is used as the optimiser; the optimiser is numerical and constraint-based"],
    ))


# ----------------------------------------------------------------------------- synthetic datasets / historical analytics / replay


def _synthetic_csv(name: str):
    import pandas as pd

    path = exports.OUT_DIR / name
    if not path.exists():
        raise HTTPException(404, "synthetic dataset not generated yet - run `python -m app.exports`")
    return pd.read_csv(path)


@app.get("/api/history/summary")
def api_history_summary(well_id: str | None = None):
    import numpy as np

    cyc = _synthetic_csv("synthetic_css_cycles.csv")
    ev = _synthetic_csv("synthetic_failures.csv")
    if well_id:
        cyc, ev = cyc[cyc["well_id"] == well_id], ev[ev["well_id"] == well_id]
    by_type = ev["event_type"].value_counts().to_dict() if len(ev) else {}
    per_well = cyc.groupby("well_id").agg(cycles=("cycle_id", "count"), oil_bbl=("oil_bbl", "sum"), sor=("sor_t_per_m3", "mean"),
                                          energy=("energy_per_bbl_kwh", "mean")).reset_index()
    return ok(dict(
        data_mode=config.DATA_MODE, wells=sorted(cyc["well_id"].unique().tolist()), n_cycles=int(len(cyc)), n_events=int(len(ev)),
        events_by_type=by_type,
        cycles=cyc.replace({np.nan: None}).to_dict("records"),
        events=ev.sort_values("timestamp").tail(400).replace({np.nan: None}).to_dict("records"),
        per_well=per_well.round(2).to_dict("records"),
    ))


@app.get("/api/history/series")
def api_history_series(well_id: str, max_points: int = 1200):
    import numpy as np

    tel = _synthetic_csv("synthetic_srp_telemetry.csv")
    d = tel[tel["well_id"] == well_id]
    if d.empty:
        raise HTTPException(404, "unknown well in synthetic telemetry")
    step = max(1, -(-len(d) // max_points))
    d = d.iloc[::step]
    cols = ["timestamp", "cycle_phase", "reservoir_temperature", "viscosity_est", "oil_rate", "pump_fillage", "spm", "peak_load", "min_load", "sor", "power_kw"]
    return ok(dict(well_id=well_id, rows=d[cols].replace({np.nan: None}).to_dict("records")))


@app.get("/api/data/schema")
def api_data_schema():
    return ok(dict(columns=dataio.schema_json(), phases=sorted(dataio.PHASES), files=exports.FILES))


@app.get("/api/data/files/{name}", response_class=PlainTextResponse)
def api_data_file(name: str):
    if name not in exports.FILES:
        raise HTTPException(404, "unknown dataset")
    return (exports.OUT_DIR / name).read_text()


@app.get("/api/data/sample-upload", response_class=PlainTextResponse)
def api_sample_upload(well_id: str = "BGW-001", rows: int = 600):
    tel = _synthetic_csv("synthetic_srp_telemetry.csv")
    d = tel[tel["well_id"] == well_id].head(rows)
    if d.empty:
        raise HTTPException(404, "unknown well")
    return d.to_csv(index=False)


UPLOADS: dict = {}


@app.post("/api/data/validate")
async def api_data_validate(file: UploadFile = File(...)):
    raw = await file.read()
    v = dataio.validate(raw)
    token = None
    if v["ok"]:
        token = "up-" + str(abs(hash((file.filename, len(raw)))) % 10**8)
        UPLOADS[token] = v["df"]
        if len(UPLOADS) > 6:
            UPLOADS.pop(next(iter(UPLOADS)))
    f = F()
    f.storage.audit(f.sim_iso(), "engineer", "csv_validate", None, dict(file=file.filename, ok=v["ok"], errors=v["errors"][:5], rows=v["rows"]))
    preview = v["df"].head(8).astype(str).to_dict("records") if v["ok"] else []
    return ok(dict(ok=v["ok"], errors=v["errors"], warnings=v["warnings"], rows=v["rows"], wells=v["wells"], missingness=v["missingness"],
                   token=token, preview=preview, data_label="USER-ENTERED"))


@app.post("/api/data/replay")
async def api_data_replay(body: dict):
    df = UPLOADS.get(body.get("token"))
    if df is None:
        raise HTTPException(400, "validate a CSV first (token expired or unknown)")
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, lambda: dataio.replay(df, str(body.get("well_id"))))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ok(res)


@app.get("/api/wells/{wid}/risk")
def api_risk(wid: str):
    """Model-estimated SRP risk for the well's latest measured point (synthetic-trained classifier)."""
    from .ml.risk import feature_vector

    w = well_or_404(wid)
    m, tw = w.last_meas, w.last_twin
    if not m or not tw or w.plant.phase != "production" or m["spm"] < 0.3:
        return ok(dict(available=False, reason="well is not pumping", classes=risk_model.metrics.get("classes")))
    vec = feature_vector(m, tw.get("mu_pump", tw.get("mu_oil_cp", 100.0)) or 100.0, tw.get("t_avg", 100.0), tw.get("spm_rodfall", 30.0) or 30.0)
    r = risk_model.predict(vec)
    r.update(available=True, data_label="MODELLED", note="Model-estimated risk from a classifier trained on synthetic labels; not an operational diagnosis.")
    return ok(r)


@app.get("/api/models")
def api_models():
    f = F()
    return ok(dict(
        risk=risk_model.metrics,
        dynacard=classifier.metrics, forecaster=forecaster.metrics, surrogate=surrogate.metrics, anomaly=detector.metrics,
        calibration=[dict(well_id=w.id, status=w.calib.get("status"), confidence=w.calib.get("confidence"), mape=w.calib.get("mape"),
                          mape_before=w.calib.get("mape_before"), params=w.calib.get("params"), last=w.calib.get("last"),
                          n_days=w.calib.get("n_days")) for w in f.wells],
        physics=dict(walther=dict(A=fluid.WALTHER_A, B=fluid.WALTHER_B, r2=fluid.WALTHER_R2),
                     models=["Walther / ASTM D341 viscosity-temperature", "Ramey + Hasan-Kabir wellbore heat loss",
                             "Marx-Langenheim heated area", "Boberg-Lantz heated-zone cooling (exact V_r, V_z)",
                             "Composite hot/cold radial Darcy inflow with condensate WOR and steam repressurisation",
                             "API RP 11L dimensionless groups + damped wave-equation rod dynamics", "Gibbs surface-to-downhole diagnostic",
                             "Modified Goodman diagram, gearbox torque, rod-fall limit", "Miner's-rule rod fatigue"]),
        bus=f.bus.status if f.bus else None, telemetry_rows=f.storage.telemetry_count(),
    ))


SAMPLE_COLUMNS = ["prod_day", "oil_rate_m3d", "water_rate_m3d", "wellhead_temp_c", "spm"]


@app.get("/api/sample-csv", response_class=PlainTextResponse)
def api_sample_csv(well_id: str = "BGW-001"):
    w = well_or_404(well_id)
    lines = [",".join(SAMPLE_COLUMNS)]
    for d in w.daily:
        lines.append(f"{d['day']},{d['q_oil']:.3f},{d['q_water']:.3f},{d['wht']:.2f},{d['spm']:.2f}")
    return "\n".join(lines) + "\n"


@app.post("/api/wells/{wid}/upload")
async def api_upload(wid: str, file: UploadFile = File(...), steam_t: float = Form(...), inj_rate_tpd: float = Form(...),
                     soak_days: float = Form(...), quality: float = Form(0.8), stroke_m: float = Form(2.54),
                     pump_depth_m: float | None = Form(None), cum_oil_before_m3: float | None = Form(None),
                     apply: bool = Form(False), by: str = Form("Field Engineer")):
    import pandas as pd

    f = F()
    w = well_or_404(wid)
    raw = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"could not parse CSV: {e}")
    df.columns = [c.strip().lower() for c in df.columns]
    need = {"oil_rate_m3d", "water_rate_m3d", "spm"}
    if not need.issubset(df.columns):
        raise HTTPException(400, f"CSV needs columns {sorted(need)} (optional: prod_day, wellhead_temp_c); got {list(df.columns)}")
    if "prod_day" in df.columns:
        df = df.sort_values("prod_day")
    df = df.dropna(subset=list(need))
    if len(df) < 5:
        raise HTTPException(400, "need at least 5 daily rows")
    if (df[list(need)] < 0).any().any():
        raise HTTPException(400, "rates and SPM must be non-negative")
    if "wellhead_temp_c" not in df.columns:
        df["wellhead_temp_c"] = np.nan
    design = coupling.CycleDesign(steam_t=steam_t, inj_rate_tpd=inj_rate_tpd, soak_days=soak_days, quality=quality,
                                  prod_days=float(len(df) + 30), spm_knots=[float(df["spm"].iloc[0])] * 4, stroke_m=stroke_m,
                                  pump_depth_m=pump_depth_m or w.design.pump_depth_m)
    state0 = coupling.CycleState(np_total_m3=cum_oil_before_m3 if cum_oil_before_m3 is not None else w.cycle_start_twin.np_total_m3)
    records = []
    for _, r in df.iterrows():
        wht = float(r["wellhead_temp_c"]) if not np.isnan(r["wellhead_temp_c"]) else None
        records.append(dict(spm=float(r["spm"]), q_oil=float(r["oil_rate_m3d"]), q_water=float(r["water_rate_m3d"]), wht=wht))
    have_t = all(r["wht"] is not None for r in records)
    if not have_t:
        # without wellhead temperatures the fit uses rates only
        _, _, t_model, _ = calibration.replay(w.twin_p, design, state0, [r["spm"] for r in records])
        for r, t in zip(records, t_model):
            r["wht"] = float(t)
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, lambda: calibration.calibrate(replace(w.twin_p), design, state0, records))
    res.pop("end_state", None)
    if apply and res.get("status") == "calibrated":
        w.twin_p = replace(w.twin_p, **res["params"])
        w.calib.update(params=res["params"], status="calibrated (uploaded data)", confidence=res["confidence"], mape=res["mape_recent"],
                       last=f.sim_iso())
    f.storage.audit(f.sim_iso(), by, "upload_calibrate", wid, dict(rows=len(records), applied=apply, params=res.get("params")))
    res["rows"] = len(records)
    res["applied"] = bool(apply and res.get("status") == "calibrated")
    return ok(res)


# ----------------------------------------------------------------------------- physics explorer


@app.get("/api/physics/viscosity")
def api_visc():
    return ok(dict(curve=fluid.viscosity_curve(), table=[dict(t_c=t, mu_cp=m) for t, m in config.VISCOSITY_TABLE],
                   walther=dict(A=fluid.WALTHER_A, B=fluid.WALTHER_B, r2=fluid.WALTHER_R2)))


@app.get("/api/physics/boberg-lantz")
def api_bl():
    th = np.logspace(-3, 2, 60)
    return ok([dict(theta=float(t), v_r=reservoir.v_r(float(t)), v_z=reservoir.v_z(float(4 * t))) for t in th])


# ----------------------------------------------------------------------------- websocket


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        if field and field.ready:
            await ws.send_text(json.dumps(joint.sanitize(field.snapshot())))
        while True:
            await ws.receive_text()  # keep-alive / ignore client messages
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


# ----------------------------------------------------------------------------- built frontend (single-command demo)

_DIST = config.BASE_DIR.parent / "frontend" / "dist"
if _DIST.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")
