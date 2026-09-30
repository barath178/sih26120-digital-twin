"""The static demo runs whatif.simulate_json in the browser from recorded JSON; it must equal the API path."""
from dataclasses import asdict

from app import whatif
from app.physics import coupling


def test_simulate_json_matches_simulate():
    p = coupling.WellParams(well_id="BGW-T", pi_mult=1.1)
    st = coupling.CycleState(phase="production", t_phase_d=20.0, day_in_cycle=40.0, cycle_no=3, r_h=9.0, t_s=150.0)
    cur = coupling.CycleDesign()
    new = whatif.design_dict(coupling.CycleDesign(steam_t=1800.0, spm_knots=[5.0, 4.0, 3.5, 3.0]))
    body = dict(design={k: new[k] for k in ("steam_t", "inj_rate_tpd", "quality", "soak_days", "prod_days", "spm_knots", "stroke_m",
                                            "pump_depth_m", "vfd_auto", "poc")}, compare_current=True)
    direct = whatif.sanitize(whatif.simulate(p, st, cur, body["design"], True))
    via_json = whatif.simulate_json(dict(params=asdict(p), state=asdict(st), current=whatif.design_dict(cur)), body)
    assert via_json == direct
    assert via_json["delta"]["oil_per_day"] != 0


def test_invalid_design_raises_value_error():
    p, st, cur = coupling.WellParams(), coupling.CycleState(), coupling.CycleDesign()
    bad = whatif.design_dict(coupling.CycleDesign(pump_depth_m=p.depth_m + 10))
    try:
        whatif.simulate(p, st, cur, bad)
    except ValueError as e:
        assert "above mid-perforation" in str(e)
    else:
        raise AssertionError("expected ValueError")
