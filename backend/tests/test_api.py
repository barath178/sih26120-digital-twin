"""End-to-end API tests: startup, every endpoint, approval workflow, optimiser, upload."""
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


@pytest.mark.parametrize("path", ["/api/health", "/api/config", "/api/field", "/api/wells/BGW-001", "/api/wells/BGW-001/history?n=100",
                                  "/api/wells/BGW-001/card", "/api/wells/BGW-001/forecast", "/api/wells/BGW-001/calibration",
                                  "/api/steam-schedule", "/api/alerts", "/api/recommendations", "/api/models", "/api/audit",
                                  "/api/physics/viscosity", "/api/physics/boberg-lantz", "/api/sample-csv?well_id=BGW-001"])
def test_get_endpoints(client, path):
    assert client.get(path).status_code == 200


def test_unknown_well_is_404(client):
    assert client.get("/api/wells/XX-99").status_code == 404


def test_field_snapshot(client):
    f = client.get("/api/field").json()
    assert len(f["wells"]) == 10
    assert {w["phase"] for w in f["wells"]} <= {"injection", "soak", "production"}
    assert f["kpis"]["sor"] is None or 1.0 < f["kpis"]["sor"] < 10.0


def test_approval_workflow_and_audit(client):
    recs = client.get("/api/recommendations?status=pending").json()
    assert recs, "rule engine should have proposals for the pounding wells"
    r = recs[0]
    ok = client.post(f"/api/recommendations/{r['id']}/approve", json={"by": "Test Engineer", "note": "pytest"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert client.post(f"/api/recommendations/{r['id']}/approve", json={"by": "x"}).status_code == 409
    audit = client.get("/api/audit").json()
    assert any(a["actor"] == "Test Engineer" and a["action"] == "approve" for a in audit)


def test_simulate_validation_and_result(client):
    d = client.get("/api/wells/BGW-002").json()["design"]
    design = {k: d[k] for k in ("steam_t", "inj_rate_tpd", "quality", "soak_days", "prod_days", "spm_knots", "stroke_m", "pump_depth_m", "vfd_auto", "poc")}
    r = client.post("/api/simulate", json={"well_id": "BGW-002", "design": design})
    assert r.status_code == 200
    assert abs(r.json()["delta"]["oil_per_day"]) < 1e-9  # same design -> no difference
    bad = dict(design, pump_depth_m=1190)
    assert client.post("/api/simulate", json={"well_id": "BGW-002", "design": bad}).status_code == 400


def test_optimiser_and_submit(client):
    r = client.post("/api/wells/BGW-005/optimize", json={"method": "nsga2", "pop": 30, "gens": 15})
    assert r.status_code == 200
    body = r.json()
    assert body["n_feasible"] > 0 and body["recommended"] is not None
    assert all(c["ok"] for c in body["recommended"]["summary"]["constraints"].values() if c)
    sub = client.post("/api/wells/BGW-005/optimize/submit", json={"by": "Test Engineer"})
    assert sub.status_code == 200 and sub.json()["type"] == "design"


def test_vfd_toggle(client):
    wells = client.get("/api/field").json()["wells"]
    wid = next(w["id"] for w in wells if w["phase"] == "production" and not w["vfd"])
    assert client.post(f"/api/wells/{wid}/vfd", json={"enabled": True, "by": "Test"}).json()["enabled"] is True
    assert client.post(f"/api/wells/{wid}/vfd", json={"enabled": False, "by": "Test"}).json()["enabled"] is False


def test_csv_upload_calibration(client):
    csv = client.get("/api/sample-csv?well_id=BGW-010").text
    r = client.post("/api/wells/BGW-010/upload", files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"steam_t": 1200, "inj_rate_tpd": 200, "soak_days": 5})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "calibrated"
    bad = client.post("/api/wells/BGW-010/upload", files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
                      data={"steam_t": 1200, "inj_rate_tpd": 200, "soak_days": 5})
    assert bad.status_code == 400


def test_websocket_stream(client):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "tick" and len(msg["wells"]) == 10
