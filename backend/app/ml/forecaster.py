"""Production forecaster: XGBoost quantile regression (P10 / P50 / P90) of the daily oil rate
for the remainder of the current CSS cycle, from field-measurable features only."""
from __future__ import annotations

import json
import logging
import math
import time

import numpy as np

from .. import config
from ..physics import coupling
from . import scenarios

log = logging.getLogger(__name__)
MODEL_PATH = config.MODELS_DIR / "forecaster.json"
METRICS_PATH = config.MODELS_DIR / "forecaster_metrics.json"
QUANTILES = [0.1, 0.5, 0.9]
FEATURES = ["day", "horizon", "log_q3", "log_q_prev", "slope", "log_qw3", "wht3", "spm3", "log_cum", "steam_t",
            "soak_days", "inj_rate", "log_np_before"]


def features(q_oil, q_water, wht, spm, k: int, horizon: int, steam_t: float, soak: float, inj_rate: float, np_before: float):
    q_oil = np.asarray(q_oil, float)
    lo = max(0, k - 2)
    q3 = float(np.mean(q_oil[lo:k + 1]))
    prev_lo, prev_hi = max(0, k - 9), max(1, k - 6)
    q_prev = float(np.mean(q_oil[prev_lo:prev_hi]))
    return [
        k, horizon, math.log1p(q3), math.log1p(q_prev), math.log1p(q3) - math.log1p(q_prev),
        math.log1p(float(np.mean(np.asarray(q_water)[lo:k + 1]))), float(np.mean(np.asarray(wht)[lo:k + 1])),
        float(np.mean(np.asarray(spm)[lo:k + 1])), math.log1p(float(np.sum(q_oil[:k + 1]))), steam_t, soak, inj_rate,
        math.log1p(np_before),
    ]


def _simulate_training_cycles(n_cycles: int, seed: int):
    rng = np.random.default_rng(seed)
    cycles = []
    for i in range(n_cycles):
        w = scenarios.sample_well(rng)
        v = scenarios.sample_design_vec(rng)
        p, st = scenarios.well_from_features(w)
        d = scenarios.design_from_vec(v, p.depth_m, poc=bool(rng.random() < 0.6))
        d.vfd_auto = bool(rng.random() < 0.3)
        d.prod_days = float(rng.uniform(90, 240))
        res = coupling.simulate_cycle(p, d, state0=None if w["np_total_m3"] == 0 else st, dt_d=1.0)
        ser = res["series"]
        idx = [j for j, ph in enumerate(ser["phase"]) if ph == "production"]
        if len(idx) < 30:
            continue
        noise = lambda a, rel: np.maximum(np.asarray(a) * (1 + rng.normal(0, rel, len(a))), 0.0)
        cycles.append(dict(
            q_oil=noise([ser["q_oil"][j] for j in idx], 0.05), q_water=noise([ser["q_water"][j] for j in idx], 0.05),
            wht=np.asarray([ser["wht"][j] for j in idx]) + rng.normal(0, 1.5, len(idx)), spm=np.asarray([ser["spm"][j] for j in idx]),
            true_q=np.asarray([ser["q_oil"][j] for j in idx]), steam_t=d.steam_t, soak=d.soak_days, inj=d.inj_rate_tpd,
            np_before=w["np_total_m3"],
        ))
    return cycles


def _build_rows(cycles, rng):
    x, y = [], []
    for c in cycles:
        n = len(c["q_oil"])
        for k in range(4, n - 5, 6):
            for h in sorted(set(rng.integers(1, n - k, size=min(8, n - k - 1)).tolist())):
                x.append(features(c["q_oil"], c["q_water"], c["wht"], c["spm"], k, h, c["steam_t"], c["soak"], c["inj"], c["np_before"]))
                y.append(math.log1p(c["true_q"][k + h]))
    return np.asarray(x, float), np.asarray(y, float)


class Forecaster:
    def __init__(self):
        self.model = None
        self.metrics: dict = {}

    def train(self, n_cycles: int = 1200, seed: int = 11):
        import xgboost as xgb

        t0 = time.time()
        cycles = _simulate_training_cycles(n_cycles, seed)
        rng = np.random.default_rng(seed)
        n_test = len(cycles) // 5
        test_c, train_c = cycles[:n_test], cycles[n_test:]
        xtr, ytr = _build_rows(train_c, rng)
        xte, yte = _build_rows(test_c, rng)
        model = xgb.XGBRegressor(objective="reg:quantileerror", quantile_alpha=np.array(QUANTILES), n_estimators=400,
                                 max_depth=6, learning_rate=0.05, subsample=0.8, colsample_bytree=0.9, tree_method="hist")
        model.fit(xtr, ytr)
        pred = model.predict(xte)
        q_true = np.expm1(yte)
        q50 = np.expm1(pred[:, 1])
        mape = float(np.mean(np.abs(q50 - q_true) / np.maximum(q_true, 0.3)))
        coverage = float(np.mean((yte >= pred[:, 0]) & (yte <= pred[:, 2])))
        ss_res = float(np.sum((pred[:, 1] - yte) ** 2))
        r2 = 1.0 - ss_res / float(np.sum((yte - yte.mean()) ** 2))
        self.model = model
        model.save_model(str(MODEL_PATH))
        self.metrics = dict(model="XGBoost quantile regression", quantiles=QUANTILES, mape_p50=mape, r2_log=r2,
                            p10_p90_coverage=coverage, n_train_rows=int(len(ytr)), n_test_rows=int(len(yte)),
                            n_cycles=len(cycles), features=FEATURES, train_seconds=round(time.time() - t0, 1))
        METRICS_PATH.write_text(json.dumps(self.metrics, indent=2))
        log.info("forecaster trained: MAPE=%.3f coverage=%.2f", mape, coverage)
        return self.metrics

    def load(self) -> bool:
        if not (MODEL_PATH.exists() and METRICS_PATH.exists()):
            return False
        import xgboost as xgb

        self.model = xgb.XGBRegressor()
        self.model.load_model(str(MODEL_PATH))
        self.metrics = json.loads(METRICS_PATH.read_text())
        return True

    def ensure(self):
        if self.model is None and not self.load():
            self.train()

    def forecast(self, q_oil, q_water, wht, spm, steam_t, soak, inj_rate, np_before, horizon_days: int):
        """Returns list of dicts {horizon, p10, p50, p90} for 1..horizon_days."""
        self.ensure()
        k = len(q_oil) - 1
        if k < 2 or horizon_days < 1:
            return []
        rows = [features(q_oil, q_water, wht, spm, k, h, steam_t, soak, inj_rate, np_before) for h in range(1, horizon_days + 1)]
        pred = np.expm1(self.model.predict(np.asarray(rows, float)))
        pred = np.sort(np.maximum(pred, 0.0), axis=1)
        return [dict(horizon=h + 1, p10=float(pred[h, 0]), p50=float(pred[h, 1]), p90=float(pred[h, 2])) for h in range(len(rows))]


forecaster = Forecaster()
