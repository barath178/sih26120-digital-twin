# Baghewala CSS + SRP Digital Twin (SIH26120 · Oil India Limited)

> **SYNTHETIC DEMO DATA. NOT LIVE OIL INDIA DATA.** This is a decision-support prototype. Its recommendations are simulated
> against synthetic data and reduced-order models. It does not control any equipment, and validated field data, engineering
> review and approved operating limits would be required before deployment.

A well-to-surface digital twin for heavy-oil wells on **Cyclic Steam Stimulation (CSS)** and **Sucker Rod Pumps (SRP)**.
The reservoir heats and cools within every cycle, so pump settings have to change with it. The twin couples the two:
steam changes the thermal state, temperature changes viscosity, viscosity changes fillage, load, production, energy and
risk, and the optimiser searches CSS and SRP settings together. Engineers approve every change.

## Run it

| Platform | Command |
|---|---|
| Windows | `.\start.ps1` |
| Linux / macOS | `./start.sh` |
| Docker + MQTT broker | `docker compose up --build` |

Then open http://127.0.0.1:8000. Requirements: Python 3.12 and Node 20+.
The first run creates the environment, trains the ML models from physics simulations and exports the synthetic CSV
datasets (about 5 minutes). Later starts take about 15 s. `.\start.ps1 -Dev` runs the UI with hot reload on :5173.

Manual commands: `cd backend`, then `python -m app.train` (models + `data/synthetic/*.csv`), `python -m app.exports`
(datasets only), `python -m pytest -q` (92 tests). In `frontend/`: `npm run build` type-checks and builds the UI.

**Online demo (Vercel, no backend):** https://sih26120-digital-twin-lilac.vercel.app is a static build (`npm run build:demo`)
that replays a recorded run of the real twin in the browser. Page data, optimiser runs, the field plan and the sample-data
replay come from that recording; approvals, acknowledgements, VFD and scheduling update state in the browser; the what-if
simulator runs the real physics (`backend/app/whatif.py`) in the browser with Pyodide, identical to the API to 1e-15.
Fault injection, custom optimiser settings and your own data uploads need the live twin (`start.ps1`). Re-record with
`cd backend && python -m scripts.record_demo` (writes `frontend/public/demo/`). See [DEPLOY.md](DEPLOY.md).

**Troubleshooting:** if port 8000 is busy, stop the other process or pass another `--port` to uvicorn. If models are
missing, run `python -m app.train`. `python -m app.train --force` retrains everything. Set `MQTT_HOST=<host>` to publish
telemetry through a real broker (falls back to an in-process bus with identical topics if unreachable).

## The app: six pages, one loop
The core loop the problem asks for is *see the coupled state → predict → get one recommendation → engineer approves*.
**Mission control** explains it in the first screen; the **Well twin** is where it runs.

| Page | Purpose |
|---|---|
| **Mission control** (landing) | What the project is and the live ₹ value at stake per year (recoverable upside, steam-efficiency gap, expected failure cost, maintenance savings), the four-step method, the coupling chain live on the priority well, top actions with one-click approval, predicted maintenance |
| **Well twin** | Cycle timeline · eight KPIs · 3D twin · "why production is what it is" (reservoir vs pump) · recommended action · alerts with root cause and fix · forecast · calibration and health · dynamometer card |
| **Field** (3 tabs) | *Overview & steam:* map, priorities, generator calendar, wells table. *Field optimiser:* optimises all ten wells under a shared steam budget. *Maintenance planner:* predicted rod/pump failures, rig calendar, savings |
| **Optimise & what-if** | Constraint- and weight-driven coupled CSS + SRP search; 3-scenario what-if with tornado |
| **Actions** | Recommendations awaiting approval, active alerts, audit trail |
| **Data & models** | CSV validation and replay, model accuracy and twin calibration, assumptions and sources |

