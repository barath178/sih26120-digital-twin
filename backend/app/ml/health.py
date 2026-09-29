"""Equipment health and cycle-end prediction.

* Rod-string remaining useful life: Miner's-rule fatigue damage (S-N curve on the modified
  Goodman loading, amplified by fluid-pound shock) accumulated by the twin; RUL = remaining
  damage / recent damage rate.
* Pump wear: travelling-valve / plunger leakage shows up on the downhole card as a loss of
  upstroke load retention. A robust linear trend of that metric is extrapolated to the
  workover threshold.
* Optimal re-steam day (marginal-value rule): continue the cycle while the daily net value
  exceeds the cycle-average net value (downtime included); re-steam when it falls below."""
from __future__ import annotations

import numpy as np

from .. import config
from ..physics import coupling

ECON = config.ECONOMICS
RETENTION_THRESHOLD = 0.62


def rod_rul(damage: float, damage_rate_per_day: float) -> float | None:
    if damage_rate_per_day <= 1e-12:
        return None
    return max((1.0 - damage) / damage_rate_per_day, 0.0)


def pump_wear_rul(days: list[float], retention: list[float], threshold: float = RETENTION_THRESHOLD) -> dict:
    if len(days) < 6:
        return dict(days_to_threshold=None, slope_per_day=None, current=retention[-1] if retention else None)
    d = np.asarray(days[-60:], float)   # ~15 days of 6-hourly cards
    r = np.asarray(retention[-60:], float)
    # Theil-Sen slope (robust to misclassified cards)
    slopes = [(r[j] - r[i]) / (d[j] - d[i]) for i in range(len(d)) for j in range(i + 1, len(d)) if d[j] - d[i] > 1.0]
    if not slopes:
        return dict(days_to_threshold=None, slope_per_day=None, current=float(r[-1]))
    slope = float(np.median(slopes))
    current = float(np.median(r[-8:]))
    if current <= threshold:
        return dict(days_to_threshold=0.0, slope_per_day=slope, current=current)
    if slope >= -1e-4:
        return dict(days_to_threshold=None, slope_per_day=slope, current=current)
    return dict(days_to_threshold=float((threshold - current) / slope), slope_per_day=slope, current=current)


def cycle_cash_so_far(state: coupling.CycleState) -> float:
    price = ECON["oil_price_inr_per_m3"]
    return (state.np_cycle * price - state.steam_injected_t * config.STEAM["fuel_cost_inr_per_tonne"]
            - state.energy_kwh_cycle * ECON["electricity_inr_per_kwh"] - state.wp_cycle * ECON["water_handling_inr_per_m3"])


def forward_projection(params: coupling.WellParams, state: coupling.CycleState, design: coupling.CycleDesign,
                       spm_override: float | None = None, max_days: int = 300) -> dict:
    """Continue the twin forward from `state` (production phase) day by day.
    Returns the projected oil series and the optimal re-steam day by the marginal-value rule."""
    st = state.copy()
    guard = 0
    while st.phase != "production" and guard < 400:
        guard += 1
        coupling.advance(params, st, design, 0.25)
    cash = cycle_cash_so_far(st)
    elapsed = max(st.day_in_cycle, 1e-6)
    days, q, cash_rate = [], [], []
    optimal = None
    for k in range(max_days):
        out = coupling.advance(params, st, design, 1.0, spm_override)
        rate = coupling.daily_economics(out, 1.0, design)
        days.append(k)
        q.append(out["q_oil"])
        cash_rate.append(rate)
        avg = cash / elapsed
        if optimal is None and rate < avg and k >= 0:
            optimal = k
        cash += rate
        elapsed += 1.0
        if optimal is not None and k > optimal + 30 and k >= design.prod_days - state.t_phase_d:
            break
    return dict(days=days, q_oil=q, cash_rate=cash_rate, optimal_resteam_in_days=optimal,
                planned_days_left=max(design.prod_days - state.t_phase_d, 0.0))
