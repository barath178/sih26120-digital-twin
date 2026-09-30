# Deployment

## Online demo on Vercel (no backend needed)

`frontend/vercel.json` builds `npm run build:demo` (`vite build --mode demo`).
That build answers every API call from a recorded run of the real twin (`frontend/public/demo/`, made by
`backend/scripts/record_demo.py`) and runs the what-if physics in the browser with Pyodide in a Web Worker
(`src/demo/`). It is labelled "Recorded run" in the header.

```
cd backend && python -m scripts.record_demo     # re-record after changing the twin (about 6 minutes)
cd ../frontend && vercel deploy --prod          # Vercel project root = frontend
```

What works online: every page, the replayed live stream (pause and speed controls), approve/reject/acknowledge,
VFD on/off, workover scheduling, the optimiser for each well with the default limits and weights, the field steam
plan at any budget from 60 to 120 %, the synthetic sample upload and replay, report and CSV downloads, and the
what-if simulator with real physics. What needs the live twin: fault injection, optimiser runs with custom
limits/weights, and uploading your own files.

## Live twin with a separate backend

| Part | Where | Why |
|---|---|---|
| React UI (`frontend/`) | Vercel | Static build |
| FastAPI twin (`backend/`) | Render / Railway / Fly (Docker) | Needs WebSockets, xgboost and background threads, which Vercel serverless cannot run |

Deploy the root `Dockerfile` to a Docker host (CORS is open), then build the UI with the normal `npm run build` and
`VITE_BACKEND_URL=https://<your-backend-host>` (no trailing slash). Without it the UI expects the API on the same origin.

All data in the prototype is synthetic and labelled as such in the UI.

## Render free tier
`render.yaml` builds `Dockerfile.render`, a small image for 512 MB / 0.1 CPU hosts:
no PyTorch (the dynacard CNN runs in numpy from `backend/models/dynacard_cnn.npz`, verified identical by
`tests/test_dynacard_numpy.py`), and `TWIN_BACKGROUND_START=1` so `/api/health` answers while the twin boots.
The free instance sleeps when idle; the first request after a pause takes about a minute.
It also slows the simulation clock (1 sim-hour per 5 s), generates cards every 2 sim-days, recalibrates weekly and pauses the
clock while no dashboard is connected (`TWIN_PAUSE_WHEN_IDLE=1`): about 4 % of one core with a viewer, ~0 % idle.
