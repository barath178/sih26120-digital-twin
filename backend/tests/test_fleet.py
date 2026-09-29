"""Field-level features: mission summary, maintenance planner, field optimiser with steam budget, printable report."""
import os
import time

import pytest

os.environ.setdefault("TICK_SECONDS", "0.2")

from fastapi.testclient import TestClient  # noqa: E402

from app import fleet  # noqa: E402
from app.main import app  # noqa: E402


def _rows():
    """Each well: keep current (gain 0), a steam-lean plan and a full plan."""
    opt = lambda name, steam, gain: dict(name=name, steam_t=steam, gain_inr_per_year=gain, metrics={"m": name}, design={"steam_t": steam})
    return [
        dict(well_id="A", base_steam_t=1000.0, options=[opt("full", 1400.0, 4_000_000.0), opt("lean", 800.0, 1_000_000.0)]),
        dict(well_id="B", base_steam_t=1000.0, options=[opt("full", 1200.0, 3_000_000.0), opt("lean", 750.0, -500_000.0)]),
        dict(well_id="C", base_steam_t=1000.0, options=[opt("full", 900.0, 2_500_000.0)]),
        dict(well_id="D", base_steam_t=1000.0, options=[opt("full", 1000.0, -10.0)]),
    ]


def _brute_force(rows, budget):
    import itertools

    best = None
    for combo in itertools.product(*[[(0.0, r["base_steam_t"])] + [(o["gain_inr_per_year"], o["steam_t"]) for o in r["options"]] for r in rows]):
        steam = sum(c[1] for c in combo)
        if steam <= budget + 1e-6 and (best is None or sum(c[0] for c in combo) > best):
            best = sum(c[0] for c in combo)
    return best


def test_allocation_takes_the_best_option_per_well_when_steam_is_ample():
    rows = _rows()
    a = fleet.allocate_steam(rows, 10_000.0)
    assert a["total_gain_inr_per_year"] == pytest.approx(4_000_000.0 + 3_000_000.0 + 2_500_000.0)
    assert [r["choice"] for r in rows] == ["full", "full", "full", "baseline"]


@pytest.mark.parametrize("budget", [3_450.0, 3_700.0, 4_000.0, 4_300.0])
def test_allocation_is_optimal_and_respects_the_budget(budget):
    rows = _rows()
    a = fleet.allocate_steam(rows, budget)
    assert a["total_steam_t"] <= budget + 1e-6
    assert a["total_gain_inr_per_year"] == pytest.approx(_brute_force(rows, budget))


def test_tight_budget_cuts_steam_where_it_hurts_least():
    """Below today's steam use the plan must cut: the well with the cheapest cut (C saves steam AND gains) is used first."""
    rows = _rows()
    a = fleet.allocate_steam(rows, 3_800.0)
    by = {r["well_id"]: r for r in rows}
    assert by["C"]["choice"] == "full"                       # 900 t and +2.5M: cuts steam and earns
    assert a["total_steam_t"] <= 3_800.0 + 1e-6 and a["note"] is None


def test_allocation_reports_when_budget_is_below_any_feasible_plan():
    a = fleet.allocate_steam(_rows(), 100.0)
    assert a["note"] and a["total_steam_t"] == pytest.approx(a["min_steam_t"])


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_mission_summary_has_value_and_evidence(client):
    m = client.get("/api/mission").json()
    assert m["wells"] == 10 and m["oil_m3d"] > 0
    keys = {v["key"] for v in m["value_at_stake"]}
    assert keys == {"actions", "leak", "steam", "failure", "maint"}
    assert m["total_at_stake_inr"] == pytest.approx(sum(v["inr_per_year"] for v in m["value_at_stake"]))
    assert len(m["top_actions"]) <= 3 and m["chain"]["well_id"].startswith("BGW-")
    assert m["models"]["dynacard_accuracy"] and m["twin_accuracy"] is not None


def test_maintenance_plan_is_consistent(client):
    p = client.get("/api/maintenance-plan").json()
    assert p["n_jobs"] == len(p["jobs"])
    for j in p["jobs"]:
        assert j["do_in_days"] >= 0 and j["saving_inr"] >= 0 and j["reasons"]
        if j["failed"]:
            assert j["do_in_days"] == 0
        else:  # a planned job must never be scheduled after the predicted end of life
            assert j["do_in_days"] <= (j["rul_days"] or 0) + 1e-6
    assert p["assumptions"]["status"] == "SYNTHETIC_ASSUMPTION"
    covered = [w for b in p["batches"] for w in b["wells"]]
    assert sorted(covered) == sorted(j["well_id"] for j in p["jobs"])


def test_report_is_printable_and_labelled_synthetic(client):
    r = client.get("/api/report")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "SYNTHETIC DEMO DATA" in r.text and "window.print()" in r.text and "BGW-001" in r.text


def test_field_plan_runs_reallocates_and_submits(client):
    r = client.post("/api/field/plan", json={"budget_pct": 100, "wells": ["BGW-001", "BGW-005"], "pop": 24, "gens": 8})
    assert r.status_code == 200
    assert client.post("/api/field/plan", json={"budget_pct": 100}).status_code == 409  # already running
    t0 = time.time()
    while True:
        st = client.get("/api/field/plan").json()
        if st["status"] in ("done", "error"):
            break
        assert time.time() - t0 < 240, "field plan did not finish"
        time.sleep(1.5)
    assert st["status"] == "done", st.get("error")
    assert {x["well_id"] for x in st["results"]} == {"BGW-001", "BGW-005"}
    assert st["allocation"]["total_steam_t"] <= st["budget_t"] + 1e-6 or st["allocation"]["note"]
    assert all(r["choice"] in ("baseline", "full", "lean") for r in st["results"])
    tight = client.post("/api/field/plan/reallocate", json={"budget_pct": 60}).json()
    assert tight["budget_pct"] == 60
    n_before = len([x for x in client.get("/api/recommendations?status=pending").json() if x["type"] == "design"])
    sub = client.post("/api/field/plan/submit", json={"by": "Test Engineer"}).json()
    assert sub["submitted"] == len([x for x in tight["results"] if x["chosen"]])
    n_after = len([x for x in client.get("/api/recommendations?status=pending").json() if x["type"] == "design"])
    assert n_after >= n_before
    assert any(a["action"] == "submit_field_plan" for a in client.get("/api/audit").json())
