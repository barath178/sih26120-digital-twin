"""Tests mapped to Build Bible sections 14.1 (physics sanity), 14.3 (optimiser), 14.4 (software)."""
import hashlib
import importlib
import io
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from app import config, dataio, exports
from app.ml import risk as risk_mod
from app.optimize import joint
from app.physics import coupling, fluid, srp


# ------------------------------------------------------------------ 14.1 physics sanity
def _end_of_injection(steam_t):
    p, d = coupling.WellParams(), coupling.CycleDesign(steam_t=steam_t, inj_rate_tpd=200.0, soak_days=5.0)
    st = coupling.CycleState()
    while st.phase == "injection":
        coupling.advance(p, st, d, 0.25)
    return st


def test_more_steam_does_not_lower_thermal_state():
    small, big = _end_of_injection(700.0), _end_of_injection(1400.0)
    assert big.heat_injected_j > small.heat_injected_j
    assert big.r_h >= small.r_h
    assert big.t_s >= small.t_s - 1e-6


def _temp_after(soak_days, hours_after_injection=45.0):
    p, d = coupling.WellParams(), coupling.CycleDesign(steam_t=1000.0, inj_rate_tpd=200.0, soak_days=soak_days, spm_knots=[4.0] * 4)
    st, t_after, out = coupling.CycleState(), 0.0, None
    while st.phase == "injection":
        coupling.advance(p, st, d, 0.25)
    while t_after < hours_after_injection:
        out = coupling.advance(p, st, d, 0.5)
        t_after += 0.5
    return out["t_avg"]


def test_longer_soak_retains_heat_longer_than_shorter_soak():
    """Same time after injection: a well kept shut in longer has not yet lost heat with produced fluids."""
    assert _temp_after(12.0) >= _temp_after(2.0) - 1e-6


def test_viscosity_monotonic_decreasing_in_temperature():
    mus = [fluid.oil_viscosity_cp(t) for t in np.linspace(20, 320, 60)]
    assert all(a > b for a, b in zip(mus, mus[1:]))
    assert all(0.5 <= m < 1e6 for m in mus)  # bounded


def test_higher_viscosity_never_increases_fillage():
    fills = [srp.viscous_fill_limit(mu, 4.0) for mu in (10, 100, 1000, 5000, 20000)]
    assert all(a >= b for a, b in zip(fills, fills[1:]))


def test_srp_capacity_increases_with_spm_and_stroke_but_oil_limited_by_inflow():
    q = lambda spm, stroke: srp.quick_srp(spm, stroke, 1000.0, 1.0, 950.0, 100.0, 1.0)["capacity"]
    assert q(6, 2.54) > q(3, 2.54) and q(4, 3.4) > q(4, 1.8)
    # late in the cycle the well is inflow-limited: faster pumping cannot raise oil, only lowers fillage
    p, d = coupling.WellParams(), coupling.CycleDesign(spm_knots=[4.0] * 4)
    st = coupling.CycleState()
    while not (st.phase == "production" and st.t_phase_d > 120):
        coupling.advance(p, st, d, 1.0)
    slow = coupling.production_point(p, st, 3.0, d.stroke_m, d.pump_depth_m)
    fast = coupling.production_point(p, st, 8.0, d.stroke_m, d.pump_depth_m)
    assert fast["q_oil"] == pytest.approx(slow["q_oil"], rel=0.02)
    assert fast["fillage"] < slow["fillage"]


def test_sor_and_energy_safe_with_zero_oil():
    dead = coupling.WellParams(perm_md=1e-6, pi_mult=1e-6)
    s = coupling.simulate_cycle(dead, coupling.CycleDesign(), record=False)["summary"]
    assert np.isfinite(s["sor"]) and np.isfinite(s["energy_per_bbl_kwh"])


def test_risk_score_higher_for_rod_float_than_normal():
    normal = [0.86, 1.0, 3.0, 45.0, 17.0, 28.0, 0.38, 150.0, 150.0, 4.0, 0.08]
    floating = [0.90, 1.0, 8.0, 70.0, -8.0, 78.0, -0.11, 6000.0, 60.0, 9.0, 1.4]
    assert risk_mod.risk_model.predict(floating)["risk_score"] > risk_mod.risk_model.predict(normal)["risk_score"] + 0.3


def test_risk_model_has_support_for_every_class():
    risk_mod.risk_model.ensure()
    assert risk_mod.risk_model.metrics["classes_without_support"] == []


def test_energy_per_barrel_includes_steam():
    p = coupling.WellParams()
    a = coupling.simulate_cycle(p, coupling.CycleDesign(steam_t=800.0), record=False)["summary"]
    b = coupling.simulate_cycle(p, coupling.CycleDesign(steam_t=1800.0), record=False)["summary"]
    assert b["steam_kwh"] > a["steam_kwh"] and a["total_energy_kwh"] > a["steam_kwh"]


