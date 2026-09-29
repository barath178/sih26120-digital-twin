"""Field runtime: synthetic Baghewala field (plant) + digital twin, wired through the bus.

Plant side  : each well has hidden "true" parameters and equipment condition. It is advanced
              with the coupled physics model and emits noisy SCADA telemetry and surface
              dynamometer cards on the bus.
Twin side   : subscribes to the bus, runs its own (calibrated) model in lock-step with the
              commanded settings, diagnoses cards, scores anomalies, history-matches its
              parameters, predicts cycle end and failures, raises alerts and proposes
              recommendations that only take effect after engineer approval.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta

import numpy as np

from . import advisor, config
from .bus import TOPIC_ROOT, make_bus
from .ml import anomaly, calibration, health
from .ml.anomaly import detector
from .ml.dynacard import classifier
from .physics import coupling, fluid, sensors, srp
from .storage import Storage

log = logging.getLogger(__name__)
PU = config.PUMPING_UNIT
ECON = config.ECONOMICS

# ----------------------------------------------------------------------------- field layout
# (id, x, y, pad) - schematic lease layout, coordinates are illustrative (not surveyed)
LAYOUT = [
    ("BGW-001", 18, 22, "Pad A"), ("BGW-002", 32, 16, "Pad A"), ("BGW-003", 46, 24, "Pad A"),
    ("BGW-004", 62, 18, "Pad B"), ("BGW-005", 78, 26, "Pad B"), ("BGW-006", 22, 58, "Pad C"),
    ("BGW-007", 38, 66, "Pad C"), ("BGW-008", 56, 60, "Pad D"), ("BGW-009", 72, 70, "Pad D"),
    ("BGW-010", 86, 56, "Pad D"),
]
GENERATOR_POS = {"SG-1": (48, 42), "SG-2": (66, 44)}


class WellRuntime:
    def __init__(self, wid: str, x: float, y: float, pad: str, true_p: coupling.WellParams, design: coupling.CycleDesign,
                 state: coupling.CycleState):
        self.id = wid
        self.x, self.y, self.pad = x, y, pad
        self.true = true_p
        self.twin_p = coupling.WellParams(well_id=wid, depth_m=true_p.depth_m, net_pay_m=true_p.net_pay_m, perm_md=true_p.perm_md,
                                          porosity=true_p.porosity, p_res_mpa=true_p.p_res_mpa)
        self.design = design
        self.next_design: coupling.CycleDesign | None = None
        self.plant = state
        self.twin = state.copy()
        self.cycle_start_twin = state.copy()
        self.spm_override: float | None = None
        self.vfd_closed_loop = False
        self.vfd_spm: float | None = None
        self._poc_before_vfd = design.poc
        self.generator: str | None = None
        self.tv_leak_rate = 0.0
        self.down_until: float | None = None
        self.history: deque = deque(maxlen=4000)
        self.daily: list[dict] = []
        self._day_acc: list = []
        self._day_idx: int | None = None
        self.card: dict | None = None
        self.card_hist: deque = deque(maxlen=300)
        self.card_version = 0
        self.calib: dict = dict(status="not yet calibrated", confidence=0.0, params=dict(pi_mult=1.0, u_mult=1.0, cool_mult=1.0),
                                history=[])
        self.last_meas: dict | None = None
        self.last_true: dict | None = None
        self.last_twin: dict | None = None
        self.anomaly = dict(score=0.0, anomalous=False, z={}, consecutive=0)
        self.fill_window: deque = deque(maxlen=12)
        self.damage_hist: deque = deque(maxlen=60)
        self.projection: dict | None = None
        self.health: dict = {}
        self.cycles_log: list[dict] = []
        self.cum_oil_meas = state.np_total_m3
        self.cycle_oil_meas = 0.0
        self.cycle_water_meas = 0.0
        self.cycle_steam_meas = 0.0
        self.cash_today = 0.0
        self.calibrating = False
        self.rod_damage_meas = 0.0
        self.expected_cycle_oil: float | None = None
        self._expected_for_cycle: int | None = None

    # ------------------------------------------------------------------ control
    def commanded_spm(self, sim_day: float) -> float | None:
        if self.plant.phase != "production":
            return None
        if self.down_until is not None and sim_day < self.down_until:
            return 0.0
        if self.vfd_closed_loop and self.vfd_spm is not None:
            return self.vfd_spm
        if self.spm_override is not None:
            return self.spm_override
        return self.design.spm_at(self.plant.t_phase_d)



def _make_field(seed: int) -> list[WellRuntime]:
    rng = np.random.default_rng(seed)
    wells = []
    # starting position in the cycle: (phase, days into that phase)
    starts = [("production", 38), ("production", 118), ("soak", 2), ("production", 70), ("production", 146),
              ("production", 6), ("production", 131), ("production", 95), ("injection", 2.5), ("production", 55)]
    for i, (wid, x, y, pad) in enumerate(LAYOUT):
        depth = float(rng.uniform(1060, 1290))
        true = coupling.WellParams(
            well_id=wid, depth_m=depth, net_pay_m=float(rng.uniform(14.0, 21.0)), perm_md=float(rng.uniform(650, 950)),
            pi_mult=float(rng.uniform(0.75, 1.35)), u_mult=float(rng.uniform(0.85, 1.35)), cool_mult=float(rng.uniform(0.85, 1.2)),
        )
        design = coupling.CycleDesign(
            steam_t=float(rng.choice([1000.0, 1200.0, 1400.0])), inj_rate_tpd=200.0, soak_days=5.0,
            prod_days=float(rng.choice([150.0, 160.0, 180.0])), spm_knots=[float(rng.choice([3.0, 3.5, 4.0]))] * 4,
            stroke_m=2.54, pump_depth_m=round(depth - 60.0),
        )
        state = coupling.CycleState(cycle_no=int(rng.integers(3, 7)), np_total_m3=float(rng.uniform(900, 3500)),
                                    rod_damage=float(rng.uniform(0.1, 0.5)))
        w = WellRuntime(wid, x, y, pad, true, design, state)
        w._start = starts[i]  # type: ignore[attr-defined]
        wells.append(w)
    # scripted equipment conditions for the demo
    by = {w.id: w for w in wells}
    by["BGW-004"].true.tv_leak = 0.22            # slightly worn pump; wear accelerates from "now" (see _seed_scenarios)
    by["BGW-008"].design.spm_knots = [4.5] * 4
    by["BGW-006"].design.pump_depth_m = round(by["BGW-006"].true.depth_m - 190.0)  # shallow pump -> steam flashing
    by["BGW-006"].design.spm_knots = [5.0] * 4
    for w in wells:
        w.twin.rod_damage = w.plant.rod_damage
        w.rod_damage_meas = w.plant.rod_damage  # from equipment records (rod-string age / previous loading)
        w.cycle_start_twin = w.twin.copy()
    return wells


class Field:
    def __init__(self, seed: int = config.RANDOM_SEED):
        self.rng = np.random.default_rng(seed)
        self.wells = _make_field(seed)
        self.by_id = {w.id: w for w in self.wells}
        self.t0 = datetime.now().replace(minute=0, second=0, microsecond=0)
        self.sim_day = 0.0
        self.hours_per_tick = config.SIM_HOURS_PER_TICK
        self.paused = False
        self.generators = {g["id"]: dict(g, busy_with=None) for g in config.STEAM["generators"]}
        self.recommendations: dict[str, dict] = {}
        self.alerts: dict[str, dict] = {}
        self._open_alerts: dict[str, str] = {}
        self._alert_seq = 0
        self.storage = Storage()
        self.bus = None
        self.steam_schedule: dict = {}
        self.subscribers: set = set()
        self._card_task: asyncio.Task | None = None
        self._last_card_day = -1.0
        self._ticks = 0
        self.tick_ms = 0.0
        self.ready = False
        self.has_viewers = lambda: True   # main.py replaces this when TWIN_PAUSE_WHEN_IDLE=1
        self._pending_rows: list = []
        self._bootstrapping = False
        self._pending_resid: list = []

    # ------------------------------------------------------------------ time helpers
    @property
    def sim_time(self) -> datetime:
        return self.t0 + timedelta(days=self.sim_day)

    def sim_iso(self, day: float | None = None) -> str:
        return (self.t0 + timedelta(days=self.sim_day if day is None else day)).isoformat(timespec="minutes")

    # ------------------------------------------------------------------ bootstrap
    def bootstrap(self, loop: asyncio.AbstractEventLoop | None):
        """Fast-forward every well from the start of its current cycle to its starting point,
        through the same plant -> bus -> twin path used live, so history and daily records exist."""
        from .bus import InProcBus

        self.bus = InProcBus()
        self.bus.subscribe(self._on_message)
        self._bootstrapping = True
        dt = self.hours_per_tick / 24.0
        order = {"injection": 0, "soak": 1, "production": 2}
        for w in self.wells:
            phase, days = w._start  # type: ignore[attr-defined]
            # elapsed cycle time at the starting point, so the local clock ends at sim_day ~ 0
            elapsed = days + (w.design.inj_days if order[phase] >= 1 else 0.0) + (w.design.soak_days if order[phase] >= 2 else 0.0)
            self.sim_day = -math.ceil(elapsed / dt) * dt
            self._begin_cycle(w, first=True)
            guard = 0
            while guard < 20000 and self.sim_day < -1e-9:
                guard += 1
                self._step_well(w, dt, self.sim_day)
                self.sim_day += dt
        self.sim_day = 0.0
        self._bootstrapping = False
        self._seed_scenarios()
        self.storage.write_telemetry(self._pending_rows)
        self._pending_rows = []
        if loop is not None:
            self.bus = make_bus(loop)
            self.bus.subscribe(self._on_message)
        for w in self.wells:
            if w.plant.phase == "injection":
                gid = self._free_generator()
                if gid:
                    self.generators[gid]["busy_with"] = w.id
                    w.generator = gid
        self._generate_cards_sync()
        for w in self.wells:
            self._daily_tasks(w)
        self._recompute_schedule()
        self._evaluate_alerts()
        self.ready = True

    def _seed_scenarios(self):
        """Equipment conditions for the live demonstration, set at 'now' so they unfold on screen:
        BGW-004 pump wear accelerates; BGW-008 rod string is ~4 weeks from fatigue failure at its current loading."""
        self.by_id["BGW-004"].tv_leak_rate = 0.012
        w8 = self.by_id["BGW-008"]
        (t0, d0), (t1, d1) = w8.damage_hist[0], w8.damage_hist[-1]
        rate = max((d1 - d0) / max(t1 - t0, 1e-6), 1e-5)
        target = 1.0 - 28.0 * rate
        w8.plant.rod_damage = w8.twin.rod_damage = w8.rod_damage_meas = target
        w8.damage_hist = deque([(t, target - (t1 - t) * rate) for t, _ in w8.damage_hist], maxlen=60)

    # ------------------------------------------------------------------ cycle management
    def _begin_cycle(self, w: WellRuntime, first: bool = False):
        if not first:
            if w.plant.np_cycle > 0:
                w.cycles_log.append(dict(cycle_no=w.plant.cycle_no, oil_m3=w.cycle_oil_meas, water_m3=w.cycle_water_meas,
                                         steam_t=w.cycle_steam_meas, sor=w.cycle_steam_meas / max(w.cycle_oil_meas, 1e-6),
                                         days=w.plant.day_in_cycle, ended=self.sim_iso()))
            if w.next_design is not None:
                w.design = w.next_design
                w.next_design = None
                self.storage.audit(self.sim_iso(), "system", "design_applied", w.id, w.design.to_dict())
            coupling.start_new_cycle(w.plant)
            coupling.start_new_cycle(w.twin)
            w.twin.np_total_m3 = w.cum_oil_meas  # measured cumulative production
            w.spm_override = None
        w.cycle_start_twin = w.twin.copy()
        w.daily = []
        w._day_acc = []
        w._day_idx = None
        w.cycle_oil_meas = w.cycle_water_meas = w.cycle_steam_meas = 0.0
        w.projection = None

    def _free_generator(self) -> str | None:
        for gid, g in self.generators.items():
            if g["busy_with"] is None:
                return gid
        return None

    def _start_injections(self):
        due = [w for w in self.wells if w.plant.phase == "production" and w.plant.due_for_steam and not self._mechanical_fault_open(w)]
        due.sort(key=lambda w: (-(w.plant.t_phase_d - w.design.prod_days), -self._value_per_tonne(w)))
        for w in due:
            gid = self._free_generator()
            if gid is None:
                return
            self.generators[gid]["busy_with"] = w.id
            w.generator = gid
            self._begin_cycle(w)
            self.storage.audit(self.sim_iso(), "system", "steam_injection_started", w.id, dict(generator=gid, steam_t=w.design.steam_t))

    def _value_per_tonne(self, w: WellRuntime) -> float:
        # value of steam: cycle-average net value per tonne injected for the current design (twin projection)
        if w.projection and w.projection.get("avg_cash_rate"):
            return w.projection["avg_cash_rate"] * w.design.total_days / max(w.design.steam_t, 1.0)
        return 0.0

    # ------------------------------------------------------------------ plant step
    def _step_well(self, w: WellRuntime, dt: float, sim_day: float):
        spm = w.commanded_spm(sim_day)
        if w.plant.phase == "production" and spm and spm > 0 and not w.true.rod_parted:
            w.true.tv_leak = min(w.true.tv_leak + w.tv_leak_rate * dt, 0.95)
        out = coupling.advance(w.true, w.plant, w.design, dt, spm)
        if w.plant.rod_damage >= 1.0 and not w.true.rod_parted:
            w.true.rod_parted = True  # fatigue failure in the "real" well
        w.last_true = out
        meas = sensors.measure(out, self.rng)
        payload = dict(well_id=w.id, t=self.sim_day, dt_d=dt, phase=out["phase"], cycle_no=out["cycle_no"],
                       day_in_cycle=out["day_in_cycle"], t_phase_d=out["t_phase_d"], spm_cmd=spm, meas=meas)
        self.bus.publish(f"{TOPIC_ROOT}/{w.id}/telemetry", payload)

    # ------------------------------------------------------------------ twin side
    def _on_message(self, topic: str, payload: dict):
        parts = topic.split("/")
        if len(parts) < 3 or parts[1] not in self.by_id:
            return
        w = self.by_id[parts[1]]
        if parts[2] == "telemetry":
            self._ingest_telemetry(w, payload)
        elif parts[2] == "card":
            self._ingest_card(w, payload)

    def _ingest_telemetry(self, w: WellRuntime, pl: dict):
        dt = pl["dt_d"]
        spm = pl["spm_cmd"]
        # twin runs in lock-step with the commanded settings
        pending_resid = None
        out_tw = coupling.advance(w.twin_p, w.twin, w.design, dt, spm)
        w.last_twin = out_tw
        meas = pl["meas"]
        w.last_meas = meas
        tw_ideal = sensors.ideal(out_tw)
        t = pl["t"]
        # anomaly scoring on residuals while pumping
        if pl["phase"] == "production" and meas["spm"] > 0.3 and tw_ideal["spm"] > 0.3:
            if not self._bootstrapping:  # scored in one batch at the end of the tick
                pending_resid = anomaly.residual_vector(meas, tw_ideal)
        else:
            w.anomaly = dict(score=0.0, anomalous=False, z={}, consecutive=0)
        if pl["phase"] == "production" and meas["spm"] > 0.3:
            w.fill_window.append(meas["fillage"])
        # closed-loop VFD (only when the engineer has enabled it)
        if w.vfd_closed_loop and pl["phase"] == "production":
            w.vfd_spm = advisor.vfd_controller(w, meas, out_tw)
        # cumulative bookkeeping (measured)
        w.cum_oil_meas += meas["oil_rate_m3d"] * dt
        w.cycle_oil_meas += meas["oil_rate_m3d"] * dt
        w.cycle_water_meas += meas["water_rate_m3d"] * dt
        w.cycle_steam_meas += meas["steam_rate_tpd"] * dt
        w.cash_today = coupling.daily_economics(dict(q_oil=meas["oil_rate_m3d"], q_water=meas["water_rate_m3d"],
                                                     steam_rate_tpd=meas["steam_rate_tpd"], motor_kw=meas["motor_kw"],
                                                     failure_rate=out_tw.get("failure_rate", 0.0)), 1.0, w.design)
        # rod fatigue from the *measured* load-cell range (Miner's rule), as a field system would do
        if pl["phase"] == "production" and meas["spm"] > 0.3 and not self._mechanical_fault_open(w):
            secs = srp.rod_string(w.design.pump_depth_m)
            g = srp.string_goodman(secs, meas["pprl_kn"] * 1000.0, meas["mprl_kn"] * 1000.0, out_tw.get("rho", 960.0) or 960.0)
            fl = meas["fillage"]
            pound = 2.0 * np.sqrt(max(1.0 - fl, 0.0) * min(fl, 1.0)) * min(1.0, meas["spm"] / 6.0) if fl < 0.95 else 0.0
            w.rod_damage_meas += meas["spm"] * 1440.0 * dt * meas.get("runtime", 1.0) / coupling.cycles_to_failure(g * (1.0 + 0.5 * pound))
        w.damage_hist.append((t, w.rod_damage_meas))
        # daily production records for calibration / forecasting
        if pl["phase"] == "production":
            day = int(pl["t_phase_d"] + 1e-9)
            if w._day_idx is not None and day != w._day_idx and w._day_acc:
                a = np.mean(w._day_acc, axis=0)
                w.daily.append(dict(day=w._day_idx, sim_day=t, spm=float(a[0]), q_oil=float(a[1]), q_water=float(a[2]), wht=float(a[3]),
                                    fillage=float(a[4]), valid=bool(min(r[5] for r in w._day_acc))))
                w._day_acc = []
            w._day_idx = day
            w._day_acc.append([spm or 0.0, meas["oil_rate_m3d"], meas["water_rate_m3d"], meas["wht_c"], meas["fillage"],
                               0.0 if self._mechanical_fault_open(w) else 1.0])
        rec = dict(
            t=t, ts=self.sim_iso(t), phase=pl["phase"], cycle_no=pl["cycle_no"], day=pl["day_in_cycle"], m=meas,
            tw=dict(q_oil=out_tw.get("q_oil", 0.0), q_water=out_tw.get("q_water", 0.0), q_liq=out_tw.get("q_liq", 0.0),
                    q_liq_deliv=out_tw.get("q_liq_deliv", 0.0), cap=out_tw.get("cap", 0.0), fillage=out_tw.get("fillage", 0.0),
                    pprl_kn=out_tw.get("pprl", 0.0) / 1000.0, mprl_kn=out_tw.get("mprl", 0.0) / 1000.0, goodman=out_tw.get("goodman", 0.0),
                    t_avg=out_tw.get("t_avg", config.T_RESERVOIR_C), mu_cp=out_tw.get("mu_oil_cp", fluid.oil_viscosity_cp(config.T_RESERVOIR_C)),
                    r_h=out_tw.get("r_h", 0.0), wht_c=tw_ideal["wht_c"], motor_kw=out_tw.get("motor_kw", 0.0),
                    sor_cum=w.cycle_steam_meas / w.cycle_oil_meas if w.cycle_oil_meas > 25.0 else None,
                    x_bh=out_tw.get("x_bh", 0.0), heat_loss_mw=out_tw.get("heat_loss_mw", 0.0),
                    limiting=out_tw.get("limiting", ""), flash_sev=out_tw.get("flash_sev", 0.0)),
            anomaly=round(w.anomaly["score"], 3),
        )
        w.history.append(rec)
        if pending_resid is not None:
            self._pending_resid.append((w, pending_resid, rec))
        self._pending_rows.append((self.sim_iso(t), t, w.id, pl["phase"], _json(meas)))

    def _ingest_card(self, w: WellRuntime, pl: dict):
        tw = w.last_twin or {}
        mu_est = tw.get("mu_tub", 100.0) or 100.0
        rho = tw.get("rho", 960.0) or 960.0
        fo_est = tw.get("fo") or srp.fo_from_pressures(pl["pump_depth"], max(tw.get("pip", 1.0) or 1.0, 0.1), rho)
        spec = srp.CardSpec(stroke=pl["stroke"], spm=pl["spm"], pump_depth=pl["pump_depth"], fo=fo_est, rho_liq=rho)
        from .ml.dynacard import card_to_image

        img, dpos, dload = card_to_image(np.asarray(pl["surface_pos"]), np.asarray(pl["surface_load"]), spec, mu_est, fo_est)
        probs = classifier.predict(img[None])[0]
        order = np.argsort(probs)[::-1]
        cls = srp.CARD_CLASSES[int(order[0])]
        metrics = srp.card_metrics(dpos, dload, fo_est, pl["stroke"])
        tq = srp.torque_and_power_from_card(np.asarray(pl["surface_pos"]), np.asarray(pl["surface_load"]), pl["stroke"], pl["spm"])
        w.card = dict(
            t=pl["t"], ts=self.sim_iso(pl["t"]), spm=pl["spm"], stroke=pl["stroke"], pump_depth=pl["pump_depth"],
            surface=dict(pos=_r(pl["surface_pos"]), load_kn=_r(np.asarray(pl["surface_load"]) / 1000.0)),
            downhole=dict(pos=_r(dpos), load_kn=_r(dload / 1000.0)),
            fo_kn=fo_est / 1000.0, diagnosis=cls, label=srp.CARD_CLASS_LABELS[cls], confidence=float(probs[order[0]]),
            probabilities={srp.CARD_CLASSES[i]: float(probs[i]) for i in range(len(probs))},
            metrics=metrics, peak_torque_knm=tq["peak_torque_nm"] / 1000.0, card_motor_kw=tq["motor_kw"],
            pprl_kn=float(np.max(pl["surface_load"]) / 1000.0), mprl_kn=float(np.min(pl["surface_load"]) / 1000.0),
        )
        w.card_version += 1
        w.card_hist.append(dict(t=pl["t"], diagnosis=cls, confidence=float(probs[order[0]]),
                                retention=metrics["upstroke_load_retention"], fillage_est=metrics["fillage_est"]))

    def _mechanical_fault_open(self, w: WellRuntime) -> bool:
        """True while the twin has an open parted-rod alert: those days reflect equipment, not reservoir."""
        return f"{w.id}:rod_parted" in self._open_alerts

    def _score_anomalies(self):
        if not self._pending_resid:
            return
        items, self._pending_resid = self._pending_resid, []
        scores = detector.score_batch(np.array([r for _, r, _ in items]))
        for (w, _, rec), a in zip(items, scores):
            a["consecutive"] = w.anomaly.get("consecutive", 0) + 1 if a["anomalous"] else 0
            w.anomaly = a
            rec["anomaly"] = round(a["score"], 3)

    # ------------------------------------------------------------------ cards
    def _card_specs(self):
        specs, wells = [], []
        for w in self.wells:
            o = w.last_true
            if not o or o["phase"] != "production" or not o.get("spm") or o["spm"] <= 0.3 or not o.get("fo"):
                continue
            gas = min(1.0, o.get("flash_sev", 0.0) * 1.6) if o.get("flash_sev", 0.0) > 0.12 else 0.0
            fill = o["fillage"]
            if gas > 0 and fill > 0.9:
                fill = 1.0 - 0.5 * o.get("flash_sev", 0.0)
            specs.append(srp.CardSpec(stroke=w.design.stroke_m, spm=o["spm"], pump_depth=w.design.pump_depth_m, fo=o["fo"],
                                      rho_liq=o.get("rho", 960.0), mu_tubing_cp=o.get("mu_tub", 100.0), mu_pump_cp=o.get("mu_pump", 100.0),
                                      fill=float(np.clip(fill, 0.05, 1.0)), gas=gas, tv_leak=w.true.tv_leak,
                                      parted_frac=0.55 if w.true.rod_parted else 0.0, noise=0.01))
            wells.append(w)
        return specs, wells

    def _publish_cards(self, specs, wells, res, t):
        for j, w in enumerate(wells):
            self.bus.publish(f"{TOPIC_ROOT}/{w.id}/card", dict(
                well_id=w.id, t=t, spm=specs[j].spm, stroke=specs[j].stroke, pump_depth=specs[j].pump_depth,
                surface_pos=res["surface_pos"][j].tolist(), surface_load=res["surface_load"][j].tolist()))

    def _generate_cards_sync(self):
        specs, wells = self._card_specs()
        if specs:
            res = srp.simulate_cards(specs, seed=int(self.rng.integers(1 << 30)))
            self._publish_cards(specs, wells, res, self.sim_day)

    async def _generate_cards_async(self):
        specs, wells = self._card_specs()
        if not specs:
            return
        t = self.sim_day
        loop = asyncio.get_running_loop()
        seed = int(self.rng.integers(1 << 30))
        res = await loop.run_in_executor(None, lambda: srp.simulate_cards(specs, seed=seed))
        self._publish_cards(specs, wells, res, t)

    # ------------------------------------------------------------------ calibration
    def calibration_job(self, w: WellRuntime):
        """Returns a closure that runs off the event loop and the data it needs (snapshotted)."""
        snap = dict(params=replace(w.twin_p), design=w.design, state0=w.cycle_start_twin.copy(), records=list(w.daily),
                    cycle_no=w.plant.cycle_no)

        def job():
            return calibration.calibrate(snap["params"], snap["design"], snap["state0"], snap["records"])

        return job, snap

    def apply_calibration(self, w: WellRuntime, res: dict, snap: dict):
        if res.get("status") != "calibrated" or snap["cycle_no"] != w.plant.cycle_no:
            w.calib.update(status=res.get("status", "skipped"))
            return
        w.twin_p = replace(w.twin_p, **res["params"])
        # re-sync the twin state: replayed state, then advance to the plant's current time
        st = res["end_state"]
        st.rod_damage = w.twin.rod_damage
        st.np_total_m3 = w.twin.np_total_m3 + 0.0
        guard = 0
        while st.phase == w.plant.phase and st.t_phase_d < w.plant.t_phase_d - 1e-6 and guard < 200:
            guard += 1
            step = min(self.hours_per_tick / 24.0, w.plant.t_phase_d - st.t_phase_d)
            coupling.advance(w.twin_p, st, w.design, step, w.commanded_spm(self.sim_day))
        if st.phase == w.plant.phase:
            st.np_total_m3 = w.twin.np_total_m3
            w.twin = st
        entry = dict(t=self.sim_day, ts=self.sim_iso(), params=res["params"], mape=res["mape_recent"], confidence=res["confidence"],
                     mape_before=res["mape_before"], n_days=res["n_days"])
        hist = w.calib.get("history", [])[-40:] + [entry]
        w.calib = dict(status="calibrated", confidence=res["confidence"], params=res["params"], mape=res["mape_recent"],
                       mape_before=res["mape_before"], r2=res["r2_recent"], n_days=res["n_days"], fit_series=res["fit_series"],
                       last=self.sim_iso(), history=hist)

    async def _calibrate_async(self, w: WellRuntime):
        if w.calibrating or len(w.daily) < 5:
            return
        w.calibrating = True
        try:
            job, snap = self.calibration_job(w)
            res = await asyncio.get_running_loop().run_in_executor(None, job)
            self.apply_calibration(w, res, snap)
        except Exception:
            log.exception("calibration failed for %s", w.id)
        finally:
            w.calibrating = False

    def calibrate_all_sync(self):
        for w in self.wells:
            if len(w.daily) >= 5:
                job, snap = self.calibration_job(w)
                self.apply_calibration(w, job(), snap)

    # ------------------------------------------------------------------ daily tasks
    def _daily_tasks(self, w: WellRuntime):
        # cycle-end / re-steam projection from the twin
        if w.plant.phase == "production" and not self._mechanical_fault_open(w):
            pr = health.forward_projection(w.twin_p, w.twin, w.design, spm_override=w.spm_override, max_days=260)
            cum = health.cycle_cash_so_far(w.twin)
            elapsed = max(w.twin.day_in_cycle, 1e-6)
            k_opt = pr["optimal_resteam_in_days"]
            if k_opt is not None:
                avg_opt = (cum + sum(pr["cash_rate"][:k_opt])) / (elapsed + k_opt)
            else:
                avg_opt = None
            k_plan = int(round(pr["planned_days_left"]))
            k_plan = min(k_plan, len(pr["cash_rate"]))
            avg_plan = (cum + sum(pr["cash_rate"][:k_plan])) / (elapsed + k_plan)
            w.projection = dict(optimal_resteam_in_days=k_opt, planned_days_left=pr["planned_days_left"], avg_cash_rate=avg_plan,
                                avg_cash_rate_opt=avg_opt, days=pr["days"][:200], q_oil=pr["q_oil"][:200], at=self.sim_iso())
        elif w.plant.phase != "production":
            w.projection = dict(optimal_resteam_in_days=None, planned_days_left=w.design.total_days - w.plant.day_in_cycle,
                                avg_cash_rate=None, avg_cash_rate_opt=None, days=[], q_oil=[], at=self.sim_iso())
        # expected oil of the current cycle (for the projected field SOR)
        if w.plant.phase == "production" and w.projection and w.projection.get("q_oil"):
            left = int(round(w.projection["planned_days_left"]))
            w.expected_cycle_oil = w.cycle_oil_meas + float(sum(w.projection["q_oil"][:left]))
        elif w.plant.phase != "production" and w._expected_for_cycle != w.plant.cycle_no:
            st0 = w.cycle_start_twin.copy()
            st0.cycle_no -= 1  # simulate_cycle starts a new cycle from the state it is given
            w.expected_cycle_oil = coupling.simulate_cycle(w.twin_p, w.design, state0=st0, record=False)["summary"]["cum_oil_m3"]
            w._expected_for_cycle = w.plant.cycle_no
        # health
        rate = 0.0
        if len(w.damage_hist) >= 2:
            (t0, d0), (t1, d1) = w.damage_hist[0], w.damage_hist[-1]
            rate = (d1 - d0) / max(t1 - t0, 1e-6)
        rul_rod = health.rod_rul(w.rod_damage_meas, rate)
        ch = [c for c in w.card_hist if c["diagnosis"] != "rod_parted"]
        pw = health.pump_wear_rul([c["t"] for c in ch], [c["retention"] for c in ch])
        w.health = dict(rod_damage=w.rod_damage_meas, rod_damage_rate_per_day=rate, rod_rul_days=rul_rod,
                        pump_retention=pw["current"], pump_retention_slope=pw["slope_per_day"], pump_rul_days=pw["days_to_threshold"],
                        failure_rate_per_year=(w.last_twin or {}).get("failure_rate", 0.0))

    # ------------------------------------------------------------------ schedule / alerts / recommendations
    def _recompute_schedule(self):
        from .optimize import steam

        active, reqs = [], []
        for w in self.wells:
            if w.plant.phase == "injection" and w.generator:
                rem = (w.design.steam_t - w.plant.steam_injected_t) / w.design.inj_rate_tpd
                active.append(steam.ActiveInjection(w.id, w.generator, rem, w.design.steam_t, w.design.inj_rate_tpd))
            else:
                if self._mechanical_fault_open(w):
                    continue
                if w.plant.phase == "production":
                    due = w.design.prod_days - w.plant.t_phase_d
                else:  # soak
                    due = w.design.soak_days - w.plant.t_phase_d + w.design.prod_days
                d_next = w.next_design or w.design
                opt = None
                if w.projection and w.projection.get("optimal_resteam_in_days") is not None and w.plant.phase == "production":
                    opt = float(w.projection["optimal_resteam_in_days"])
                reqs.append(steam.SteamRequest(w.id, due, d_next.inj_days, d_next.steam_t, d_next.inj_rate_tpd, self._value_per_tonne(w), opt))
        self.steam_schedule = steam.schedule(config.STEAM["generators"], active, reqs)
        self.steam_schedule["generated"] = self.sim_iso()

    def _evaluate_alerts(self):
        """Open, refresh and resolve alerts. One open alert per (well, type); every occurrence
        gets its own id so history is kept after it resolves."""
        seen = set()
        for w in self.wells:
            for a in advisor.well_alerts(self, w):
                key = f"{w.id}:{a['type']}"
                seen.add(key)
                aid = self._open_alerts.get(key)
                if aid is not None:
                    self.alerts[aid].update(value=a.get("value"), root_cause=a["root_cause"], fix=a["fix"], title=a["title"],
                                            severity=a["severity"], updated=self.sim_iso())
                else:
                    self._alert_seq += 1
                    aid = f"A{self._alert_seq:05d}"
                    obj = dict(id=aid, key=key, well_id=w.id, status="active", created=self.sim_iso(), updated=self.sim_iso(), **a)
                    self.alerts[aid] = obj
                    self._open_alerts[key] = aid
                    self.storage.upsert("alerts", obj)
        for key in [k for k in self._open_alerts if k not in seen]:
            a = self.alerts[self._open_alerts.pop(key)]
            a["status"] = "resolved"
            a["resolved"] = self.sim_iso()
            self.storage.upsert("alerts", a)
        closed = [k for k, a in self.alerts.items() if a["status"] == "resolved"]
        for k in closed[:-300]:
            self.alerts.pop(k)

    def _evaluate_recommendations(self):
        for w in self.wells:
            for r in advisor.well_recommendations(self, w):
                self.upsert_recommendation(r)

    def upsert_recommendation(self, r: dict) -> dict:
        # one pending recommendation per (well, type); refresh it with the latest numbers
        for existing in self.recommendations.values():
            if existing["status"] == "pending" and existing["well_id"] == r["well_id"] and existing["type"] == r["type"]:
                existing.update({k: v for k, v in r.items() if k not in ("id", "created")}, updated=self.sim_iso())
                self.storage.upsert("recommendations", existing)
                return existing
        obj = dict(id=uuid.uuid4().hex[:10], status="pending", created=self.sim_iso(), updated=self.sim_iso(), **r)
        self.recommendations[obj["id"]] = obj
        self.storage.upsert("recommendations", obj)
        return obj

    def withdraw_stale_recommendations(self):
        for r in self.recommendations.values():
            if r["status"] != "pending":
                continue
            w = self.by_id[r["well_id"]]
            if not advisor.still_relevant(self, w, r):
                r["status"] = "withdrawn"
                r["updated"] = self.sim_iso()
                self.storage.upsert("recommendations", r)

    def decide(self, rec_id: str, approve: bool, by: str, note: str = "") -> dict:
        r = self.recommendations[rec_id]
        if r["status"] != "pending":
            raise ValueError(f"recommendation is {r['status']}")
        w = self.by_id[r["well_id"]]
        r["decided_by"] = by or "engineer"
        r["decided_at"] = self.sim_iso()
        r["note"] = note
        if approve:
            advisor.apply_recommendation(self, w, r)
            r["status"] = "approved"
        else:
            r["status"] = "rejected"
        self.storage.upsert("recommendations", r)
        self.storage.audit(self.sim_iso(), r["decided_by"], "approve" if approve else "reject", w.id,
                           dict(recommendation=r["id"], type=r["type"], title=r["title"], note=note))
        return r

    def set_vfd(self, w: WellRuntime, enabled: bool, by: str):
        if enabled and not w.vfd_closed_loop:
            w._poc_before_vfd = w.design.poc
            w.design.poc = True
            w.vfd_spm = w.commanded_spm(self.sim_day) or w.design.spm_at(w.plant.t_phase_d)
            w.vfd_closed_loop = True
        elif not enabled and w.vfd_closed_loop:
            w.vfd_closed_loop = False
            w.design.poc = w._poc_before_vfd
            w.vfd_spm = None
        self.storage.audit(self.sim_iso(), by or "engineer", "vfd_closed_loop_" + ("enabled" if enabled else "disabled"), w.id,
                           dict(target_fillage=advisor.VFD_TARGET_FILL))

    def inject_fault(self, w: WellRuntime, kind: str, by: str):
        if kind == "rod_parted":
            w.true.rod_parted = True
        elif kind == "tv_leak":
            w.true.tv_leak = max(w.true.tv_leak, 0.35)
            w.tv_leak_rate = max(w.tv_leak_rate, 0.008)
        elif kind == "clear":
            w.true.rod_parted = False
            w.true.tv_leak = 0.0
            w.tv_leak_rate = 0.0
        else:
            raise ValueError(kind)
        self.storage.audit(self.sim_iso(), by or "demo", f"fault_{kind}", w.id, "scenario injected into the synthetic field")

    # ------------------------------------------------------------------ main loop
    def tick(self) -> list:
        t_start = time.perf_counter()
        dt = self.hours_per_tick / 24.0
        day_before = math.floor(self.sim_day)
        self._start_injections()
        for w in self.wells:
            self._step_well(w, dt, self.sim_day)
            if w.plant.phase != "injection" and w.generator:
                self.generators[w.generator]["busy_with"] = None
                w.generator = None
            if w.down_until is not None and self.sim_day >= w.down_until:
                w.down_until = None
        self._score_anomalies()
        self.sim_day += dt
        self._ticks += 1
        tasks = []
        if self.sim_day - self._last_card_day >= config.CARD_EVERY_SIM_HOURS / 24.0 - 1e-9:
            self._last_card_day = self.sim_day
            tasks.append("cards")
        if math.floor(self.sim_day) != day_before:
            tasks.append("daily")
        self._evaluate_alerts()
        if self._pending_rows:
            self.storage.write_telemetry(self._pending_rows)
            self._pending_rows = []
        self.tick_ms = (time.perf_counter() - t_start) * 1000.0
        return tasks

    def _daily_all(self):
        for w in self.wells:
            self._daily_tasks(w)
        self._recompute_schedule()
        self._evaluate_recommendations()
        self.withdraw_stale_recommendations()

    async def run(self, broadcast):
        loop = asyncio.get_running_loop()
        while True:
            t0 = loop.time()
            try:
                if not self.paused and self.ready and self.has_viewers():
                    # on small hosts the step runs in a worker thread so the event loop keeps answering requests
                    off = config.BACKGROUND_START
                    tasks = await loop.run_in_executor(None, self.tick) if off else self.tick()
                    if "cards" in tasks and (self._card_task is None or self._card_task.done()):
                        self._card_task = asyncio.create_task(self._generate_cards_async())
                    if "daily" in tasks:
                        day = math.floor(self.sim_day)
                        due = [w for i, w in enumerate(self.wells) if (day + i) % int(config.CALIBRATE_EVERY_SIM_DAYS) == 0]
                        if off:
                            await loop.run_in_executor(None, self._daily_all)
                        else:
                            self._daily_all()
                        for w in due:
                            asyncio.create_task(self._calibrate_async(w))
                    snap = await loop.run_in_executor(None, self.snapshot) if off else self.snapshot()
                    await broadcast(snap)
            except Exception:
                log.exception("tick failed")
            await asyncio.sleep(max(config.TICK_SECONDS - (loop.time() - t0), 0.05))

    # ------------------------------------------------------------------ views
    def _energy_per_bbl(self, w: WellRuntime) -> float | None:
        """Projected cycle energy per barrel = steam-generation energy of the slug / expected cycle oil
        + pumping and surface energy per barrel produced so far (all proxies, see config)."""
        exp_oil_bbl = (w.expected_cycle_oil or 0.0) * coupling.M3_TO_BBL
        if exp_oil_bbl < 150.0:
            return None
        steam_part = w.design.steam_t * config.STEAM_ENERGY_KWH_PER_T / exp_oil_bbl
        oil_bbl = w.cycle_oil_meas * coupling.M3_TO_BBL
        run_part = 0.0
        if oil_bbl >= 150.0:
            surface = coupling.SURFACE_KWH_PER_M3_LIQ * (w.cycle_oil_meas + w.cycle_water_meas)
            run_part = (w.twin.energy_kwh_cycle + surface) / oil_bbl
        return steam_part + run_part

    def well_summary(self, w: WellRuntime) -> dict:
        m = w.last_meas or {}
        tw = w.last_twin or {}
        alerts = [a for a in self.alerts.values() if a["well_id"] == w.id and a["status"] in ("active", "acknowledged")]
        sev_rank = {"critical": 3, "warning": 2, "info": 1}
        worst = max((sev_rank[a["severity"]] for a in alerts), default=0)
        down = w.down_until is not None or self._mechanical_fault_open(w)
        status = "down" if down else {3: "critical", 2: "warning", 1: "ok", 0: "ok"}[worst]
        proj = w.projection or {}
        return dict(
            id=w.id, pad=w.pad, x=w.x, y=w.y, phase=w.plant.phase, cycle_no=w.plant.cycle_no, day_in_cycle=w.plant.day_in_cycle,
            t_phase_d=w.plant.t_phase_d, prod_days_plan=w.design.prod_days, soak_days_plan=w.design.soak_days,
            inj_days_plan=w.design.inj_days, oil=m.get("oil_rate_m3d", 0.0), water=m.get("water_rate_m3d", 0.0),
            liquid=m.get("liquid_rate_m3d", 0.0), steam_rate=m.get("steam_rate_tpd", 0.0), spm=m.get("spm", 0.0),
            fillage=m.get("fillage", 0.0), runtime=m.get("runtime", 0.0), pprl=m.get("pprl_kn", 0.0), mprl=m.get("mprl_kn", 0.0),
            wht=m.get("wht_c", 0.0), whp=m.get("whp_mpa", 0.0), t_avg=tw.get("t_avg", config.T_RESERVOIR_C),
            mu_cp=tw.get("mu_oil_cp", fluid.oil_viscosity_cp(config.T_RESERVOIR_C)), r_h=w.twin.r_h, goodman=tw.get("goodman", 0.0),
            limiting=tw.get("limiting", ""), card=w.card["diagnosis"] if w.card else None,
            card_conf=w.card["confidence"] if w.card else None, card_version=w.card_version,
            anomaly=w.anomaly.get("score", 0.0), alerts=len(alerts), status=status, vfd=w.vfd_closed_loop,
            generator=w.generator, calib_conf=w.calib.get("confidence", 0.0), calib_status=w.calib.get("status"),
            resteam_in_days=proj.get("optimal_resteam_in_days"), planned_days_left=proj.get("planned_days_left"),
            cash_inr_d=w.cash_today, sor_cycle=(w.design.steam_t / w.expected_cycle_oil) if w.expected_cycle_oil and w.expected_cycle_oil > 25.0 else None,
            cum_oil=w.cum_oil_meas, next_design=w.next_design is not None,
            energy_bbl=self._energy_per_bbl(w), cycle_days_plan=w.design.total_days,
            optimal_resteam_day=(w.plant.t_phase_d + proj["optimal_resteam_in_days"]) if w.plant.phase == "production" and proj.get("optimal_resteam_in_days") is not None else None,
        )

    def kpis(self) -> dict:
        s = [self.well_summary(w) for w in self.wells]
        oil = sum(x["oil"] for x in s)
        exp_oil = sum(w.expected_cycle_oil or 0.0 for w in self.wells)
        exp_steam = sum(w.design.steam_t for w in self.wells if w.expected_cycle_oil)
        return dict(
            oil_m3d=oil, oil_bpd=oil * 6.2898, water_m3d=sum(x["water"] for x in s), steam_tpd=sum(x["steam_rate"] for x in s),
            sor=exp_steam / exp_oil if exp_oil > 1e-6 else None, cash_inr_d=sum(x["cash_inr_d"] for x in s),
            revenue_inr_d=oil * ECON["oil_price_inr_per_m3"],
            producing=sum(1 for x in s if x["phase"] == "production"), injecting=sum(1 for x in s if x["phase"] == "injection"),
            soaking=sum(1 for x in s if x["phase"] == "soak"), down=sum(1 for x in s if x["status"] == "down"),
            alerts_active=sum(1 for a in self.alerts.values() if a["status"] == "active"),
            alerts_critical=sum(1 for a in self.alerts.values() if a["status"] == "active" and a["severity"] == "critical"),
            pending_recommendations=sum(1 for r in self.recommendations.values() if r["status"] == "pending"),
            avg_fillage=float(np.mean([x["fillage"] for x in s if x["phase"] == "production"] or [0.0])),
            generators=[dict(id=g, capacity_tpd=v["capacity_tpd"], busy_with=v["busy_with"], x=GENERATOR_POS[g][0], y=GENERATOR_POS[g][1])
                        for g, v in self.generators.items()],
        )

    def snapshot(self) -> dict:
        return dict(type="tick", sim_time=self.sim_iso(), sim_day=self.sim_day, hours_per_tick=self.hours_per_tick,
                    paused=self.paused, bus=self.bus.status if self.bus else "", tick_ms=round(self.tick_ms, 1),
                    kpis=self.kpis(), wells=[self.well_summary(w) for w in self.wells])


def _r(a, nd: int = 4):
    return [round(float(v), nd) for v in a]


def _json(d: dict) -> str:
    import json

    return json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()})
