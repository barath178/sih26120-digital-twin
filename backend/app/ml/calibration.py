"""Physics-informed calibration (history matching).

The twin replays the current cycle from its start state with the settings the plant really
used, and adjusts three uncertain multipliers so that simulated rates and wellhead
temperature match the measured daily data:
    pi_mult   - effective permeability / productivity multiplier
    u_mult    - wellbore heat-loss coefficient multiplier (Ramey)
    cool_mult - heated-zone thermal diffusivity multiplier (Boberg-Lantz cooling rate)
A weak prior keeps the problem well-posed when the data are not yet informative."""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
from scipy.optimize import least_squares

from ..physics import coupling

PARAMS = ["pi_mult", "u_mult", "cool_mult"]
PRIOR_SIGMA = [1.0, 0.35, 0.35]  # in log space
BOUNDS = (np.log([0.2, 0.4, 0.4]), np.log([4.0, 2.5, 2.5]))


def replay(params: coupling.WellParams, design: coupling.CycleDesign, state0: coupling.CycleState, spm_days: list[float]):
    """Re-simulate a cycle from its start with the recorded daily SPM. Returns per-production-day
    mean outputs (q_oil, q_water, wht) and the state at the end of the replay."""
    st = state0.copy()
    guard = 0
    while st.phase != "production" and guard < 400:
        guard += 1
        coupling.advance(params, st, design, 0.25)
    q_o, q_w, wht = [], [], []
    for spm in spm_days:
        acc = np.zeros(3)
        for _ in range(2):
            o = coupling.advance(params, st, design, 0.5, spm)
            acc += (o["q_oil"], o["q_water"], o["wht"])
        acc /= 2.0
        q_o.append(acc[0])
        q_w.append(acc[1])
        wht.append(acc[2])
    return np.asarray(q_o), np.asarray(q_w), np.asarray(wht), st


def fit_stats(meas_oil, pred_oil) -> dict:
    meas_oil = np.asarray(meas_oil)
    pred_oil = np.asarray(pred_oil)
    if len(meas_oil) == 0:
        return dict(mape=None, r2=None)
    mape = float(np.mean(np.abs(pred_oil - meas_oil) / np.maximum(meas_oil, 0.3)))
    ss_tot = float(np.sum((meas_oil - meas_oil.mean()) ** 2))
    r2 = 1.0 - float(np.sum((pred_oil - meas_oil) ** 2)) / ss_tot if ss_tot > 1e-9 else None
    return dict(mape=mape, r2=r2)


def calibrate(params: coupling.WellParams, design: coupling.CycleDesign, state0: coupling.CycleState, records: list[dict],
              max_nfev: int = 30) -> dict:
    """records: per production day {spm, q_oil, q_water, wht} (measured daily means)."""
    if len(records) < 5:
        return dict(status="insufficient data", params={k: getattr(params, k) for k in PARAMS}, n_days=len(records))
    # days with a detected mechanical failure produced nothing: replay them shut in, but do not fit them
    spm = [r["spm"] if r.get("valid", True) else 0.0 for r in records]
    m_oil = np.array([r["q_oil"] for r in records])
    m_w = np.array([r["q_water"] for r in records])
    m_t = np.array([r["wht"] for r in records])
    valid = np.array([1.0 if r.get("valid", True) else 0.0 for r in records])
    if valid.sum() < 5:
        return dict(status="insufficient data", params={k: getattr(params, k) for k in PARAMS}, n_days=int(valid.sum()))
    x0 = np.log([getattr(params, k) for k in PARAMS])
    prior = x0.copy()
    x0 = np.clip(x0, BOUNDS[0] + 1e-6, BOUNDS[1] - 1e-6)
    # recent data weigh more (the twin must be right *now*)
    w = np.linspace(0.6, 1.4, len(records)) * valid

    def resid(x):
        trial = replace(params, **{k: float(math.exp(v)) for k, v in zip(PARAMS, x)})
        q_o, q_w, t, _ = replay(trial, design, state0, spm)
        r = np.concatenate([
            w * (np.log(q_o + 0.2) - np.log(m_oil + 0.2)),
            0.5 * w * (np.log(q_w + 0.2) - np.log(m_w + 0.2)),
            0.3 * w * (t - m_t) / 5.0,
            (x - prior) / np.asarray(PRIOR_SIGMA) * 0.3,
        ])
        return r

    before = replay(params, design, state0, spm)
    sol = least_squares(resid, x0, bounds=BOUNDS, max_nfev=max_nfev, x_scale=0.3, diff_step=0.02)
    new = {k: float(math.exp(v)) for k, v in zip(PARAMS, sol.x)}
    tuned = replace(params, **new)
    q_o, q_w, t, end_state = replay(tuned, design, state0, spm)
    ok_idx = np.where(valid > 0)[0]
    recent = ok_idx[-14:]
    stats_after = fit_stats(m_oil[recent], q_o[recent])
    stats_before = fit_stats(m_oil[recent], before[0][recent])
    mape = stats_after["mape"] if stats_after["mape"] is not None else 1.0
    confidence = float(np.clip(1.0 - mape / 0.3, 0.0, 1.0)) * min(1.0, len(recent) / 14.0)
    return dict(
        status="calibrated", params=new, previous={k: getattr(params, k) for k in PARAMS},
        mape_recent=stats_after["mape"], r2_recent=stats_after["r2"], mape_before=stats_before["mape"],
        confidence=confidence, n_days=int(valid.sum()), nfev=int(sol.nfev), end_state=end_state,
        fit_series=dict(day=list(range(len(records))), measured_oil=m_oil.tolist(), model_oil=q_o.tolist(),
                        measured_water=m_w.tolist(), model_water=q_w.tolist()),
    )