### Field-level features
1. **Field optimiser.** Each well gets three options (keep current, steam-lean plan, full plan). An exact knapsack picks one per well to maximise total net value within the steam budget, so extra tonnes go where they pay most and cuts fall where they hurt least. Moving the budget slider re-allocates instantly. Adopted plans are submitted for engineer approval.
2. **Maintenance and rig planner.** Rod fatigue (Miner) and pump wear predict failures; each job is dated alongside the next re-steam pull when equipment will last that long, else at 80 % of remaining life; jobs within 10 days share one rig mobilisation. Shows the saving against running to failure.
3. **Value at stake.** Recoverable upside, steam-efficiency gap vs the field's best quartile, expected failure cost and maintenance savings, in ₹ per year.
4. **One-click printable field report** (`/api/report`, print to PDF) for shift handover.
5. **Mission control** as a judge-friendly first screen. Maintenance economics are SYNTHETIC_ASSUMPTIONs (listed in About & assumptions).

## Interface design
A "control room" design system (`frontend/src/index.css`, `frontend/src/components/ui.tsx`): warm bitumen-graphite neutrals, one steam-heat orange accent for the primary action and the active place, IBM Plex Sans with Plex Mono readouts, tonal surfaces instead of outlines, gauge-style KPI tiles with sparklines, and well-log tick rules. Chart series keep a separately validated categorical palette; status colours are always paired with an icon and a label.
Engineering: pages and the 3D scene are code-split (entry bundle about 250 KB), in-app confirm dialogs and toasts replace browser popups, skeleton loading states, an error boundary, keyboard-visible focus, `prefers-reduced-motion` support, and a responsive shell (instrument rail on desktop, bottom tab bar on small screens). The synthetic-data flag stays in the always-visible status bar.

## Data basis (honest)
Only these are source-backed for Baghewala: Jodhpur Sandstone, about 17-19° API, reservoir temperature 46-48 °C, thermal
completions with VIT, SRP + CSS operation, and about 13,000 cP at 50 °C in an older OIL tender (the fitted model gives
11,300 cP). Depth 1050-1300 m, pay 5-23 m, porosity 18-20 %, permeability below 1000 mD and BHP examples come from a
**historical** OIL tender and are used only as an engineering envelope. All wells, telemetry, cards, events and costs are
generated; every parameter and its status is listed on the About page and in `backend/app/config.py`.

## Results you can reproduce (synthetic test sets only, not field accuracy)
| Model | Result |
|---|---|
| Dynacard CNN, 7 classes | 99 % accuracy |
| Rod-risk classifier, 5 classes | 99 % accuracy, macro-F1 0.96; PUMP_UNSETTING has only about 18 test rows, so its score is uncertain |
| Production forecaster | 3.5 % median error, 83 % P10-P90 coverage |
| Optimiser surrogate | R² 0.99 (oil), 0.98 (net value), 0.74 (SOR, energy per barrel); every design is re-verified by full physics |

## 5-minute demo
1. **Well twin (opens on the highest-priority well).** Read the timeline and KPIs, then "Why production is what it is": the reservoir delivers far less than the pump can lift, so the barrel is 15-30 % full and the plunger pounds.
2. **Same page, BGW-008:** the rod-fatigue alert predicts failure in about 4 weeks. Approve "Enable closed-loop VFD" and watch SPM drop and fillage rise to about 85 %; the rod-failure estimate moves out to years.
3. **BGW-004:** pump wear from card-classifier leak diagnoses and a falling load-retention trend.
4. **Optimise & what-if → BGW-007:** run the search (constraints and weights are editable), open a candidate's "Why?", then submit it. On the what-if tab, build scenarios A, B, C against the current plan.
5. **Actions:** approve the submitted plan and show the audit trail.
6. **Field & steam:** the map, the shared-generator calendar and the wells table.
7. **Data & models:** upload the synthetic sample (or a wrong-unit file to see it rejected), replay it, then show model accuracy and the assumptions register, and finish by separating prototype evidence from field validation.

## Limitations
- No OIL data was used or invented. Model coefficients are not field-calibrated and are not a commercial simulator replacement.
- ML metrics are on synthetic data. The risk classes' labels are rules on simulator truth; the model estimates them from noisy inputs.
- Well coordinates on the map are illustrative. Storage is SQLite (a TimescaleDB-shaped schema).
- The MQTT path was tested against a local broker; the Docker image was not built in this environment.
- With these assumptions the model tends to favour longer cycles; treat "extend cycle" advice as an output of the assumptions, not a finding.

See `architecture.md` for the module-by-module design.
