"""Model-estimated SRP risk classifier (Build Bible 7.2) with contribution-based explanations
and out-of-domain (OOD) detection (16.2).

Labels are SYNTHETIC ground truth defined from the simulator's true state - never field diagnoses:
    NORMAL, LOW_FILLAGE, ROD_FLOAT_RISK, HIGH_IMPACT, PUMP_UNSETTING_RISK
The classifier only sees noisy, field-measurable features. Splits are grouped by simulated cycle
(no cycle appears in both train and test) and reported per class (precision / recall / F1)."""
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
MODEL_PATH = config.MODELS_DIR / "risk_xgb.joblib"
METRICS_PATH = config.MODELS_DIR / "risk_metrics.json"
CLASSES = ["NORMAL", "LOW_FILLAGE", "ROD_FLOAT_RISK", "HIGH_IMPACT", "PUMP_UNSETTING_RISK"]
FEATURES = ["fillage", "runtime", "spm", "pprl_kn", "mprl_kn", "load_range_kn", "load_ratio", "mu_cp", "t_avg", "motor_kw",
            "spm_over_rodfall"]
FEATURE_TEXT = {
    "fillage": "pump fillage", "runtime": "run-time fraction", "spm": "pump speed", "pprl_kn": "peak rod load",
    "mprl_kn": "minimum rod load", "load_range_kn": "load range", "load_ratio": "min/peak load ratio",
    "mu_cp": "estimated oil viscosity", "t_avg": "heated-zone temperature", "motor_kw": "motor power",
    "spm_over_rodfall": "speed vs rod-fall limit",
}
LABEL_TEXT = {
    "NORMAL": "normal operation",
    "LOW_FILLAGE": "low pump fillage",
    "ROD_FLOAT_RISK": "rod-float risk (rods falling slower than the polished rod)",
    "HIGH_IMPACT": "high impact loading (fluid pound at speed)",
    "PUMP_UNSETTING_RISK": "pump-unsetting risk (heavy oil drag with high rod stress)",
}


def label_from_truth(fillage: float, spm: float, rodfloat: float, mu_pump_cp: float, goodman: float, runtime: float) -> str:
    """Synthetic ground-truth rules (priority order). Thresholds are SYNTHETIC_ASSUMPTIONs."""
    if rodfloat > 0.25:
        return "ROD_FLOAT_RISK"
    if runtime >= 0.99 and fillage < 0.5 and spm >= 3.5:
        return "HIGH_IMPACT"
    if mu_pump_cp > 1200.0 and goodman > 0.75:
        return "PUMP_UNSETTING_RISK"
    if runtime >= 0.99 and fillage < 0.65:
        return "LOW_FILLAGE"
    return "NORMAL"


def feature_vector(meas: dict, mu_cp: float, t_avg: float, spm_rodfall: float) -> list[float]:
    """meas: sensors.measure()-style dict (pprl_kn, mprl_kn, spm, fillage, runtime, motor_kw)."""
    pp, mp = meas["pprl_kn"], meas["mprl_kn"]
    return [meas["fillage"], meas.get("runtime", 1.0), meas["spm"], pp, mp, pp - mp, mp / pp if pp > 1e-6 else 0.0,
            mu_cp, t_avg, meas["motor_kw"], meas["spm"] / spm_rodfall if spm_rodfall > 1e-6 else 0.0]


