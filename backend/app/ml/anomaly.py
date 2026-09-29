"""Anomaly detection on twin residuals (measured - twin-predicted), Isolation Forest.

Because the twin removes the expected physics (cycle decline, viscosity changes, SPM moves),
what is left in the residuals is either noise, slow model drift (handled by calibration) or
equipment trouble. The forest is trained on residuals from healthy, calibrated wells."""
from __future__ import annotations

import json
import logging
import time

import joblib
import numpy as np

from .. import config
from ..physics import coupling, sensors
from . import scenarios

log = logging.getLogger(__name__)
MODEL_PATH = config.MODELS_DIR / "anomaly_iforest.joblib"
METRICS_PATH = config.MODELS_DIR / "anomaly_metrics.json"
FEATURES = ["liquid_rate", "pprl", "mprl", "motor_kw", "wht", "fillage"]


def residual_vector(meas: dict, twin: dict) -> np.ndarray:
    """meas / twin are sensors.ideal()-style dicts (twin noise-free)."""
    pprl_ref = max(twin["pprl_kn"], 10.0)
    return np.array([
        (meas["liquid_rate_m3d"] - twin["liquid_rate_m3d"]) / max(twin["liquid_rate_m3d"], 2.0),
        (meas["pprl_kn"] - twin["pprl_kn"]) / pprl_ref,
        (meas["mprl_kn"] - twin["mprl_kn"]) / pprl_ref,
        (meas["motor_kw"] - twin["motor_kw"]) / max(twin["motor_kw"], 1.0),
        (meas["wht_c"] - twin["wht_c"]) / 20.0,
        meas["fillage"] - twin["fillage"],
    ])


def _training_residuals(n_cycles: int, seed: int):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n_cycles):
        w = scenarios.sample_well(rng)
        v = scenarios.sample_design_vec(rng)
        p_twin, st = scenarios.well_from_features(w)
        d = scenarios.design_from_vec(v, p_twin.depth_m, poc=bool(rng.random() < 0.5))
        d.prod_days = 120.0
        p_true = coupling.WellParams(**{**p_twin.to_dict(), "pi_mult": p_twin.pi_mult * rng.uniform(0.9, 1.1),
                                        "cool_mult": p_twin.cool_mult * rng.uniform(0.93, 1.07)})
        s_true, s_twin = st.copy(), st.copy()
        coupling.start_new_cycle(s_true)
        coupling.start_new_cycle(s_twin)
        guard = 0
        while guard < 400:
            guard += 1
            if s_true.phase == "production" and s_true.t_phase_d >= d.prod_days:
                break
            dt = 0.5 if s_true.phase != "production" else 1.0
            spm = d.spm_at(s_true.t_phase_d) if s_true.phase == "production" else None
            o_true = coupling.advance(p_true, s_true, d, dt, spm)
            o_twin = coupling.advance(p_twin, s_twin, d, dt, spm)
            if o_true["phase"] != "production":
                continue
            meas = sensors.measure(o_true, rng)
            rows.append(residual_vector(meas, sensors.ideal(o_twin)))
    return np.asarray(rows)


class AnomalyDetector:
    def __init__(self):
        self.model = None
        self.metrics: dict = {}
        self.scale = None

    def train(self, n_cycles: int = 250, seed: int = 31):
        from sklearn.ensemble import IsolationForest

        t0 = time.time()
        x = _training_residuals(n_cycles, seed)
        model = IsolationForest(n_estimators=250, contamination="auto", random_state=seed)
        model.fit(x)
        raw = -model.score_samples(x)
        thr = float(np.quantile(raw, 0.995))
        base = float(np.quantile(raw, 0.5))
        self.scale = x.std(axis=0).tolist()
        self.model = model
        joblib.dump(dict(model=model, thr=thr, base=base, scale=self.scale), MODEL_PATH)
        self.metrics = dict(model="Isolation Forest on twin residuals", features=FEATURES, n_train=int(len(x)),
                            threshold_raw=thr, median_raw=base, feature_std=dict(zip(FEATURES, self.scale)),
                            train_seconds=round(time.time() - t0, 1))
        self._thr, self._base = thr, base
        METRICS_PATH.write_text(json.dumps(self.metrics, indent=2))
        log.info("anomaly detector trained on %d residual vectors", len(x))
        return self.metrics

    def load(self) -> bool:
        if not (MODEL_PATH.exists() and METRICS_PATH.exists()):
            return False
        blob = joblib.load(MODEL_PATH)
        self.model, self._thr, self._base, self.scale = blob["model"], blob["thr"], blob["base"], blob["scale"]
        self.metrics = json.loads(METRICS_PATH.read_text())
        return True

    def ensure(self):
        if self.model is None and not self.load():
            self.train()

    def score_batch(self, resids: np.ndarray) -> list[dict]:
        """Score many residual vectors in one call (the forest has a large per-call overhead)."""
        self.ensure()
        raw = -self.model.score_samples(np.atleast_2d(resids))
        out = []
        for r, x in zip(raw, np.atleast_2d(resids)):
            score = (float(r) - self._base) / max(self._thr - self._base, 1e-9)
            z = {f: float(x[i] / max(self.scale[i], 1e-6)) for i, f in enumerate(FEATURES)}
            out.append(dict(score=float(np.clip(score, 0.0, 2.0)), anomalous=bool(r > self._thr), z=z))
        return out

    def score(self, resid: np.ndarray) -> dict:
        """Return anomaly score in [0,1] (>=1 means beyond the 99.5 % healthy threshold, clipped)
        and the residual z-scores for explanation."""
        self.ensure()
        raw = float(-self.model.score_samples(resid.reshape(1, -1))[0])
        score = (raw - self._base) / max(self._thr - self._base, 1e-9)
        z = {f: float(resid[i] / max(self.scale[i], 1e-6)) for i, f in enumerate(FEATURES)}
        return dict(score=float(np.clip(score, 0.0, 2.0)), anomalous=bool(raw > self._thr), z=z)


detector = AnomalyDetector()