# ------------------------------------------------------------------ 14.3 optimiser
LIMITS = dict(min_fillage=0.7, max_goodman=0.9, max_risk=0.3, max_steam_t=1400.0)


def _run_opt(seed=1):
    p, st = coupling.WellParams(depth_m=1150.0), coupling.CycleState(np_total_m3=1500.0)
    return joint.optimize(p, st, coupling.CycleDesign(pump_depth_m=1090.0), method="nsga2", pop=30, gens=12, seed=seed, limits=LIMITS)


def test_optimizer_returns_only_feasible_candidates_and_is_deterministic():
    a, b = _run_opt(1), _run_opt(1)
    assert a["candidates"], "expected feasible candidates"
    for c in a["candidates"]:
        m, dsn = c["metrics"], c["design"]
        assert c["feasibility"]["status"] == "PASS"
        assert m["avg_fillage"] >= LIMITS["min_fillage"] - 1e-9
        assert m["max_goodman"] <= LIMITS["max_goodman"] + 1e-9
        assert m["failure_prob"] <= LIMITS["max_risk"] + 1e-9
        assert dsn["steam_t"] <= LIMITS["max_steam_t"] + 1e-6
        assert all(v["ok"] for v in c["constraints"].values())
    assert [c["design"]["steam_t"] for c in a["candidates"]] == [c["design"]["steam_t"] for c in b["candidates"]]
    assert a["baseline"]["metrics"]["oil_per_day"] == b["baseline"]["metrics"]["oil_per_day"]  # deterministic baseline


def test_infeasible_limits_yield_no_candidates_rather_than_violations():
    p, st = coupling.WellParams(depth_m=1150.0), coupling.CycleState()
    res = joint.optimize(p, st, coupling.CycleDesign(pump_depth_m=1090.0), method="nsga2", pop=24, gens=8,
                         limits=dict(min_fillage=0.99, max_goodman=0.05))
    assert res["candidates"] == [] and res["recommended"] is None


# ------------------------------------------------------------------ 14.4 data validation
def _good_df(n=6):
    ts = pd.date_range("2026-01-01", periods=n, freq="2h")
    return pd.DataFrame(dict(timestamp=ts.astype(str), well_id="BGW-001", cycle_phase="PRODUCTION", stroke_length=100.0, spm=4.0,
                             pump_fillage=60.0, oil_rate=12.0, peak_load=45.0, min_load=17.0, water_cut=0.6))


def _csv(df):
    return df.to_csv(index=False).encode()


def test_valid_csv_passes():
    v = dataio.validate(_csv(_good_df()))
    assert v["ok"] and v["rows"] == 6 and v["wells"] == ["BGW-001"]


def test_missing_columns_rejected_with_clear_message():
    v = dataio.validate(_csv(_good_df().drop(columns=["spm", "oil_rate"])))
    assert not v["ok"] and any("spm" in e and "oil_rate" in e for e in v["errors"])


def test_wrong_types_rejected():
    df = _good_df()
    df["spm"] = df["spm"].astype(object)
    df.loc[2, "spm"] = "fast"
    v = dataio.validate(_csv(df))
    assert not v["ok"] and any("spm" in e and "non-numeric" in e for e in v["errors"])


def test_duplicate_timestamps_rejected():
    df = _good_df()
    df.loc[3, "timestamp"] = df.loc[2, "timestamp"]
    v = dataio.validate(_csv(df))
    assert not v["ok"] and any("duplicate" in e for e in v["errors"])


def test_ambiguous_units_rejected_not_coerced():
    frac = _good_df()
    frac["pump_fillage"] = 0.6  # fraction instead of percent
    assert any("PERCENT" in e for e in dataio.validate(_csv(frac))["errors"])
    metres = _good_df()
    metres["stroke_length"] = 2.5  # metres instead of inches
    assert any("INCHES" in e for e in dataio.validate(_csv(metres))["errors"])
    pct_wc = _good_df()
    pct_wc["water_cut"] = 60.0
    assert any("FRACTION" in e for e in dataio.validate(_csv(pct_wc))["errors"])


def test_excess_missingness_rejected_for_required_columns():
    df = _good_df(10)
    df.loc[:4, "oil_rate"] = np.nan
    v = dataio.validate(_csv(df))
    assert not v["ok"] and any("oil_rate" in e and "missing" in e for e in v["errors"])