def _generate(n_cycles: int, seed: int):
    rng = np.random.default_rng(seed)
    x, y, g = [], [], []
    for ci in range(n_cycles):
        w = scenarios.sample_well(rng)
        v = scenarios.sample_design_vec(rng)
        p, st = scenarios.well_from_features(w)
        d = scenarios.design_from_vec(v, p.depth_m, poc=bool(rng.random() < 0.35))
        d.prod_days = float(rng.uniform(100, 300))
        if rng.random() < 0.5:
            # stress scenarios: small steam slugs and very long cycles let the zone cool to <70 C, where viscous rod drag
            # makes rod-float and pump-unsetting states reachable (otherwise these classes never occur in the data)
            d.steam_t = float(rng.uniform(250.0, 700.0))
            d.prod_days = float(rng.uniform(250.0, 500.0))
            d.spm_knots = [float(rng.uniform(3.0, 9.0)) for _ in range(4)]
        state = coupling.CycleState(np_total_m3=w["np_total_m3"])
        stt = state.copy()
        # phases are advanced manually so per-day production-point truth values (mu, rod float) are available
        guard = 0
        while guard < 2000:
            guard += 1
            if stt.phase == "production" and stt.t_phase_d >= d.prod_days:
                break
            dt = 1.0 if stt.phase == "production" else 0.5
            out = coupling.advance(p, stt, d, dt)
            if out["phase"] != "production" or out.get("spm", 0) <= 0.3 or not out.get("spm_rodfall"):
                continue
            if rng.random() > 0.35:  # thin the rows
                continue
            noisy = dict(
                fillage=float(np.clip(out["fillage"] + rng.normal(0, 0.03), 0, 1)), runtime=out.get("runtime", 1.0),
                spm=out["spm"] + rng.normal(0, 0.02), pprl_kn=out["pprl"] / 1e3 * (1 + rng.normal(0, 0.015)) + rng.normal(0, 0.1),
                mprl_kn=out["mprl"] / 1e3 * (1 + rng.normal(0, 0.015)) + rng.normal(0, 0.1),
                motor_kw=max(out["motor_kw"] * (1 + rng.normal(0, 0.03)), 0.0))
            mu_est = out["mu_pump"] * rng.uniform(0.8, 1.25)
            x.append(feature_vector(noisy, mu_est, out["t_avg"] + rng.normal(0, 3), out["spm_rodfall"] * rng.uniform(0.9, 1.1)))
            y.append(CLASSES.index(label_from_truth(out["fillage"], out["spm"], out["rodfloat"], out["mu_pump"], out["goodman"], out.get("runtime", 1.0))))
            g.append(ci)
    return np.asarray(x, float), np.asarray(y, int), np.asarray(g, int)


