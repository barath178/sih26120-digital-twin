"""Data dictionary, CSV validator (units, missingness, duplicates) and replay engine.

The schema follows Build Bible 9.3. The validator rejects ambiguous units instead of silently
coercing them: e.g. a pump_fillage column whose values all lie in 0-1 is refused because the
schema expects percent."""
from __future__ import annotations

import io
import json

import numpy as np
import pandas as pd

from . import config
from .physics import fluid

# name: (type, unit, description, min, max, required)
SCHEMA: dict[str, tuple] = {
    "timestamp": ("datetime", "ISO 8601", "Simulation timestamp", None, None, True),
    "well_id": ("string", "-", "Well identifier", None, None, True),
    "cycle_id": ("string", "-", "CSS cycle identifier", None, None, False),
    "cycle_phase": ("category", "-", "INJECTION/SOAK/PRODUCTION/COOLDOWN", None, None, True),
    "reservoir_temperature": ("float", "degC", "Estimated near-wellbore temperature", -10, 400, False),
    "pump_temperature": ("float", "degC", "Estimated temperature at pump depth", -10, 400, False),
    "reservoir_pressure": ("float", "bar", "Reservoir pressure (project-wide unit: bar)", 1, 500, False),
    "viscosity_est": ("float", "cP", "Estimated crude viscosity", 0.5, 1e6, False),
    "steam_mass": ("float", "tonnes", "CSS steam input", 0, 20000, False),
    "injection_pressure": ("float", "bar", "Injection pressure", 0, 500, False),
    "injection_duration": ("float", "days", "CSS injection period", 0, 200, False),
    "soak_duration": ("float", "days", "CSS soak period", 0, 200, False),
    "stroke_length": ("float", "in", "SRP stroke", 20, 300, True),
    "spm": ("float", "strokes/min", "SRP speed", 0, 20, True),
    "vfd_frequency": ("float", "Hz", "Drive frequency / proxy", 0, 120, False),
    "motor_current": ("float", "A", "Motor current", 0, 500, False),
    "peak_load": ("float", "kN", "Peak polished-rod load proxy", 0, 500, False),
    "min_load": ("float", "kN", "Minimum load proxy", -100, 500, False),
    "pump_fillage": ("float", "%", "Estimated fillage", 0, 100, True),
    "oil_rate": ("float", "BOPD", "Oil production rate", 0, 5000, True),
    "water_cut": ("float", "fraction", "Water cut", 0, 1, False),
    "power_kw": ("float", "kW", "SRP power proxy", 0, 500, False),
    "sor": ("float", "t/m3", "Cumulative cycle steam-to-oil ratio (tonnes steam per m3 oil)", 0, 1000, False),
    "failure_label": ("category", "-", "Synthetic ground truth only", None, None, False),
}
PHASES = {"INJECTION", "SOAK", "PRODUCTION", "COOLDOWN"}
MAX_MISSING_REQUIRED = 0.2


def schema_json() -> dict:
    return {k: dict(type=v[0], unit=v[1], description=v[2], min=v[3], max=v[4], required=v[5]) for k, v in SCHEMA.items()}


def write_schema(path) -> None:
    doc = dict(title="Baghewala digital twin telemetry schema", version=config.MODEL_VERSION, data_mode=config.DATA_MODE,
               columns=schema_json())
    path.write_text(json.dumps(doc, indent=2))


