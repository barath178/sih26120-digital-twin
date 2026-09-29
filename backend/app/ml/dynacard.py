"""Dynamometer-card classifier.

Training data are produced by the physics model: surface cards are simulated with the damped
wave equation for randomised wells and pump conditions, converted to downhole cards with the
Gibbs diagnostic (using a deliberately perturbed damping estimate, as in the field), and
rasterised to 64x64 images. A small CNN (PyTorch) classifies the images. If PyTorch is not
available an sklearn MLP is used instead, with the same interface.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time

import numpy as np

from .. import config
from ..physics import srp

log = logging.getLogger(__name__)
MODEL_PATH = config.MODELS_DIR / "dynacard_cnn.pt"
SK_MODEL_PATH = config.MODELS_DIR / "dynacard_mlp.joblib"
METRICS_PATH = config.MODELS_DIR / "dynacard_metrics.json"
IMG = 64

NPZ_PATH = config.MODELS_DIR / "dynacard_cnn.npz"

try:
    if os.getenv("DYNACARD_BACKEND", "").lower() == "numpy":
        raise ImportError("numpy inference requested")
    import torch
    from torch import nn

    TORCH_OK = True
except Exception:  # pragma: no cover - fallback path
    TORCH_OK = False


# ----------------------------------------------------------------------------- data generation


def _log_uniform(rng, lo, hi):
    return float(math.exp(rng.uniform(math.log(lo), math.log(hi))))


def random_spec(rng: np.random.Generator, cls: str) -> srp.CardSpec:
    stroke = rng.uniform(1.8, 3.6)
    spm = rng.uniform(2.5, 7.5)
    depth = rng.uniform(950.0, 1280.0)
    rho = rng.uniform(930.0, 1000.0)
    pip = rng.uniform(0.3, 3.0)
    fo = srp.fo_from_pressures(depth, pip, rho)
    mu_tub = _log_uniform(rng, 5.0, 800.0)
    mu_pump = mu_tub * rng.uniform(0.3, 1.0)
    spec = srp.CardSpec(stroke=stroke, spm=spm, pump_depth=depth, fo=fo, rho_liq=rho, mu_tubing_cp=mu_tub,
                        mu_pump_cp=mu_pump, fill=rng.uniform(0.92, 1.0), noise=rng.uniform(0.004, 0.02))
    if cls == "fluid_pound":
        spec.fill = rng.uniform(0.3, 0.82)
    elif cls == "gas_interference":
        spec.fill = rng.uniform(0.3, 0.8)
        spec.gas = rng.uniform(0.75, 1.0)
    elif cls == "tv_leak":
        spec.tv_leak = rng.uniform(0.3, 0.9)
    elif cls == "sv_leak":
        spec.sv_leak = rng.uniform(0.35, 0.9)
    elif cls == "rod_parted":
        spec.parted_frac = rng.uniform(0.2, 0.9)
    elif cls == "viscous_drag":
        spec.mu_pump_cp = _log_uniform(rng, 2500.0, 12000.0)
        spec.mu_tubing_cp = _log_uniform(rng, 1500.0, 8000.0)
    return spec


def card_to_image(surface_pos, surface_load, spec: srp.CardSpec, mu_est_cp: float, fo_est: float):
    dpos, dload = srp.gibbs_downhole(surface_pos, surface_load, spec.spm, spec.pump_depth, spec.rho_liq, mu_est_cp)
    return srp.rasterize_card(dpos, dload, spec.stroke, fo_est), dpos, dload


def generate_dataset(n_per_class: int = 350, seed: int = 0, batch: int = 256):
    rng = np.random.default_rng(seed)
    specs, labels = [], []
    for ci, cls in enumerate(srp.CARD_CLASSES):
        for _ in range(n_per_class):
            specs.append(random_spec(rng, cls))
            labels.append(ci)
    order = rng.permutation(len(specs))
    specs = [specs[i] for i in order]
    labels = np.array([labels[i] for i in order])
    # group by SPM so each batch has similar step counts
    idx_sorted = np.argsort([s.spm for s in specs])
    images = np.zeros((len(specs), IMG, IMG), dtype=np.float32)
    for start in range(0, len(specs), batch):
        ids = idx_sorted[start:start + batch]
        chunk = [specs[i] for i in ids]
        res = srp.simulate_cards(chunk, seed=int(rng.integers(1 << 30)))
        for j, i in enumerate(ids):
            s = specs[i]
            mu_est = s.mu_tubing_cp * rng.uniform(0.7, 1.3)   # imperfect damping knowledge
            fo_est = s.fo * rng.uniform(0.9, 1.1)             # twin's fluid-load estimate
            images[i], _, _ = card_to_image(res["surface_pos"][j], res["surface_load"][j], s, mu_est, fo_est)
    return images, labels


# ----------------------------------------------------------------------------- model


if TORCH_OK:

    class CardCNN(nn.Module):
        def __init__(self, n_classes: int = len(srp.CARD_CLASSES)):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(), nn.MaxPool2d(2),
                nn.Conv2d(16, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2),
                nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(), nn.MaxPool2d(2),
                nn.Conv2d(64, 64, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool2d(4),
            )
            self.head = nn.Sequential(nn.Flatten(), nn.Dropout(0.3), nn.Linear(64 * 16, 96), nn.ReLU(), nn.Linear(96, n_classes))

        def forward(self, x):
            return self.head(self.features(x))


    def export_npz(model: "CardCNN", path=NPZ_PATH):
        """Save the trained CNN as plain arrays (batch-norm folded into the convs) for torch-free inference."""
        f, h = model.features, model.head
        out = {}
        for i, (ci, bi) in enumerate(((0, 1), (4, 5), (8, 9), (12, None))):
            w = f[ci].weight.detach().numpy().astype(np.float32)
            b = f[ci].bias.detach().numpy().astype(np.float32)
            if bi is not None:
                bn = f[bi]
                s = (bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
                w = w * s[:, None, None, None]
                b = (b - bn.running_mean.detach().numpy()) * s + bn.bias.detach().numpy()
            out[f"c{i}w"], out[f"c{i}b"] = w, b.astype(np.float32)
        out["l0w"], out["l0b"] = h[2].weight.detach().numpy(), h[2].bias.detach().numpy()
        out["l1w"], out["l1b"] = h[4].weight.detach().numpy(), h[4].bias.detach().numpy()
        np.savez(path, **out)


class NumpyCardCNN:
    """Inference-only CardCNN in numpy (same weights), so deployments can run without PyTorch."""

    def __init__(self, path=NPZ_PATH):
        z = np.load(path)
        self.p = {k: z[k].astype(np.float32) for k in z.files}

    @staticmethod
    def _conv(x, w, b):  # x (N,C,H,W), 3x3, padding 1
        xp = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)))
        cols = np.lib.stride_tricks.sliding_window_view(xp, (3, 3), axis=(2, 3))  # N,C,H,W,3,3
        return np.einsum("nchwij,ocij->nohw", cols, w, optimize=True) + b[None, :, None, None]

    @staticmethod
    def _pool(x, k):
        n, c, hh, ww = x.shape
        return x.reshape(n, c, hh // k, k, ww // k, k)

    def __call__(self, x):
        p = self.p
        for i in range(3):
            x = np.maximum(self._conv(x, p[f"c{i}w"], p[f"c{i}b"]), 0)
            x = self._pool(x, 2).max(axis=(3, 5))
        x = np.maximum(self._conv(x, p["c3w"], p["c3b"]), 0)
        x = self._pool(x, x.shape[2] // 4).mean(axis=(3, 5))  # adaptive avg pool to 4x4 (input is divisible)
        x = x.reshape(len(x), -1)
        x = np.maximum(x @ p["l0w"].T + p["l0b"], 0)
        return x @ p["l1w"].T + p["l1b"]


class DynacardClassifier:
    def __init__(self):
        self.model = None
        self.kind = None
        self.metrics: dict = {}

    # -------------------------------------------------------------- training
    def train(self, n_per_class: int = 350, epochs: int = 18, seed: int = 0):
        t0 = time.time()
        x, y = generate_dataset(n_per_class, seed)
        gen_s = time.time() - t0
        n = len(y)
        rng = np.random.default_rng(seed + 1)
        perm = rng.permutation(n)
        n_test = int(0.2 * n)
        te, tr = perm[:n_test], perm[n_test:]
        if TORCH_OK:
            torch.manual_seed(seed)
            model = CardCNN()
            opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
            sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
            xt = torch.tensor(x[tr, None])
            yt = torch.tensor(y[tr], dtype=torch.long)
            loss_fn = nn.CrossEntropyLoss()
            for ep in range(epochs):
                model.train()
                p = torch.randperm(len(yt))
                for k in range(0, len(yt), 64):
                    b = p[k:k + 64]
                    xb = xt[b]
                    # augmentation: small random shifts in position/load
                    if ep > 0:
                        dx, dy = np.random.randint(-2, 3, size=2)
                        xb = torch.roll(xb, shifts=(int(dy), int(dx)), dims=(2, 3))
                    opt.zero_grad()
                    loss = loss_fn(model(xb), yt[b])
                    loss.backward()
                    opt.step()
                sched.step()
            model.eval()
            with torch.no_grad():
                pred = model(torch.tensor(x[te, None])).argmax(1).numpy()
            self.model, self.kind = model, "cnn"
            torch.save(model.state_dict(), MODEL_PATH)
            export_npz(model)
        else:  # pragma: no cover
            from sklearn.neural_network import MLPClassifier
            import joblib

            model = MLPClassifier(hidden_layer_sizes=(256, 64), max_iter=300, random_state=seed)
            model.fit(x[tr].reshape(len(tr), -1), y[tr])
            pred = model.predict(x[te].reshape(len(te), -1))
            self.model, self.kind = model, "mlp"
            joblib.dump(model, SK_MODEL_PATH)
        k = len(srp.CARD_CLASSES)
        cm = np.zeros((k, k), dtype=int)
        for a, b in zip(y[te], pred):
            cm[a, b] += 1
        acc = float(np.trace(cm) / cm.sum())
        per_class = {c: float(cm[i, i] / max(cm[i].sum(), 1)) for i, c in enumerate(srp.CARD_CLASSES)}
        self.metrics = dict(model=self.kind, accuracy=acc, per_class_recall=per_class, confusion=cm.tolist(),
                            classes=srp.CARD_CLASSES, n_train=int(len(tr)), n_test=int(n_test),
                            data_gen_seconds=round(gen_s, 1), train_seconds=round(time.time() - t0 - gen_s, 1))
        METRICS_PATH.write_text(json.dumps(self.metrics, indent=2))
        log.info("dynacard classifier trained: acc=%.3f", acc)
        return self.metrics

    # -------------------------------------------------------------- persistence
    def load(self) -> bool:
        if not METRICS_PATH.exists():
            return False
        self.metrics = json.loads(METRICS_PATH.read_text())
        if TORCH_OK and MODEL_PATH.exists():
            model = CardCNN()
            model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
            model.eval()
            self.model, self.kind = model, "cnn"
            return True
        if NPZ_PATH.exists():
            self.model, self.kind = NumpyCardCNN(), "cnn-numpy"
            return True
        if SK_MODEL_PATH.exists():  # pragma: no cover
            import joblib

            self.model, self.kind = joblib.load(SK_MODEL_PATH), "mlp"
            return True
        return False

    def ensure(self):
        if self.model is None and not self.load():
            self.train()

    # -------------------------------------------------------------- inference
    def predict(self, images: np.ndarray) -> np.ndarray:
        """images (N,64,64) -> probabilities (N, n_classes)."""
        self.ensure()
        if self.kind == "cnn":
            with torch.no_grad():
                logits = self.model(torch.tensor(images[:, None].astype(np.float32)))
                return torch.softmax(logits, 1).numpy()
        if self.kind == "cnn-numpy":
            z = self.model(images[:, None].astype(np.float32))
            z = np.exp(z - z.max(axis=1, keepdims=True))
            return z / z.sum(axis=1, keepdims=True)
        return self.model.predict_proba(images.reshape(len(images), -1))  # pragma: no cover


classifier = DynacardClassifier()
