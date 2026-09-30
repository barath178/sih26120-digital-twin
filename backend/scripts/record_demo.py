"""Record a run of the live twin for the static (Vercel) demo.

Runs the real backend in-process, captures the WebSocket stream, every GET the UI makes, and the
results of the UI's actions (optimiser, field plan, calibration, sample upload), then writes them to
frontend/public/demo/. The browser replays the stream and serves these responses; what-if runs the
real physics in the browser (Pyodide) from each well's recorded state.

    cd backend && python -m scripts.record_demo
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("TICK_SECONDS", "0.25")

from fastapi.testclient import TestClient  # noqa: E402

from app import main, whatif  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "public" / "demo"
WELLS = [f"BGW-{i:03d}" for i in range(1, 11)]
N_TICKS = int(os.getenv("DEMO_TICKS", "240"))
OPT_BODY = dict(pop=80, gens=60, trials=120,
                limits=dict(min_fillage=0.6, max_goodman=0.95, max_risk=0.35, max_steam_t=2000, max_motor_kw=37),
                weights=dict(oil=3, sor=1, energy=0.5, risk=0.5))


def log(*a):
    print(*a, flush=True)


def raw_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.strip("/"))


def main_() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "raw").mkdir(parents=True)
    api: dict[str, object] = {}          # "GET /path" -> JSON response
    raw: dict[str, str] = {}             # "/path" -> file under demo/raw (non-JSON responses)
    posts: dict[str, object] = {}        # action key -> recorded result

    with TestClient(main.app) as c:
        t0 = time.time()
        while c.get("/api/health").json()["status"] != "ok":
            time.sleep(0.5)
        log(f"twin ready in {time.time() - t0:.0f}s")

        def get_json(path: str):
            r = c.get(path)
            r.raise_for_status()
            api["GET " + path] = r.json()
            return api["GET " + path]

        def get_raw(path: str, ext: str):
            r = c.get(path)
            r.raise_for_status()
            name = raw_name(path) + ext
            (OUT / "raw" / name).write_bytes(r.content)
            raw[path] = f"raw/{name}"
            return r

        # 1. live stream
        stream = []
        with c.websocket_connect("/ws") as ws:
            while len(stream) < N_TICKS:
                msg = json.loads(ws.receive_text())
                if msg.get("type") == "tick":
                    stream.append(msg)
        c.post("/api/sim/control", json=dict(paused=True))
        log(f"stream: {len(stream)} ticks, {stream[0]['sim_time']} -> {stream[-1]['sim_time']}")

        # 2. every page's data at the end of the stream
        for p in ["/api/provenance", "/api/recommendations", "/api/alerts", "/api/audit?limit=300", "/api/data/schema",
                  "/api/steam-schedule", "/api/field/plan", "/api/mission", "/api/maintenance-plan", "/api/models", "/api/config"]:
            get_json(p)
        for wid in WELLS:
            for p in [f"/api/wells/{wid}", f"/api/wells/{wid}/history?n=2400&max_points=500", f"/api/wells/{wid}/card",
                      f"/api/wells/{wid}/forecast?horizon=120", f"/api/wells/{wid}/risk", f"/api/wells/{wid}/sensitivity"]:
                get_json(p)
            get_raw(f"/api/sample-csv?well_id={wid}", ".csv")
        get_raw("/api/report", ".html")
        for f in api["GET /api/data/schema"]["files"]:
            get_raw(f"/api/data/files/{f}", "")
        log(f"GETs: {len(api)} json, {len(raw)} files")

        # 3. what-if context per well (the browser re-runs the physics from this state)
        fld = main.field
        for wid in WELLS:
            w = fld.by_id[wid]
            cur = w.next_design or w.design
            ctx = dict(params=asdict(w.twin_p), state=asdict(w.twin), current=whatif.design_dict(cur))
            posts["ctx " + wid] = ctx
            # reference result from the API, used by the parity check below
            body = dict(well_id=wid, design={k: ctx["current"][k] for k in ("steam_t", "inj_rate_tpd", "quality", "soak_days", "prod_days",
                                                                              "spm_knots", "stroke_m", "pump_depth_m", "poc")} | dict(vfd_auto=False),
                        compare_current=True)
            ref = c.post("/api/simulate", json=body).json()
            posts["whatif-check " + wid] = dict(body=body, summary=ref["summary"])

        def recs():
            return {r["id"]: r for r in c.get("/api/recommendations").json()}

        def diff(before: dict, after: dict) -> list:
            return [r for k, r in after.items() if k not in before or before[k] != r]

        # 4. calibration and optimiser per well
        for wid in WELLS:
            r = c.post(f"/api/wells/{wid}/calibrate")
            posts[f"calibrate {wid}"] = dict(status=r.status_code, body=r.json())
            for method in ("bayes", "nsga2"):
                t = time.time()
                r = c.post(f"/api/wells/{wid}/optimize", json=OPT_BODY | dict(method=method))
                res = r.json()
                entry = dict(status=r.status_code, body=res)
                if r.status_code == 200 and res.get("recommended"):
                    before = recs()
                    s = c.post(f"/api/wells/{wid}/optimize/submit", json=dict(by="Field Engineer"))
                    entry["submit"] = dict(status=s.status_code, body=s.json(), recs=diff(before, recs()))
                posts[f"optimize {wid} {method}"] = entry
                log(f"optimize {wid} {method}: {r.status_code} in {time.time() - t:.0f}s")

        # 5. field plan: full run, instant re-allocation at every budget, submit
        c.post("/api/field/plan", json=dict(budget_pct=100))
        t = time.time()
        while (plan := c.get("/api/field/plan").json())["status"] == "running":
            time.sleep(1)
        log(f"field plan: {plan['status']} in {time.time() - t:.0f}s")
        realloc = {}
        for pct in range(60, 121):
            realloc[str(pct)] = c.post("/api/field/plan/reallocate", json=dict(budget_pct=pct)).json()
        posts["fieldplan realloc"] = realloc
        c.post("/api/field/plan/reallocate", json=dict(budget_pct=100))
        before = recs()
        s = c.post("/api/field/plan/submit", json=dict(by="Field Engineer"))
        posts["fieldplan submit"] = dict(status=s.status_code, body=s.json(), recs=diff(before, recs()))

        # 6. sample upload -> validate -> replay
        txt = c.get("/api/data/sample-upload?well_id=BGW-001&rows=700").text
        api["GET /api/data/sample-upload?well_id=BGW-001&rows=700"] = txt
        v = c.post("/api/data/validate", files=dict(file=("BGW-001_synthetic_sample.csv", io.BytesIO(txt.encode()), "text/csv"))).json()
        posts["validate sample"] = v
        posts["replay sample"] = {wid: c.post("/api/data/replay", json=dict(token=v["token"], well_id=wid)).json() for wid in v["wells"]}

    (OUT / "stream.json").write_text(json.dumps(stream, separators=(",", ":")))
    (OUT / "api.json").write_text(json.dumps(api, separators=(",", ":")))
    (OUT / "actions.json").write_text(json.dumps(posts, separators=(",", ":")))
    (OUT / "raw.json").write_text(json.dumps(raw, separators=(",", ":")))

    # physics sources for the in-browser what-if
    src = ROOT / "backend" / "app"
    py = OUT / "py" / "app"
    (py / "physics").mkdir(parents=True)
    for rel in ["__init__.py", "config.py", "whatif.py", "physics/__init__.py", "physics/coupling.py", "physics/fluid.py",
                "physics/reservoir.py", "physics/srp.py", "physics/wellbore.py", "physics/sensors.py"]:
        shutil.copy(src / rel, py / rel)
    (OUT / "py" / "files.json").write_text(json.dumps(sorted(str(p.relative_to(OUT / "py")).replace("\\", "/") for p in py.rglob("*.py"))))
    (OUT / "meta.json").write_text(json.dumps(dict(recorded_at=time.strftime("%Y-%m-%d %H:%M"), ticks=len(stream),
                                                   sim_from=stream[0]["sim_time"], sim_to=stream[-1]["sim_time"])))
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    log(f"wrote {OUT} ({total / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main_())
