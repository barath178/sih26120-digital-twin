"""Fast ML surrogate of the coupled CSS + SRP cycle simulator.

Inputs : 10 design variables (steam volume, injection rate, soak, cycle length, 4 SPM knots,
         stroke length, pump setting depth) + 6 well-state variables.
Outputs: every objective and constraint the optimiser needs. One gradient-boosted model per
         output; evaluates ~100k scenarios per second, so NSGA-II can explore freely and only
         the final Pareto set is re-verified with full physics."""
from __future__ import annotations

import json
import logging
import time

import joblib
import numpy as np

from .. import config
from ..physics import coupling
from . import scenarios

log = logging.getLogger(__name__)
MODEL_PATH = config.MODELS_DIR / "surrogate.joblib"
METRICS_PATH = config.MODELS_DIR / "surrogate_metrics.json"
INPUTS = scenarios.DESIGN_KEYS + scenarios.WELL_KEYS
OUTPUTS = ["oil_per_day", "sor", "failure_prob", "npv_per_day", "avg_fillage", "max_goodman", "max_torque_nm",
           "max_motor_kw", "rodfall_ratio", "energy_per_bbl_kwh"]
LOG_OUTPUTS = {"sor", "oil_per_day", "energy_per_bbl_kwh"}
HAZARD_OUTPUTS = {"failure_prob"}  # learnt as log cumulative hazard: log(-log(1-p))


def _to_target(name: str, v: np.ndarray) -> np.ndarray:
    if name in LOG_OUTPUTS:
        return np.log(np.maximum(v, 1e-3))
    if name in HAZARD_OUTPUTS:
        return np.log(-np.log1p(-np.clip(v, 1e-6, 1 - 1e-6)))
    return v


def _from_target(name: str, v: np.ndarray) -> np.ndarray:
    if name in LOG_OUTPUTS:
        return np.exp(v)
    if name in HAZARD_OUTPUTS:
        return 1.0 - np.exp(-np.exp(v))
    return v


def physics_eval(design_vec: dict, well_feat: dict) -> dict:
    p, st = scenarios.well_from_features(well_feat)
    d = scenarios.design_from_vec(design_vec, p.depth_m, poc=True)
    res = coupling.simulate_cycle(p, d, state0=st if well_feat["np_total_m3"] > 0 else None, dt_d=1.0, record=False)
    return res["summary"]


def generate(n: int, seed: int):
    rng = np.random.default_rng(seed)
    x, y = [], []
    for _ in range(n):
        w = scenarios.sample_well(rng)
        v = scenarios.sample_design_vec(rng)
        s = physics_eval(v, w)
        x.append([v[k] for k in scenarios.DESIGN_KEYS] + [w[k] for k in scenarios.WELL_KEYS])
        y.append([s[k] for k in OUTPUTS])
    return np.asarray(x, float), np.asarray(y, float)


class Surrogate:
    def __init__(self):
        self.models: dict | None = None
        self.metrics: dict = {}

    def train(self, n: int = 4000, seed: int = 21):
        import xgboost as xgb

        t0 = time.time()
        x, y = generate(n, seed)
        gen_s = time.time() - t0
        n_test = n // 5
        xte, yte, xtr, ytr = x[:n_test], y[:n_test], x[n_test:], y[n_test:]
        models, r2, mae = {}, {}, {}
        for j, name in enumerate(OUTPUTS):
            tr_y = _to_target(name, ytr[:, j])
            m = xgb.XGBRegressor(n_estimators=500, max_depth=6, learning_rate=0.05, subsample=0.85, colsample_bytree=0.9,
                                 tree_method="hist")
            m.fit(xtr, tr_y)
            pred = _from_target(name, m.predict(xte))
            mae[name] = float(np.mean(np.abs(pred - yte[:, j])))
            ss_res = float(np.sum((pred - yte[:, j]) ** 2))
            ss_tot = float(np.sum((yte[:, j] - yte[:, j].mean()) ** 2)) or 1.0
            r2[name] = 1.0 - ss_res / ss_tot
            models[name] = m
        self.models = models
        joblib.dump(models, MODEL_PATH)
        self.metrics = dict(model="XGBoost (one regressor per output)", inputs=INPUTS, outputs=OUTPUTS, r2=r2, mae=mae, n_train=int(len(xtr)),
                            n_test=int(n_test), data_gen_seconds=round(gen_s, 1), train_seconds=round(time.time() - t0 - gen_s, 1))
        METRICS_PATH.write_text(json.dumps(self.metrics, indent=2))
        log.info("surrogate trained: %s", {k: round(v, 3) for k, v in r2.items()})
        return self.metrics

    def load(self) -> bool:
        if not (MODEL_PATH.exists() and METRICS_PATH.exists()):
            return False
        self.models = joblib.load(MODEL_PATH)
        self.metrics = json.loads(METRICS_PATH.read_text())
        return True

    def ensure(self):
        if self.models is None and not self.load():
            self.train()

    def predict(self, x: np.ndarray) -> dict:
        """x: (N, len(INPUTS)) -> dict of output arrays."""
        self.ensure()
        out = {}
        for name, m in self.models.items():
            out[name] = _from_target(name, m.predict(x))
        return out


surrogate = Surrogate()
