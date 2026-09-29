"""Field sensor model: converts model outputs into noisy SCADA-style measurements."""
from __future__ import annotations

import math

import numpy as np

from .. import config

PU = config.PUMPING_UNIT

TELEMETRY_FIELDS = [
    "steam_rate_tpd", "steam_quality", "whp_mpa", "wht_c", "oil_rate_m3d", "water_rate_m3d", "liquid_rate_m3d",
    "spm", "stroke_m", "runtime", "pprl_kn", "mprl_kn", "motor_kw", "motor_current_a", "fillage", "pip_mpa", "pump_temp_c",
]


def motor_current(kw: float) -> float:
    return kw * 1000.0 / (math.sqrt(3.0) * PU["motor_voltage_v"] * PU["motor_pf"] * 0.9) if kw > 0 else 0.0


def ideal(out: dict) -> dict:
    """Noise-free measurement vector from a coupling.advance() output."""
    producing = out["phase"] == "production"
    runtime = out.get("runtime", 1.0) if producing else 0.0
    return dict(
        steam_rate_tpd=out.get("steam_rate_tpd", 0.0),
        steam_quality=out.get("steam_quality_wh", 0.0),
        whp_mpa=out.get("whp", config.WELLBORE["wellhead_pressure_mpa"]),
        wht_c=out.get("wht", 35.0),
        oil_rate_m3d=out.get("q_oil", 0.0),
        water_rate_m3d=out.get("q_water", 0.0),
        liquid_rate_m3d=out.get("q_liq", 0.0),
        spm=out.get("spm", 0.0) if producing else 0.0,
        stroke_m=out.get("stroke", 0.0),
        runtime=runtime,
        pprl_kn=out.get("pprl", 0.0) / 1000.0,
        mprl_kn=out.get("mprl", 0.0) / 1000.0,
        motor_kw=out.get("motor_kw", 0.0),
        motor_current_a=motor_current(out.get("motor_kw", 0.0)),
        fillage=out.get("fillage", 0.0),
        pip_mpa=out.get("pip", 0.0) if producing else 0.0,
        pump_temp_c=out.get("t_avg", config.T_RESERVOIR_C) if producing else out.get("t_avg", config.T_RESERVOIR_C),
    )


NOISE = dict(  # (relative, absolute)
    steam_rate_tpd=(0.02, 0.0), steam_quality=(0.0, 0.01), whp_mpa=(0.02, 0.0), wht_c=(0.0, 1.5),
    oil_rate_m3d=(0.05, 0.02), water_rate_m3d=(0.05, 0.02), liquid_rate_m3d=(0.04, 0.02), spm=(0.0, 0.02),
    stroke_m=(0.0, 0.0), runtime=(0.0, 0.0), pprl_kn=(0.015, 0.1), mprl_kn=(0.015, 0.1), motor_kw=(0.03, 0.05),
    motor_current_a=(0.03, 0.1), fillage=(0.0, 0.03), pip_mpa=(0.02, 0.01), pump_temp_c=(0.0, 1.0),
)


def measure(out: dict, rng: np.random.Generator) -> dict:
    clean = ideal(out)
    meas = {}
    for k, v in clean.items():
        rel, ab = NOISE[k]
        val = v + rng.normal(0.0, 1.0) * (abs(v) * rel + ab) if (v != 0.0 or k in ("wht_c", "pump_temp_c")) else 0.0
        if k in ("oil_rate_m3d", "water_rate_m3d", "liquid_rate_m3d", "steam_rate_tpd", "motor_kw", "motor_current_a", "spm", "pip_mpa"):
            val = max(val, 0.0)
        if k in ("fillage", "steam_quality", "runtime"):
            val = min(max(val, 0.0), 1.0)
        meas[k] = float(val)
    return meas
