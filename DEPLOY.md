# Deployment

The app has two parts:

| Part | Where | Why |
|---|---|---|
| React UI (`frontend/`) | Vercel | Static build |
| FastAPI twin (`backend/`) | Render / Railway / Fly (Docker) | Needs WebSockets, torch/xgboost and background threads, which Vercel serverless cannot run |

## 1. Backend
Deploy the root `Dockerfile` to a Docker host and note its public URL, e.g. `https://twin.onrender.com`.
CORS is open (`*`).

## 2. Frontend on Vercel
```
cd frontend
npx vercel login
npx vercel --prod          # Root Directory = frontend
```
Set the environment variable `VITE_BACKEND_URL=https://<your-backend-host>` (no trailing slash) in the Vercel project, then redeploy.
Without it the UI expects the API on the same origin.

All data in the prototype is synthetic and labelled as such in the UI.

## Render free tier
`render.yaml` builds `Dockerfile.render`, a small image for 512 MB / 0.1 CPU hosts:
no PyTorch (the dynacard CNN runs in numpy from `backend/models/dynacard_cnn.npz`, verified identical by
`tests/test_dynacard_numpy.py`), and `TWIN_BACKGROUND_START=1` so `/api/health` answers while the twin boots.
The free instance sleeps when idle; the first request after a pause takes about a minute.