class RiskModel:
    def __init__(self):
        self.model = None
        self.metrics: dict = {}
        self.lo = self.hi = None

    def train(self, n_cycles: int = 900, seed: int = 41):
        import xgboost as xgb

        t0 = time.time()
        x, y, g = _generate(n_cycles, seed)
        cycles = np.unique(g)
        rng = np.random.default_rng(seed)
        rng.shuffle(cycles)
        n = len(cycles)
        train_c, val_c, test_c = set(cycles[: int(0.7 * n)]), set(cycles[int(0.7 * n): int(0.85 * n)]), set(cycles[int(0.85 * n):])
        tr = np.array([c in train_c for c in g])
        va = np.array([c in val_c for c in g])
        te = np.array([c in test_c for c in g])
        counts = np.bincount(y[tr], minlength=len(CLASSES)).astype(float)
        cw = counts.sum() / (len(CLASSES) * np.maximum(counts, 1.0))  # class weights for imbalance
        model = xgb.XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.06, subsample=0.85, colsample_bytree=0.9,
                                  objective="multi:softprob", num_class=len(CLASSES), tree_method="hist", random_state=seed,
                                  early_stopping_rounds=25, eval_metric="mlogloss")
        model.fit(x[tr], y[tr], sample_weight=cw[y[tr]], eval_set=[(x[va], y[va])], sample_weight_eval_set=[cw[y[va]]], verbose=False)
        pred = model.predict(x[te])
        k = len(CLASSES)
        cm = np.zeros((k, k), dtype=int)
        for a, b in zip(y[te], pred):
            cm[a, b] += 1
        prec = {c: float(cm[i, i] / max(cm[:, i].sum(), 1)) for i, c in enumerate(CLASSES)}
        rec = {c: float(cm[i, i] / max(cm[i].sum(), 1)) for i, c in enumerate(CLASSES)}
        f1 = {c: (2 * prec[c] * rec[c] / (prec[c] + rec[c]) if prec[c] + rec[c] > 0 else 0.0) for c in CLASSES}
        support = {c: int(cm[i].sum()) for i, c in enumerate(CLASSES)}
        self.model = model
        self.lo = np.percentile(x[tr], 1.0, axis=0)
        self.hi = np.percentile(x[tr], 99.0, axis=0)
        joblib.dump(dict(model=model, lo=self.lo, hi=self.hi), MODEL_PATH)
        empty = [c for c in CLASSES if support[c] == 0]
        if empty:
            log.warning("risk classes with no test support (metrics undefined for them): %s", empty)
        self.metrics = dict(model="XGBoost classifier (multi:softprob, class-weighted)", classes=CLASSES, features=FEATURES,
                            classes_without_support=empty,
                            accuracy=float(np.trace(cm) / max(cm.sum(), 1)), precision=prec, recall=rec, f1=f1, support=support,
                            confusion=cm.tolist(), macro_f1=float(np.mean([f1[c] for c in CLASSES if support[c] > 0])), n_train=int(tr.sum()),
                            n_val=int(va.sum()), n_test=int(te.sum()), split="grouped by simulated cycle (70/15/15)",
                            data="SYNTHETIC labels - not field accuracy", train_seconds=round(time.time() - t0, 1),
                            train_percentiles=dict(zip(FEATURES, [[float(a), float(b)] for a, b in zip(self.lo, self.hi)])))
        METRICS_PATH.write_text(json.dumps(self.metrics, indent=2))
        log.info("risk model trained: acc=%.3f macro-F1=%.3f", self.metrics["accuracy"], self.metrics["macro_f1"])
        return self.metrics

    def load(self) -> bool:
        if not (MODEL_PATH.exists() and METRICS_PATH.exists()):
            return False
        blob = joblib.load(MODEL_PATH)
        self.model, self.lo, self.hi = blob["model"], blob["lo"], blob["hi"]
        self.metrics = json.loads(METRICS_PATH.read_text())
        return True

    def ensure(self):
        if self.model is None and not self.load():
            self.train()

    def out_of_domain(self, x: np.ndarray) -> dict:
        """Fraction of feature values outside the 1st-99th training percentile range."""
        self.ensure()
        x = np.atleast_2d(x)
        outside = (x < self.lo) | (x > self.hi)
        per_feature = {f: float(outside[:, i].mean()) for i, f in enumerate(FEATURES)}
        frac = float(outside.mean())
        return dict(fraction=frac, per_feature=per_feature, flag=frac > 0.25)

    def predict(self, vec: list[float]) -> dict:
        """Risk probabilities + top feature contributions to the leading non-normal class."""
        import xgboost as xgb

        self.ensure()
        x = np.asarray([vec], float)
        proba = self.model.predict_proba(x)[0]
        top = int(np.argmax(proba))
        risk_score = float(1.0 - proba[0])
        focus = top if top != 0 else int(np.argmax(proba[1:]) + 1)
        contrib = self.model.get_booster().predict(xgb.DMatrix(x, feature_names=FEATURES), pred_contribs=True)
        # pred_contribs shape (1, n_classes, n_features+1) for multiclass
        contrib = np.asarray(contrib).reshape(len(CLASSES), len(FEATURES) + 1)[focus, :-1]
        order = np.argsort(-np.abs(contrib))[:3]
        factors = [dict(feature=FEATURES[i], text=FEATURE_TEXT[FEATURES[i]], value=float(x[0, i]), contribution=float(contrib[i]),
                        direction="raises" if contrib[i] > 0 else "lowers") for i in order]
        return dict(label=CLASSES[top], probabilities={c: float(p) for c, p in zip(CLASSES, proba)}, risk_score=risk_score,
                    focus_class=CLASSES[focus], factors=factors, ood=self.out_of_domain(x))


risk_model = RiskModel()
