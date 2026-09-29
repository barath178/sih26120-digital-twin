"""API tests for the Build Bible additions: provenance, scenario audit, replay, history, risk, sensitivity."""
import io
import os

import pytest

os.environ.setdefault("TICK_SECONDS", "0.2")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_provenance_endpoint(client):
    j = client.get("/api/provenance").json()
    assert j["data_mode"] == "SYNTHETIC" and "DEMO DATA MODE" in j["disclaimer"] and len(j["sources"]) >= 11
    assert all(p["status"] in j["status_codes"] for p in j["parameters"])


def test_scenarios_are_audited_with_model_version_and_data_mode(client):
    d = client.get("/api/wells/BGW-002").json()["design"]
    design = {k: d[k] for k in ("steam_t", "inj_rate_tpd", "quality", "soak_days", "prod_days", "spm_knots", "stroke_m", "pump_depth_m", "vfd_auto", "poc")}
    r = client.post("/api/simulate", json={"well_id": "BGW-002", "design": design}).json()
    rows = client.get("/api/scenarios").json()
    row = next(x for x in rows if x["scenario_id"] == r["scenario_id"])
    assert row["kind"] == "what-if" and row["data_mode"] == "SYNTHETIC" and row["model_version"]
    assert "oil_per_day" in row["outputs"]["summary"]


def test_optimizer_with_limits_returns_candidate_table_and_logs_scenario(client):
    r = client.post("/api/wells/BGW-005/optimize", json={"method": "nsga2", "pop": 30, "gens": 12,
                                                          "limits": {"min_fillage": 0.65, "max_steam_t": 1500}, "weights": {"oil": 2.0}})
    assert r.status_code == 200
    j = r.json()
    assert j["limits"]["max_steam_t"] == 1500 and j["weights"]["oil"] == 2.0
    assert j["candidates"] and all(c["feasibility"]["status"] == "PASS" for c in j["candidates"])
    assert all(c["metrics"]["energy_per_bbl_kwh"] > 0 for c in j["candidates"])
    assert any(row["scenario_id"] == j["scenario_id"] and row["kind"] == "optimization" for row in client.get("/api/scenarios").json())


def test_sensitivity_and_risk_endpoints(client):
    s = client.get("/api/wells/BGW-001/sensitivity").json()
    assert len(s["rows"]) == 10 and s["rows"][0]["swing"] >= s["rows"][-1]["swing"]
    wid = next(w["id"] for w in client.get("/api/field").json()["wells"] if w["phase"] == "production")
    r = client.get(f"/api/wells/{wid}/risk").json()
    assert r["available"] and 0 <= r["risk_score"] <= 1 and len(r["factors"]) == 3 and r["data_label"] == "MODELLED"


def test_history_endpoints_serve_synthetic_datasets(client):
    h = client.get("/api/history/summary").json()
    assert h["data_mode"] == "SYNTHETIC" and h["n_cycles"] >= 200 and h["n_events"] >= 100
    s = client.get(f"/api/history/series?well_id={h['wells'][0]}").json()
    assert len(s["rows"]) > 100
    assert client.get("/api/data/schema").json()["columns"]["pump_fillage"]["unit"] == "%"


def test_csv_validate_then_replay_and_rejection(client):
    sample = client.get("/api/data/sample-upload?well_id=BGW-001&rows=400").text
    v = client.post("/api/data/validate", files={"file": ("s.csv", io.BytesIO(sample.encode()), "text/csv")}).json()
    assert v["ok"] and v["token"] and v["data_label"] == "USER-ENTERED"
    r = client.post("/api/data/replay", json={"token": v["token"], "well_id": "BGW-001"})
    assert r.status_code == 200 and r.json()["n"] > 100
    bad = client.post("/api/data/validate", files={"file": ("b.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}).json()
    assert not bad["ok"] and bad["errors"] and bad["token"] is None
    assert client.post("/api/data/replay", json={"token": "nope", "well_id": "x"}).status_code == 400
