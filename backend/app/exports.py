"""Deterministic synthetic dataset generator (Build Bible 9): causal, produced by the same coupled
physics model as the live twin, then exported as CSV files in the documented schema.

    python -m app.exports            # writes backend/data/synthetic/*.csv + schema.json

Ground-truth event labels are stored for synthetic evaluation only."""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import config, dataio
from .ml import scenarios
from .ml.risk import label_from_truth
from .physics import coupling, sensors

OUT_DIR = config.DATA_DIR / "synthetic"
N_WELLS = 30
CYCLES_PER_WELL = 12
TELEMETRY_STEP_H = 2.0
TELEMETRY_PROD_DAYS = 48.0
SEED = config.RANDOM_SEED
BASE_TIME = datetime(2026, 1, 1)
FILES = ["synthetic_wells.csv", "synthetic_css_cycles.csv", "synthetic_srp_telemetry.csv", "synthetic_failures.csv"]


def exists() -> bool:
    return all((OUT_DIR / f).exists() for f in FILES) and (OUT_DIR / "schema.json").exists()


def _well_sample(rng: np.random.Generator, i: int):
    w = scenarios.sample_well(rng)
    w["depth_m"] = float(rng.uniform(1060.0, 1290.0))
    w["perm_eff_md"] = float(rng.uniform(600.0, 950.0))
    w["net_pay_m"] = float(rng.uniform(12.0, 22.0))
    w["np_total_m3"] = 0.0
    p, st = scenarios.well_from_features(w, f"BGW-{i + 1:03d}")
    p.perm_md = w["perm_eff_md"]
    p.u_mult = float(rng.uniform(0.85, 1.3))
    p.cool_mult = float(rng.uniform(0.85, 1.2))
    d = coupling.CycleDesign(steam_t=float(rng.choice([900.0, 1100.0, 1300.0])), inj_rate_tpd=200.0, soak_days=float(rng.choice([4.0, 5.0, 7.0])),
                             prod_days=float(rng.choice([130.0, 150.0, 170.0])), spm_knots=[float(rng.choice([3.0, 3.5, 4.0]))] * 4,
                             stroke_m=2.54, pump_depth_m=round(p.depth_m - float(rng.uniform(40.0, 120.0))))
    return p, d


def _wells_table(wells) -> pd.DataFrame:
    rows = []
    for p, d in wells:
        rows.append(dict(well_id=p.well_id, depth_m=round(p.depth_m, 1), formation="Jodhpur Sandstone", completion_type="thermal (VIT)",
                         vit_flag=True, pump_type="insert SRP", pump_diameter_in=2.25, net_pay_m=round(p.net_pay_m, 1),
                         permeability_md=round(p.perm_md, 0), porosity=p.porosity, pump_depth_m=d.pump_depth_m,
                         notes="SYNTHETIC well; geometry sampled inside the HISTORICAL_REFERENCE envelope (S4)"))
    return pd.DataFrame(rows)


def _cycles_and_events(wells, rng):
    cyc_rows, ev_rows = [], []
    ev_id = 0
    for p, d in wells:
        t0 = BASE_TIME - timedelta(days=CYCLES_PER_WELL * 190)
        state = None
        for n in range(1, CYCLES_PER_WELL + 1):
            design = coupling.CycleDesign(**{**d.__dict__, "steam_t": d.steam_t * float(rng.uniform(0.85, 1.15)),
                                             "prod_days": d.prod_days * float(rng.uniform(0.85, 1.15))})
            res = coupling.simulate_cycle(p, design, state0=state, dt_d=1.0, record=True)
            s, ser = res["summary"], res["series"]
            state = res["end_state"]
            cid = f"{p.well_id}-C{n:02d}"
            p_inj = min(p.p_res_mpa + config.RESERVOIR["injectivity_mpa_per_tpd"] * design.inj_rate_tpd, config.RESERVOIR["frac_pressure_mpa"]) * 10.0
            cyc_rows.append(dict(cycle_id=cid, well_id=p.well_id, start_time=t0.isoformat(timespec="minutes"),
                                 injection_mass_t=round(design.steam_t, 1), injection_pressure_bar=round(p_inj, 1),
                                 injection_duration_d=round(design.inj_days, 2), soak_duration_d=round(design.soak_days, 2),
                                 cutoff_rule=f"production_days={design.prod_days:.0f}", oil_bbl=round(s["cum_oil_bbl"], 1),
                                 water_bbl=round(s["cum_water_m3"] * coupling.M3_TO_BBL, 1), sor_t_per_m3=round(s["sor"], 3),
                                 avg_fillage_pct=round(s["avg_fillage"] * 100, 1), energy_per_bbl_kwh=round(s["energy_per_bbl_kwh"], 2),
                                 failure_prob=round(s["failure_prob"], 3), cycle_days=round(s["cycle_days"], 1),
                                 peak_oil_bopd=round(s["peak_oil"] * coupling.M3_TO_BBL, 1)))
            # events: episodes of low fillage / pound in the daily series
            prod_i = [i for i, ph in enumerate(ser["phase"]) if ph == "production"]
            run, kind = 0, None
            for i in prod_i:
                fill, spm = ser["fillage"][i], ser["spm"][i]
                lab = label_from_truth(fill, spm, 0.0, 0.0, ser["goodman"][i], 1.0)
                if lab != "NORMAL":
                    run += 1
                    kind = lab
                elif run >= 20:
                    ev_id += 1
                    ev_rows.append(dict(event_id=f"EV{ev_id:05d}", well_id=p.well_id, timestamp=(t0 + timedelta(days=ser["day"][i - 1])).isoformat(timespec="minutes"),
                                        event_type=kind, severity="medium" if kind == "LOW_FILLAGE" else "high", label_source="SYNTHETIC_RULE",
                                        notes=f"{run} consecutive days"))
                    run = 0
                else:
                    run = 0
            if run >= 20 and prod_i:
                ev_id += 1
                ev_rows.append(dict(event_id=f"EV{ev_id:05d}", well_id=p.well_id, timestamp=(t0 + timedelta(days=ser["day"][prod_i[-1]])).isoformat(timespec="minutes"),
                                    event_type=kind, severity="medium" if kind == "LOW_FILLAGE" else "high", label_source="SYNTHETIC_RULE",
                                    notes=f"{run} consecutive days"))
            # mechanical failure sampled from the cycle hazard
            if rng.random() < s["failure_prob"]:
                ev_id += 1
                et = "ROD_PART" if rng.random() < 0.55 else "PUMP_WEAR"
                when = t0 + timedelta(days=float(rng.uniform(design.inj_days + design.soak_days, s["cycle_days"])))
                ev_rows.append(dict(event_id=f"EV{ev_id:05d}", well_id=p.well_id, timestamp=when.isoformat(timespec="minutes"), event_type=et,
                                    severity="critical", label_source="SYNTHETIC_HAZARD", notes="sampled from modelled failure hazard"))
            t0 += timedelta(days=s["cycle_days"] + float(rng.uniform(0, 3)))
    return pd.DataFrame(cyc_rows), pd.DataFrame(ev_rows)


