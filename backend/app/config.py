"""Field, reservoir, equipment and economic constants for the Baghewala digital twin.

Values marked PUBLISHED come from public Oil India / literature summaries of Baghewala:
  * Jodhpur Sandstone reservoir, 17-19 deg API heavy crude
  * dead-oil viscosity 10,000-13,000 cP at 50 C
  * initial reservoir temperature ~46-48 C
Everything marked ASSUMED is a representative engineering value used to calibrate the
synthetic data. Every ASSUMED value is exposed through the API so it can be replaced by
OIL field data; the calibration layer re-tunes the most uncertain ones automatically.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
DATA_DIR = BASE_DIR / "data"
MODELS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

G = 9.80665  # m/s2

# --------------------------------------------------------------------------- fluid
API_GRAVITY = 18.0                      # PUBLISHED range 17-19
T_RESERVOIR_C = 47.0                    # PUBLISHED 46-48 C
# Dead-oil viscosity table used to fit the Walther (ASTM D341) equation.
# 50 C point is PUBLISHED (10,000-13,000 cP -> 11,500 mid value); the others are ASSUMED
# from typical heavy-oil temperature behaviour and should be replaced by OIL lab PVT data.
VISCOSITY_TABLE = [  # (temperature C, viscosity cP)
    (47.0, 14500.0),
    (50.0, 11500.0),
    (60.0, 5300.0),
    (80.0, 1450.0),
    (100.0, 530.0),
    (125.0, 190.0),
    (150.0, 85.0),
    (200.0, 25.0),
    (250.0, 10.0),
]
OIL_THERMAL_EXPANSION = 6.5e-4          # 1/K  ASSUMED
OIL_HEAT_CAPACITY = 2000.0              # J/kg/K ASSUMED
WATER_HEAT_CAPACITY = 4186.0            # J/kg/K

# --------------------------------------------------------------------------- reservoir
RESERVOIR = dict(
    depth_mid_perf_m=1150.0,            # HISTORICAL_REFERENCE envelope 1050-1300 m (S4); varied per well
    net_pay_m=18.0,                     # HISTORICAL_REFERENCE envelope 5-23 m (S4)
    permeability_md=800.0,              # HISTORICAL_REFERENCE "below 1000 mD" (S4); value assumed
    porosity=0.19,                      # HISTORICAL_REFERENCE 18-20 % (S4)
    so_initial=0.80,                    # ASSUMED
    so_residual=0.25,                   # ASSUMED
    pressure_mpa=10.0,                  # HISTORICAL_REFERENCE BHP examples 1044-1600 psi (S4); value assumed
    temperature_c=T_RESERVOIR_C,
    drainage_radius_m=45.0,             # ASSUMED effective mobile-oil drainage radius
    wellbore_radius_m=0.108,            # 8.5 in hole
    skin_cold=2.0,                      # ASSUMED near-wellbore damage (removed by steam)
    rock_heat_capacity=2.35e6,          # J/m3/K volumetric, oil-sand  ASSUMED
    rock_conductivity=2.1,              # W/m/K ASSUMED
    overburden_conductivity=1.7,        # W/m/K ASSUMED
    overburden_heat_capacity=2.4e6,     # J/m3/K ASSUMED
    steam_pressure_boost_mpa=4.0,       # near-well repressurisation after steaming, ASSUMED
    injectivity_mpa_per_tpd=0.012,      # sand-face overpressure per t/d injected, ASSUMED
    frac_pressure_mpa=16.0,             # SYNTHETIC_ASSUMPTION max sand-face pressure (not a field limit)
    condensate_recoverable_frac=0.8,    # fraction of injected water that can flow back
    wor_condensate_initial=6.0,         # water-oil ratio from condensate right after soak
)

# --------------------------------------------------------------------------- wellbore
WELLBORE = dict(
    surface_temp_c=30.0,                # Rajasthan mean surface temperature
    tubing_od_m=0.0730,                 # 2-7/8 in tubing
    tubing_id_m=0.0620,
    casing_id_m=0.1594,                 # 7 in casing
    earth_conductivity=2.0,             # W/m/K
    earth_diffusivity=8.0e-7,           # m2/s
    u_injection=10.0,                   # W/m2/K overall coefficient (insulated tubing + N2 annulus) ASSUMED
    u_production=30.0,                  # W/m2/K bare tubing during production ASSUMED
    wellhead_pressure_mpa=0.8,          # flow-line back-pressure during production
    casing_pressure_mpa=0.3,
    min_submergence_m=30.0,
)

# --------------------------------------------------------------------------- SRP
STEEL_E = 2.05e11                       # Pa
ROD_GRADE_D_TENSILE_PA = 793e6          # 115 ksi
GOODMAN_SERVICE_FACTOR = 0.9            # mildly corrosive (hot produced water)
ROD_TAPERS = [                          # API 86 taper (7/8 x 3/4), top to bottom
    dict(name='7/8"', diameter_m=0.02223, mass_per_m=3.31, fraction=0.45),
    dict(name='3/4"', diameter_m=0.01905, mass_per_m=2.43, fraction=0.55),
]
PUMP = dict(
    plunger_diameter_m=0.05715,         # 2-1/4 in
    plunger_length_m=1.22,
    plunger_clearance_m=1.0e-4,
    slippage_efficiency=0.92,           # volumetric efficiency of a healthy pump
    dead_space_frac=0.05,
    viscous_fill_constant=56800.0,      # cP*SPM giving the heavy-oil fill limit (see srp.py)
)
PUMPING_UNIT = dict(
    model="C-320D-256-144",
    gearbox_rating_nm=36150.0,          # 320,000 in-lb
    max_stroke_m=3.66,                  # 144 in
    min_stroke_m=1.63,                  # 64 in
    crank_pitman_ratio=0.28,
    motor_rating_kw=37.0,               # 50 hp
    motor_voltage_v=415.0,
    motor_pf=0.85,
    system_efficiency=0.55,
    spm_min=1.5,
    spm_max=9.0,
)

# --------------------------------------------------------------------------- steam plant
STEAM = dict(
    generators=[
        dict(id="SG-1", capacity_tpd=250.0),
        dict(id="SG-2", capacity_tpd=250.0),
    ],
    surface_quality=0.80,
    fuel_cost_inr_per_tonne=2900.0,     # gas-fired OTSG incl. water treatment, ASSUMED
)

# --------------------------------------------------------------------------- economics (INR)
ECONOMICS = dict(
    oil_price_inr_per_m3=33000.0,       # ~ USD 65/bbl less heavy-crude discount, ASSUMED
    electricity_inr_per_kwh=8.0,
    water_handling_inr_per_m3=120.0,
    workover_cost_inr=1_200_000.0,      # rod/pump pulling job
    workover_days=4.0,
    discount_rate_annual=0.10,
    base_failure_rate_per_year=0.4,
    # maintenance planner (SYNTHETIC_ASSUMPTION): run-to-failure means fishing and rig waiting; a planned job is faster and can
    # share a rig mobilisation and the rod pull that re-steaming already requires
    unplanned_down_days=14.0,
    combined_job_saving=0.30,
    rig_mobilisation_inr=300_000.0,
    rig_group_window_days=10.0,
    maint_horizon_days=120.0,
)

# --------------------------------------------------------------------------- runtime
SIM_HOURS_PER_TICK = float(os.getenv("SIM_HOURS_PER_TICK", "2.0"))
TICK_SECONDS = float(os.getenv("TICK_SECONDS", "1.0"))
CARD_EVERY_SIM_HOURS = 6.0
CALIBRATE_EVERY_SIM_DAYS = 2.0
MQTT_HOST = os.getenv("MQTT_HOST", "")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
RANDOM_SEED = int(os.getenv("TWIN_SEED", "7"))
# build the twin after the server starts accepting requests (for hosts whose health checks time out on slow startup)
BACKGROUND_START = os.getenv("TWIN_BACKGROUND_START", "0") == "1"


# --------------------------------------------------------------------------- provenance register
# Status codes (Build Bible 16.1): VERIFIED_FIELD, PUBLIC_REFERENCE, HISTORICAL_REFERENCE,
# SYNTHETIC_ASSUMPTION, USER_CONFIGURED. No VERIFIED_FIELD values exist until OIL supplies data.
DATA_MODE = "SYNTHETIC"
MODEL_VERSION = "twin-1.1.0"
SOURCES = {
    "S1": ("SIH 2026 problem statement SIH26120", "https://sih2026.vuce.in/ps/SIH26120"),
    "S2": ("Oil India - Rajasthan Fields", "https://www.oil-india.com/hi/node/4588"),
    "S3": ("OIL India Annual Report 2024-25", "https://www.oil-india.com/files/financial_results_documents/OIL_India_Annual_Report_2024_25_0.pdf"),
    "S4": ("OIL technical tender NIT_SJI6628P22", "https://www.oil-india.com/files/oldtender/national/NIT_SJI6628P22.pdf"),
    "S5": ("OIL SRP tender NIT_CJI4999P18", "https://www.oil-india.com/files/oldtender/national/NIT_CJI4999P18.pdf"),
    "S6": ("SPE/ICoTA 2025 Baghewala SRP case study", "https://www.researchgate.net/publication/398187378"),
    "S7": ("Cyclic steam stimulation overview", "https://www.sciencedirect.com/topics/engineering/cyclic-steam-stimulation"),
    "S8": ("Digital twins for O&G production systems (2025)", "https://www.sciencedirect.com/science/article/abs/pii/S0950584925000813"),
    "S9": ("AI/ML for O&G surface networks (2026)", "https://www.sciencedirect.com/science/article/pii/S2666546826000984"),
    "S10": ("Comprehensive evaluation of CSS for heavy-oil recovery (2025)", "https://link.springer.com/article/10.1007/s13202-025-02052-1"),
    "S11": ("OIL environmental compliance record", "https://www.oil-india.com/environmental-compliance-grants"),
}
PROVENANCE = [
    # (parameter, value used, status, source, note)
    ("Field / formation", "Baghewala, Rajasthan / Jodhpur Sandstone", "PUBLIC_REFERENCE", "S1, S2", "asset context"),
    ("API gravity", f"{API_GRAVITY:.0f} deg API", "PUBLIC_REFERENCE", "S1", "problem-context range 17-19"),
    ("Reservoir temperature", f"{T_RESERVOIR_C:.0f} C", "PUBLIC_REFERENCE", "S1", "SIH 46-48 C; older OIL documents 50-52 C"),
    ("Viscosity at 50 C", "11,300 cP (Walther fit)", "HISTORICAL_REFERENCE", "S1, S4", "about 13,000 cP at 50 C in S4; other table points assumed"),
    ("Mid-perforation depth", f"{RESERVOIR['depth_mid_perf_m']:.0f} m (wells 1060-1290 m)", "HISTORICAL_REFERENCE", "S4", "envelope 1050-1300 m"),
    ("Net pay", f"{RESERVOIR['net_pay_m']:.0f} m (wells vary 14-21 m)", "HISTORICAL_REFERENCE", "S4", "envelope 5-23 m"),
    ("Porosity", f"{RESERVOIR['porosity']:.2f}", "HISTORICAL_REFERENCE", "S4", "18-20 %"),
    ("Permeability", f"{RESERVOIR['permeability_md']:.0f} mD (wells 650-950 mD)", "HISTORICAL_REFERENCE", "S4", "below 1000 mD; value assumed"),
    ("Reservoir pressure", f"{RESERVOIR['pressure_mpa']:.1f} MPa", "HISTORICAL_REFERENCE", "S4", "BHP examples 1044-1600 psi"),
    ("Thermal completion (VIT)", f"U = {WELLBORE['u_injection']:.0f} W/m2K", "PUBLIC_REFERENCE", "S2", "VIT use is public; coefficient assumed"),
    ("Tubing / pump", "2-7/8 in tubing, 2-1/4 in insert pump", "PUBLIC_REFERENCE", "S6", "pump about 1035 m in case study; plunger size assumed"),
    ("Rod taper, pumping unit, motor", "API 86 taper, C-320 unit, 37 kW", "SYNTHETIC_ASSUMPTION", "-", "equipment ratings are placeholders"),
    ("Goodman / torque / motor limits", "95 % / 36.2 kN.m / 37 kW (editable)", "USER_CONFIGURED", "-", "prototype feasibility envelope, not field limits"),
    ("Min fillage, max risk, steam budget", "optimiser inputs", "USER_CONFIGURED", "-", "set on the optimiser page"),
    ("Steam generators", "2 x 250 t/d, quality 0.80", "SYNTHETIC_ASSUMPTION", "-", ""),
    ("Thermal rock properties", "M = 2.35 MJ/m3K, K = 2.1 W/mK", "SYNTHETIC_ASSUMPTION", "S7", "typical oil-sand values"),
    ("Economics (oil price, fuel, power, workover)", "INR 33,000/m3, 2,900/t, 8/kWh, 12 lakh", "SYNTHETIC_ASSUMPTION", "-", ""),
    ("CSS history", "38 cycles executed; 10 in FY2024-25", "PUBLIC_REFERENCE", "S3", "context only, not a model target"),
    ("Telemetry, cards, events", "generated by the field simulator", "SYNTHETIC_ASSUMPTION", "-", "seed-deterministic"),
    ("Maintenance planner economics", "14 d unplanned vs 4 d planned downtime; 30 % saving when combined with the re-steam pull; INR 3 lakh rig mobilisation", "SYNTHETIC_ASSUMPTION", "-", "replace with OIL workover records"),
]
DISCLAIMER = ("DEMO DATA MODE - This dashboard is running on synthetic demonstration data calibrated to publicly reported "
              "Baghewala context. Values are not live Oil India measurements and must not be used as field operating "
              "instructions. Replace the dataset with validated field data before operational use.")
SAFETY_WORDING = ("The system is a decision-support prototype. Its recommendations are simulated against synthetic data and "
                  "reduced-order models. It does not directly control Oil India equipment, and we would require validated "
                  "field data, engineering review, and approved operating limits before deployment.")
VFD_BASE_HZ = 60.0
SPM_AT_BASE_HZ = 8.0   # SYNTHETIC_ASSUMPTION: sheave ratio giving 8 SPM at 60 Hz (VFD frequency proxy)
STEAM_ENERGY_KWH_PER_T = 2340.0 / 3.6 / 0.85  # SYNTHETIC_ASSUMPTION: ~2.34 GJ/t enthalpy rise, 85 % generator efficiency
