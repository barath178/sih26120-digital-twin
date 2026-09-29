"""Physics validation against analytical limits and published data."""
import math

import numpy as np
import pytest

from app.physics import coupling, fluid, reservoir, srp, wellbore


def test_viscosity_matches_published_baghewala_range():
    assert 10_000 <= fluid.oil_viscosity_cp(50.0) <= 13_000
    assert fluid.WALTHER_R2 > 0.999
    # viscosity falls monotonically with temperature
    mus = [fluid.oil_viscosity_cp(t) for t in range(40, 320, 20)]
    assert all(a > b for a, b in zip(mus, mus[1:]))


def test_steam_tables():
    assert fluid.t_sat(10.0) == pytest.approx(311.0, abs=0.1)
    assert fluid.h_fg(1.0) == pytest.approx(2014.6e3, rel=1e-4)


def test_marx_langenheim_no_loss_limit():
    h0, t, h, m = 3.5e6, 1800.0, 12.0, 2.35e6
    a = reservoir.marx_langenheim_area(h0, t, h, m, 1.7, 2.4e6, 256.0)
    assert a == pytest.approx(h0 * t / (m * h * 256.0), rel=0.01)
    # thermal efficiency falls with time
    e1 = reservoir.marx_langenheim_efficiency(86400, h, m, 1.7, 2.4e6)
    e2 = reservoir.marx_langenheim_efficiency(30 * 86400, h, m, 1.7, 2.4e6)
    assert 0 < e2 < e1 <= 1


@pytest.mark.parametrize("theta", [1e-4, 1e-3])
def test_boberg_lantz_vr_short_time(theta):
    assert reservoir.v_r(theta) == pytest.approx(1 - 2 * math.sqrt(theta / math.pi), abs=5e-4)


@pytest.mark.parametrize("theta", [100.0, 500.0])
def test_boberg_lantz_vr_long_time(theta):
    assert reservoir.v_r(theta) == pytest.approx(1 / (4 * theta), rel=0.02)


def test_ramey_quality_drops_with_depth_and_heat_loss():
    shallow = wellbore.steam_downhole(9.0, 0.8, 200, 500, 47, 86400)
    deep = wellbore.steam_downhole(9.0, 0.8, 200, 1000, 47, 86400)
    lossy = wellbore.steam_downhole(9.0, 0.8, 200, 1000, 47, 86400, u_mult=2.0)
    assert 0 < lossy["quality"] < deep["quality"] < shallow["quality"] < 0.8


def test_gibbs_diagnostic_recovers_pump_card():
    fo = srp.fo_from_pressures(950, 1.0, 970)
    specs = [srp.CardSpec(stroke=2.54, spm=5, pump_depth=950, fo=fo, rho_liq=970, mu_tubing_cp=200, mu_pump_cp=200, noise=0.0,
                          fill=f) for f in (1.0, 0.55)]
    res = srp.simulate_cards(specs, seed=0)
    for i, s in enumerate(specs):
        pos, load = srp.gibbs_downhole(res["surface_pos"][i], res["surface_load"][i], s.spm, s.pump_depth, s.rho_liq, s.mu_tubing_cp)
        rmse = np.sqrt(np.mean((load - res["pump_load"][i]) ** 2)) / fo
        assert rmse < 0.03
        assert pos.max() == pytest.approx(res["pump_pos"][i].max(), rel=0.02)
        assert srp.card_metrics(pos, load, fo, s.stroke)["fillage_est"] == pytest.approx(s.fill, abs=0.08)


def test_goodman_increases_with_load():
    assert srp.goodman_loading(200e6, 50e6) > srp.goodman_loading(150e6, 50e6)


def test_css_cycle_is_physically_consistent():
    r = coupling.simulate_cycle(coupling.WellParams(), coupling.CycleDesign())
    s, ser = r["summary"], r["series"]
    assert s["steam_t"] == pytest.approx(1200.0)
    assert 1.5 < s["sor"] < 6.0                       # realistic heavy-oil CSS
    prod = [i for i, p in enumerate(ser["phase"]) if p == "production"]
    t = [ser["t_avg"][i] for i in prod]
    mu = [ser["mu_oil_cp"][i] for i in prod]
    assert all(a >= b - 1e-6 for a, b in zip(t, t[1:]))  # heated zone only cools
    assert all(a <= b + 1e-6 for a, b in zip(mu, mu[1:]))  # so viscosity only rises
    # coupling rule: produced liquid never exceeds deliverability or pump capacity
    for i in prod:
        assert ser["q_liq"][i] <= ser["q_liq_deliv"][i] + 1e-6
        assert ser["q_liq"][i] <= ser["cap"][i] + 1e-6
    # fillage falls as the zone cools (fluid pound later in the cycle)
    assert ser["fillage"][prod[-1]] < ser["fillage"][prod[0]]


def test_time_varying_spm_with_poc_beats_fixed_speed():
    p = coupling.WellParams()
    base = coupling.simulate_cycle(p, coupling.CycleDesign(spm_knots=[4.0] * 4))["summary"]
    sched = coupling.simulate_cycle(p, coupling.CycleDesign(spm_knots=[6, 5, 3, 2], poc=True, pump_depth_m=990))["summary"]
    assert sched["oil_per_day"] > base["oil_per_day"]
    assert sched["failure_prob"] < base["failure_prob"]
    assert sched["npv_per_day"] > base["npv_per_day"]