def validate(raw: bytes) -> dict:
    """Validate an uploaded CSV. Returns dict(ok, errors, warnings, rows, wells, missingness, df)."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:
        return dict(ok=False, errors=[f"could not parse CSV: {e}"], warnings=[], rows=0, wells=[], missingness={}, df=None)
    df.columns = [c.strip().lower() for c in df.columns]
    if df.empty:
        return dict(ok=False, errors=["file has no data rows"], warnings=[], rows=0, wells=[], missingness={}, df=None)
    missing_cols = [c for c, v in SCHEMA.items() if v[5] and c not in df.columns]
    if missing_cols:
        errors.append(f"missing required columns: {', '.join(missing_cols)}")
    unknown = [c for c in df.columns if c not in SCHEMA]
    if unknown:
        warnings.append(f"ignoring unknown columns: {', '.join(unknown)}")
    if errors:
        return dict(ok=False, errors=errors, warnings=warnings, rows=len(df), wells=[], missingness={}, df=None)

    # types
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], errors="coerce", utc=False)
        bad = int(ts.isna().sum())
        if bad:
            errors.append(f"timestamp: {bad} value(s) are not ISO 8601 datetimes")
        df["timestamp"] = ts
    for c, v in SCHEMA.items():
        if c in df.columns and v[0] == "float":
            conv = pd.to_numeric(df[c], errors="coerce")
            bad = int((conv.isna() & df[c].notna()).sum())
            if bad:
                errors.append(f"{c}: {bad} non-numeric value(s); expected {v[1]}")
            df[c] = conv
    if errors:
        return dict(ok=False, errors=errors, warnings=warnings, rows=len(df), wells=[], missingness={}, df=None)

    # phases
    ph = df["cycle_phase"].astype(str).str.upper().str.strip()
    badp = sorted(set(ph) - PHASES)
    if badp:
        errors.append(f"cycle_phase: unknown value(s) {badp}; expected INJECTION, SOAK, PRODUCTION or COOLDOWN")
    df["cycle_phase"] = ph

    # missingness
    miss = {c: float(df[c].isna().mean()) for c in df.columns if c in SCHEMA}
    for c, frac in miss.items():
        if frac > 0 and SCHEMA[c][5] and frac > MAX_MISSING_REQUIRED:
            errors.append(f"{c}: {frac * 100:.0f}% missing (limit {MAX_MISSING_REQUIRED * 100:.0f}% for required columns)")
        elif frac > 0.05:
            warnings.append(f"{c}: {frac * 100:.0f}% missing values")

    # units / ranges - reject ambiguous units
    for c, v in SCHEMA.items():
        if c not in df.columns or v[0] != "float":
            continue
        col = df[c].dropna()
        if col.empty:
            continue
        lo, hi = v[3], v[4]
        if c == "pump_fillage" and col.max() <= 1.0 and col.max() > 0:
            errors.append("pump_fillage: all values are within 0-1; the schema expects PERCENT (0-100). Convert before upload.")
            continue
        if c == "water_cut" and col.max() > 1.5:
            errors.append("water_cut: values above 1.5; the schema expects a FRACTION (0-1), not percent.")
            continue
        if c == "stroke_length" and col.max() < 20:
            errors.append("stroke_length: all values below 20; the schema expects INCHES (a 2.5 m stroke = 98 in). Metres suspected.")
            continue
        if c in ("reservoir_pressure", "injection_pressure") and 0 < col.max() < 50 and c == "reservoir_pressure":
            warnings.append("reservoir_pressure: values look like MPa; the schema expects bar (1 MPa = 10 bar).")
        n_out = int(((col < lo) | (col > hi)).sum())
        if n_out:
            errors.append(f"{c}: {n_out} value(s) outside the plausible range {lo}-{hi} {v[1]}")
    if "peak_load" in df.columns and "min_load" in df.columns:
        both = df[["peak_load", "min_load"]].dropna()
        if len(both) and (both["peak_load"] < both["min_load"]).mean() > 0.01:
            errors.append("peak_load is below min_load in more than 1% of rows - columns swapped?")

    # duplicates
    dup = int(df.duplicated(subset=["well_id", "timestamp"]).sum())
    if dup:
        errors.append(f"{dup} duplicate (well_id, timestamp) row(s)")
    if not df["timestamp"].is_monotonic_increasing and not errors:
        warnings.append("timestamps are not sorted; they will be sorted for replay")

    wells = sorted(df["well_id"].astype(str).unique().tolist())
    return dict(ok=not errors, errors=errors, warnings=warnings, rows=int(len(df)), wells=wells, missingness=miss, df=df if not errors else None)


def replay(df: pd.DataFrame, well_id: str, max_rows: int = 3000) -> dict:
    """Replay a validated time series through the same twin logic: viscosity model, energy proxy and the
    risk classifier with OOD detection. Returns a compact per-row series for the UI player."""
    from .ml.risk import risk_model, FEATURES

    d = df[df["well_id"].astype(str) == well_id].sort_values("timestamp").reset_index(drop=True)
    if d.empty:
        raise ValueError(f"no rows for well {well_id}")
    step = max(1, int(np.ceil(len(d) / max_rows)))
    d = d.iloc[::step].reset_index(drop=True)
    risk_model.ensure()
    have_loads = {"peak_load", "min_load"}.issubset(d.columns) and d[["peak_load", "min_load"]].notna().all(axis=1).any()
    t_col = "reservoir_temperature" if "reservoir_temperature" in d.columns else ("pump_temperature" if "pump_temperature" in d.columns else None)
    rows, xs, idx_risk = [], [], []
    for i, r in d.iterrows():
        t = float(r[t_col]) if t_col and pd.notna(r.get(t_col)) else config.T_RESERVOIR_C
        mu = float(r["viscosity_est"]) if "viscosity_est" in d.columns and pd.notna(r.get("viscosity_est")) else fluid.oil_viscosity_cp(t)
        row = dict(t=r["timestamp"].isoformat(), phase=r["cycle_phase"], oil_bopd=float(r["oil_rate"]), spm=float(r["spm"]),
                   fillage=float(r["pump_fillage"]), t_c=t, mu_cp=mu, modelled_viscosity=fluid.oil_viscosity_cp(t),
                   risk=None, label=None, factors=None)
        power = float(r["power_kw"]) if "power_kw" in d.columns and pd.notna(r.get("power_kw")) else None
        row["power_kw"] = power
        row["sor"] = float(r["sor"]) if "sor" in d.columns and pd.notna(r.get("sor")) else None
        if have_loads and r["cycle_phase"] in ("PRODUCTION", "COOLDOWN") and pd.notna(r["peak_load"]) and pd.notna(r["min_load"]) and r["spm"] > 0.3:
            meas = dict(fillage=float(r["pump_fillage"]) / 100.0, runtime=1.0, spm=float(r["spm"]), pprl_kn=float(r["peak_load"]),
                        mprl_kn=float(r["min_load"]), motor_kw=power or 0.0)
            # the rod-fall limit is unknown for uploaded data: estimate from viscosity with the twin's rod-drag model
            from .physics import srp
            depth = 1050.0
            _, w_rf = srp.rod_weights(srp.rod_string(depth), 950.0)
            c_drag = sum(srp.drag_coefficient(mu / 1000.0, (4 * s.area / np.pi) ** 0.5) * s.length for s in srp.rod_string(depth))
            spm_rodfall = 60.0 * (w_rf / max(c_drag, 1e-6)) / (np.pi * float(r["stroke_length"]) * 0.0254)
            xs.append(np.array(risk_vec(meas, mu, t, spm_rodfall)))
            idx_risk.append(i)
        rows.append(row)
    ood = None
    if xs:
        arr = np.vstack(xs)
        proba = risk_model.model.predict_proba(arr)
        for j, i in enumerate(idx_risk):
            rows[i]["risk"] = float(1.0 - proba[j, 0])
            rows[i]["label"] = risk_model.metrics["classes"][int(np.argmax(proba[j]))]
        ood = risk_model.out_of_domain(arr)
    counts: dict[str, int] = {}
    for r in rows:
        if r["label"]:
            counts[r["label"]] = counts.get(r["label"], 0) + 1
    return dict(well_id=well_id, n=len(rows), stride=step, rows=rows, risk_counts=counts, ood=ood, features=FEATURES,
                notes=["Risk labels are model-estimated on synthetic-trained data, not operational diagnoses."]
                + (["OUT-OF-DOMAIN: a large share of the uploaded feature values lie outside the training range, so risk estimates are shown "
                    "as indicative only and no strong recommendation language is used."] if ood and ood["flag"] else []))


def risk_vec(meas, mu, t, spm_rodfall):
    from .ml.risk import feature_vector

    return feature_vector(meas, mu, t, spm_rodfall)
