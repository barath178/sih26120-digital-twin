# Architecture

Decision-support digital twin for CSS + SRP heavy-oil wells (SIH26120). Everything runs locally; the data mode is
**SYNTHETIC** and shown on every page. No component ever sends a command to field equipment.

```
  field simulator (hidden "true" wells, faults)                 digital twin (per well)
  ─────────────────────────────────────────────                ─────────────────────────────────────────
  coupled physics + sensor noise + wave-eq cards ──MQTT/bus──▶ ingest ─▶ twin model (lock-step) ─▶ residuals
                                                               ├ dynacard CNN            ├ anomaly (Isolation Forest)
                                                               ├ rod-risk classifier     ├ calibration (history match)
                                                               ├ forecaster (XGBoost)    └ failure / cycle-end prediction
                                                               alerts + recommendations ─▶ engineer approval ─▶ audit
  optimiser (NSGA-II / Bayesian) ◀─ surrogate ◀─ physics       React UI ◀── REST + WebSocket ── FastAPI
```

The Build Bible proposes Streamlit for v1 and names FastAPI + React as the migration path (sections 10.3, 15.3); this
repository implements that stack directly. The service layer is plain Python, so it can be driven from any client.

## Frontend (`frontend/src`)
Five pages: `WellTwin` (the core loop), `FieldOverview`, `Optimize` (wraps `Optimizer` and `WhatIf`), `Alerts` (Actions) and
`DataModels` (wraps `DataUpload`, `Models`, `About`). One WebSocket feeds live state; all other data comes from the REST API.

## Backend modules (`backend/app`)

| Module | Responsibility |
|---|---|
| `config.py` | Every assumption in one place, each tagged with a provenance status; source register; disclaimer text |
| `physics/fluid.py` | Walther/ASTM D341 viscosity (bounded), water, saturated steam tables |
| `physics/wellbore.py` | Ramey/Willhite steam heat loss with Hasan-Kabir time function; flowing temperature profile |
| `physics/reservoir.py` | Marx-Langenheim heated area; Boberg-Lantz cooling with exact cylinder/slab functions |
| `physics/srp.py` | API RP 11L loads, rod-fall and viscous-fill limits, Goodman; damped wave-equation dynacards; Gibbs diagnostic |
| `physics/coupling.py` | The coupled loop steam → heated zone → viscosity → inflow vs pump → oil, load, energy, risk; whole-cycle simulator |
| `physics/sensors.py` | SCADA-style measurement noise |
| `ml/dynacard.py` | CNN classifying downhole-card images (trained on physics-generated cards) |
| `ml/risk.py` | XGBoost rod-risk classifier (5 classes), SHAP-style contributions, out-of-domain detection |
| `ml/forecaster.py` | XGBoost quantile production forecast (P10/P50/P90) |
| `ml/surrogate.py` | Fast ML surrogate of the cycle simulator for the optimiser |
| `ml/anomaly.py` | Isolation Forest on twin residuals |
| `ml/calibration.py` | Physics-informed history matching of productivity, heat loss and cooling multipliers |
| `ml/health.py` | Rod fatigue (Miner) and pump-wear remaining life; marginal-value re-steam day |
| `optimize/joint.py` | Constrained multi-objective search; feasibility with reasons; uncertainty; ranked candidates |
| `optimize/steam.py` | Shared steam-generator scheduling across wells |
| `field.py` | Synthetic plant + twin runtime wired through the bus; alerts and recommendation lifecycle |
| `advisor.py` | Alerts with root cause and fix; explainable recommendations; closed-loop VFD (engineer-enabled) |
| `dataio.py` | Data dictionary, strict CSV validator (units, missingness, duplicates), replay engine |
| `exports.py` | Deterministic synthetic datasets in the documented schema |
| `bus.py`, `storage.py` | MQTT/in-process telemetry bus; SQLite (telemetry, audit, scenarios, alerts, recommendations) |
| `main.py` | REST + WebSocket API; serves the built UI |

## The coupling (Build Bible "critical coupling")
1. Steam settings set the heat injected; wellbore loss and Marx-Langenheim give the heated zone; Boberg-Lantz cools it.
2. Zone temperature sets viscosity (Walther); viscosity sets inflow, viscous barrel filling, rod drag and steam flashing.
3. Liquid produced = min(reservoir deliverability, effective pump capacity), solved as a fixed point.
4. That gives fillage, rod loads (Goodman, torque), power, oil and water; these feed SOR, energy per barrel, failure hazard
   and net value. Changing SPM, stroke or pump depth changes all of them in the same loop.

## Data flow and provenance
- Every parameter carries a status: VERIFIED_FIELD (none yet), PUBLIC_REFERENCE, HISTORICAL_REFERENCE,
  SYNTHETIC_ASSUMPTION, USER_CONFIGURED. The About page lists them all.
- UI values carry labels: MEASURED (synthetic SCADA), MODELLED, SYNTHETIC, USER-ENTERED.
- Every what-if and optimisation is written to the `scenario` table with inputs, outputs, model version and data mode.
- Approvals, alerts and control changes go to the `audit` table.

## Guardrails
- Decision support only; the twin never actuates anything. Closed-loop VFD exists only as a simulated control on the
  synthetic well and needs an engineer to enable it.
- Constraints are USER_CONFIGURED or synthetic, never presented as verified field limits.
- The optimiser is numerical and constraint-based; no LLM is involved anywhere in the numerics.
- ML scores are on synthetic data and are never called field accuracy; uploaded data is checked for out-of-domain
  inputs and strong recommendation language is suppressed there.