def _telemetry(wells, rng):
    rows = []
    dt = TELEMETRY_STEP_H / 24.0
    for k, (p, d) in enumerate(wells):
        state = coupling.CycleState(np_total_m3=float(rng.uniform(300, 3000)), cycle_no=int(rng.integers(2, 6)))
        design = coupling.CycleDesign(**{**d.__dict__, "prod_days": TELEMETRY_PROD_DAYS + 10})
        t = BASE_TIME + timedelta(hours=float(rng.uniform(0, 240)))
        cid = f"{p.well_id}-C{state.cycle_no:02d}"
        p_inj = min(p.p_res_mpa + config.RESERVOIR["injectivity_mpa_per_tpd"] * design.inj_rate_tpd, config.RESERVOIR["frac_pressure_mpa"]) * 10.0
        steam_run = 0.0
        oil_run = 0.0
        guard = 0
        while guard < 6000:
            guard += 1
            if state.phase == "production" and state.t_phase_d >= TELEMETRY_PROD_DAYS:
                break
            out = coupling.advance(p, state, design, dt)
            meas = sensors.measure(out, rng)
            steam_run += out["steam_rate_tpd"] * dt
            oil_run += out["q_oil"] * dt
            prod = out["phase"] == "production"
            label = "NORMAL"
            if prod and out.get("spm"):
                label = label_from_truth(out["fillage"], out["spm"], out.get("rodfloat", 0.0), out.get("mu_pump", 0.0), out.get("goodman", 0.0), out.get("runtime", 1.0))
            t_avg = out.get("t_avg", config.T_RESERVOIR_C)
            rows.append(dict(
                timestamp=t.isoformat(timespec="minutes"), well_id=p.well_id, cycle_id=cid,
                cycle_phase=coupling.phase_label(out["phase"], t_avg), reservoir_temperature=round(t_avg, 2),
                pump_temperature=round(out.get("t_tub", out.get("t_bh", t_avg)), 2), reservoir_pressure=round(out.get("p_eff", p.p_res_mpa) * 10.0, 2),
                viscosity_est=round(out.get("mu_oil_cp", 0.0), 2), steam_mass=round(design.steam_t, 1), injection_pressure=round(p_inj, 1),
                injection_duration=round(design.inj_days, 2), soak_duration=round(design.soak_days, 2),
                stroke_length=round(design.stroke_m / 0.0254, 1), spm=round(meas["spm"], 3),
                vfd_frequency=round(min(meas["spm"] / config.SPM_AT_BASE_HZ * config.VFD_BASE_HZ, 120.0), 2),
                motor_current=round(meas["motor_current_a"], 2), peak_load=round(meas["pprl_kn"], 2), min_load=round(meas["mprl_kn"], 2),
                pump_fillage=round(meas["fillage"] * 100.0, 1), oil_rate=round(meas["oil_rate_m3d"] * coupling.M3_TO_BBL, 2),
                water_cut=round(min(max(meas["water_rate_m3d"] / max(meas["liquid_rate_m3d"], 1e-6), 0.0), 1.0), 3) if meas["liquid_rate_m3d"] > 0.05 else 0.0,
                power_kw=round(meas["motor_kw"], 2), sor=round(steam_run / oil_run, 3) if oil_run > 25.0 else None,
                failure_label=label))
            t += timedelta(hours=TELEMETRY_STEP_H)
    return pd.DataFrame(rows)


def generate(seed: int = SEED, out_dir=OUT_DIR, verbose: bool = True) -> dict:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    wells = [_well_sample(rng, i) for i in range(N_WELLS)]
    wells_df = _wells_table(wells)
    cycles_df, events_df = _cycles_and_events(wells, rng)
    tel_df = _telemetry(wells, rng)
    wells_df.to_csv(out_dir / "synthetic_wells.csv", index=False)
    cycles_df.to_csv(out_dir / "synthetic_css_cycles.csv", index=False)
    tel_df.to_csv(out_dir / "synthetic_srp_telemetry.csv", index=False)
    events_df.to_csv(out_dir / "synthetic_failures.csv", index=False)
    dataio.write_schema(out_dir / "schema.json")
    info = dict(wells=len(wells_df), cycles=len(cycles_df), telemetry_rows=len(tel_df), events=len(events_df), seed=seed,
                seconds=round(time.time() - t0, 1))
    if verbose:
        print("synthetic dataset:", info)
    return info


if __name__ == "__main__":
    generate()