def test_schema_matches_build_bible_dictionary():
    expected = ["timestamp", "well_id", "cycle_id", "cycle_phase", "reservoir_temperature", "pump_temperature", "reservoir_pressure",
                "viscosity_est", "steam_mass", "injection_pressure", "injection_duration", "soak_duration", "stroke_length", "spm",
                "vfd_frequency", "motor_current", "peak_load", "min_load", "pump_fillage", "oil_rate", "water_cut", "power_kw", "sor",
                "failure_label"]
    assert list(dataio.SCHEMA) == expected


def test_replay_runs_and_flags_out_of_domain():
    df = _good_df(40)
    df["cycle_phase"] = "COOLDOWN"
    df["peak_load"], df["min_load"], df["spm"] = 500.0, -90.0, 15.0  # far outside training ranges
    v = dataio.validate(_csv(df))
    assert v["ok"]
    r = dataio.replay(v["df"], "BGW-001")
    assert r["n"] == 40 and r["ood"] and r["ood"]["flag"]
    assert any(n.startswith("OUT-OF-DOMAIN") for n in r["notes"])


# ------------------------------------------------------------------ reproducibility / provenance / smoke
def test_synthetic_generation_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "N_WELLS", 2)
    monkeypatch.setattr(exports, "CYCLES_PER_WELL", 2)
    monkeypatch.setattr(exports, "TELEMETRY_PROD_DAYS", 6.0)
    hashes = []
    for name in ("a", "b"):
        out = tmp_path / name
        exports.generate(seed=5, out_dir=out, verbose=False)
        hashes.append({f: hashlib.sha256((out / f).read_bytes()).hexdigest() for f in exports.FILES})
    assert hashes[0] == hashes[1]


def test_exported_datasets_have_documented_scale_and_columns():
    assert exports.exists()
    tel = pd.read_csv(exports.OUT_DIR / "synthetic_srp_telemetry.csv")
    assert list(tel.columns) == list(dataio.SCHEMA)
    assert len(tel) >= 20_000
    assert 200 <= len(pd.read_csv(exports.OUT_DIR / "synthetic_css_cycles.csv")) <= 1000
    assert 100 <= len(pd.read_csv(exports.OUT_DIR / "synthetic_failures.csv")) <= 500
    assert 10 <= len(pd.read_csv(exports.OUT_DIR / "synthetic_wells.csv")) <= 30
    assert set(tel["cycle_phase"]) <= dataio.PHASES
    # the exported telemetry passes the same validator used for uploads (minus the synthetic-only label column)
    assert dataio.validate(_csv(tel.head(500).drop(columns=["failure_label"])))["ok"]


def test_provenance_uses_only_defined_status_codes():
    allowed = {"VERIFIED_FIELD", "PUBLIC_REFERENCE", "HISTORICAL_REFERENCE", "SYNTHETIC_ASSUMPTION", "USER_CONFIGURED"}
    assert {r[2] for r in config.PROVENANCE} <= allowed
    assert "VERIFIED_FIELD" not in {r[2] for r in config.PROVENANCE}  # no verified field data exists yet
    assert config.DATA_MODE == "SYNTHETIC" and "not live Oil India" in config.DISCLAIMER


@pytest.mark.parametrize("mod", ["app.config", "app.field", "app.advisor", "app.dataio", "app.exports", "app.main", "app.train",
                                 "app.ml.risk", "app.ml.dynacard", "app.ml.forecaster", "app.ml.surrogate", "app.optimize.joint"])
def test_modules_import(mod):
    assert importlib.import_module(mod)


def test_reservoir_parameters_inside_source_envelope():
    r = config.RESERVOIR
    assert 1050 <= r["depth_mid_perf_m"] <= 1300 and 5 <= r["net_pay_m"] <= 23
    assert 0.18 <= r["porosity"] <= 0.20 and r["permeability_md"] < 1000
    assert 46 <= config.T_RESERVOIR_C <= 48 and 17 <= config.API_GRAVITY <= 19


def test_no_recommended_plan_earns_less_than_the_current_plan():
    """Regression: with equal objective weights the search once ranked plans that lowered net value first."""
    for depth in (1100.0, 1200.0):
        p, st = coupling.WellParams(depth_m=depth, perm_md=800.0), coupling.CycleState(np_total_m3=2000.0)
        res = joint.optimize(p, st, coupling.CycleDesign(pump_depth_m=depth - 60.0), method="nsga2", pop=30, gens=12,
                             weights=dict(oil=1.0, sor=1.0, energy=0.5, risk=1.0))
        base = res["baseline"]["metrics"]["npv_per_day"]
        assert all(c["metrics"]["npv_per_day"] >= base - 1e-6 for c in res["candidates"])
        if res["recommended"]:
            assert res["gain"]["npv_per_day"] >= -1e-6
