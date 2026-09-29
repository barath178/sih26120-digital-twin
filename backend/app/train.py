"""Train (or retrain) every ML model of the twin and export the synthetic datasets.

    python -m app.train            # train / generate anything missing
    python -m app.train --force    # redo everything
"""
from __future__ import annotations

import argparse
import json
import logging
import time

from . import exports
from .ml.anomaly import detector
from .ml.dynacard import classifier
from .ml.forecaster import forecaster
from .ml.risk import risk_model
from .ml.surrogate import surrogate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="retrain / regenerate even if saved artefacts exist")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for name, model in (("dynacard CNN", classifier), ("forecaster", forecaster), ("surrogate", surrogate),
                        ("anomaly detector", detector), ("risk classifier", risk_model)):
        if not args.force and model.load():
            print(f"[skip] {name}: already trained")
            continue
        t = time.time()
        print(f"[train] {name} ...", flush=True)
        metrics = model.train()
        short = {k: v for k, v in metrics.items() if isinstance(v, (int, float, str))}
        print(f"[done] {name} in {time.time() - t:.0f}s: {json.dumps(short)[:300]}")
    if args.force or not exports.exists():
        print("[data] generating synthetic datasets ...", flush=True)
        exports.generate()
    else:
        print("[skip] synthetic datasets already exist")


if __name__ == "__main__":
    main()
