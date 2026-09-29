"""Sucker-rod pump (SRP) models.

1. `quick_srp`      analytical loading model (API RP 11L dimensionless groups + Mills
                    acceleration factor + heavy-oil viscous rod drag) used inside the cycle
                    simulator and optimiser where thousands of evaluations are needed.
2. `simulate_cards` time-domain solution of the damped wave equation for a tapered rod
                    string (lumped-mass discretisation) driven by conventional pumping-unit
                    kinematics at the top and a pump valve state machine at the bottom.
                    Generates realistic surface dynamometer cards for any pump condition
                    (fluid pound, gas interference, valve leaks, parted rods, viscous drag).
3. `gibbs_downhole` Gibbs' diagnostic: the surface card is decomposed into Fourier
                    harmonics and propagated down the (tapered) rod string with exact
                    transfer matrices of the damped wave equation to recover the pump card.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import config
from . import fluid

PU = config.PUMPING_UNIT
PUMP = config.PUMP
E = config.STEEL_E
RHO_STEEL = 7850.0
PLUNGER_AREA = math.pi / 4.0 * PUMP["plunger_diameter_m"] ** 2
TUBING_ID = config.WELLBORE["tubing_id_m"]

CARD_CLASSES = [
    "normal",
    "fluid_pound",
    "gas_interference",
    "tv_leak",
    "sv_leak",
    "rod_parted",
    "viscous_drag",
]
CARD_CLASS_LABELS = {
    "normal": "Normal full pump",
    "fluid_pound": "Fluid pound (incomplete fillage)",
    "gas_interference": "Gas / steam-flash interference",
    "tv_leak": "Travelling valve / plunger leak",
    "sv_leak": "Standing valve leak",
    "rod_parted": "Parted rods",
    "viscous_drag": "Heavy-oil viscous drag",
}


# ----------------------------------------------------------------------------- rod string


@dataclass
class RodSection:
    length: float
    area: float
    mass_per_m: float

    @property
    def ea(self) -> float:
        return E * self.area


def rod_string(pump_depth_m: float) -> list[RodSection]:
    secs = []
    for t in config.ROD_TAPERS:
        secs.append(RodSection(pump_depth_m * t["fraction"], math.pi / 4.0 * t["diameter_m"] ** 2, t["mass_per_m"]))
    return secs


def rod_weights(secs: list[RodSection], rho_fluid: float) -> tuple[float, float]:
    """Return (weight in air, buoyant weight in fluid), N."""
    w_air = sum(s.mass_per_m * s.length for s in secs) * config.G
    return w_air, w_air * (1.0 - rho_fluid / RHO_STEEL)


def drag_coefficient(mu_pa_s: float, rod_diameter: float) -> float:
    """Couette viscous drag per unit length per unit velocity for a rod in tubing (N.s/m2)."""
    return 2.0 * math.pi * mu_pa_s / math.log(TUBING_ID / rod_diameter)


def structural_damping(pump_depth_m: float) -> float:
    """Gibbs-style damping coefficient for a light-fluid well (1/s), damping factor 0.05."""
    v = math.sqrt(E / RHO_STEEL)
    return math.pi * v * 0.05 / (2.0 * pump_depth_m)


def plunger_friction_coeff(mu_pa_s: float) -> float:
    """Viscous plunger-barrel friction per unit plunger velocity (N.s/m)."""
    return mu_pa_s * math.pi * PUMP["plunger_diameter_m"] * PUMP["plunger_length_m"] / PUMP["plunger_clearance_m"]


# ----------------------------------------------------------------------------- kinematics


def polished_rod_position(theta: np.ndarray, stroke: float) -> np.ndarray:
    """Conventional crank-balanced unit, theta=0 at bottom of stroke. Returns upward position."""
    lam = PU["crank_pitman_ratio"]
    return stroke / 2.0 * ((1.0 - np.cos(theta)) + lam / 2.0 * (1.0 - np.cos(2.0 * theta)))


def torque_factor(theta: np.ndarray, stroke: float) -> np.ndarray:
    lam = PU["crank_pitman_ratio"]
    return stroke / 2.0 * (np.sin(theta) + lam * np.sin(2.0 * theta))


# ----------------------------------------------------------------------------- Goodman


def goodman_loading(s_max: float, s_min: float) -> float:
    """API modified Goodman diagram loading fraction (1.0 = 100 % of allowable)."""
    t = config.ROD_GRADE_D_TENSILE_PA
    sa = (t / 4.0 + 0.5625 * s_min) * config.GOODMAN_SERVICE_FACTOR
    if sa - s_min <= 0:
        return 9.99
    return max(s_max - s_min, 0.0) / (sa - s_min)


def string_goodman(secs, pprl, mprl, rho_fluid):
    """Max Goodman loading over the tops of all tapers."""
    worst = 0.0
    w_above = 0.0  # buoyant weight of the tapers above the current one
    buoy = config.G * (1.0 - rho_fluid / RHO_STEEL)
    for s in secs:
        # load at the top of this taper = PRL minus the buoyant weight of the tapers above it
        worst = max(worst, goodman_loading((pprl - w_above) / s.area, (mprl - w_above) / s.area))
        w_above += s.mass_per_m * s.length * buoy
    return worst


# ----------------------------------------------------------------------------- analytical model


def viscous_fill_limit(mu_pump_cp: float, spm: float) -> float:
    """Heavy-oil barrel filling limit: the standing valve cannot pass viscous oil fast enough.

    Empirical form 1/(1 + mu*SPM/K); K calibrated so that ~2,000 cP at 5 SPM gives ~85 %."""
    return 1.0 / (1.0 + mu_pump_cp * spm / PUMP["viscous_fill_constant"])


def quick_srp(
    spm: float,
    stroke: float,
    pump_depth: float,
    pip_mpa: float,
    rho_liq: float,
    mu_tubing_cp: float,
    fillage: float = 1.0,
    whp_mpa: float = config.WELLBORE["wellhead_pressure_mpa"],
) -> dict:
    """Analytical SRP loads, capacity and constraints for one operating point."""
    secs = rod_string(pump_depth)
    w_air, w_rf = rod_weights(secs, rho_liq)
    p_dis = whp_mpa * 1e6 + rho_liq * config.G * pump_depth
    fo = max(PLUNGER_AREA * (p_dis - pip_mpa * 1e6), 0.0)
    # API RP 11L dimensionless groups
    k_r = 1.0 / sum(s.length / s.ea for s in secs)          # N/m
    stretch = fo / k_r
    omega = spm * 2.0 * math.pi / 60.0
    v_rod = math.sqrt(E / RHO_STEEL)
    n_over_n0 = omega * pump_depth / (v_rod * math.pi / 2.0)  # N/N0' (N0 = quarter-wave natural freq)
    overtravel = stroke * 0.5 * (omega * pump_depth / v_rod) ** 2
    sp = max(stroke - stretch + overtravel, 0.05 * stroke)
    capacity = PLUNGER_AREA * sp * spm * 1440.0 * PUMP["slippage_efficiency"]  # m3/d at 100 % fill
    # Mills acceleration factor and viscous drag (heavy oil)
    alpha = stroke * spm ** 2 / 1790.0
    mu = mu_tubing_cp / 1000.0
    c_drag = sum(drag_coefficient(mu, math.sqrt(4 * s.area / math.pi)) * s.length for s in secs)  # N.s/m
    v_peak = math.pi * stroke * spm / 60.0
    f_drag = c_drag * v_peak
    f_plunger = plunger_friction_coeff(mu) * v_peak
    # fluid-pound impact: plunger velocity when it hits the liquid, relative to peak velocity
    f_void = min(max(1.0 - fillage, 0.0), 1.0)
    impact = 2.0 * math.sqrt(f_void * (1.0 - f_void))
    pound_severity = impact * min(1.0, spm / 6.0) * (1.0 if fillage < 0.95 else 0.0)
    pprl = w_rf + fo + w_air * alpha + f_drag + f_plunger
    mprl = w_rf - w_air * alpha - f_drag - f_plunger - 0.35 * fo * pound_severity
    goodman = string_goodman(secs, pprl, mprl, rho_liq)
    # balanced-unit peak gearbox torque and motor power
    torque = (pprl - mprl) * stroke / 4.0
    q_liq = capacity * min(fillage, 1.0)
    hyd_kw = q_liq / 86400.0 * (p_dis - pip_mpa * 1e6) / 1000.0
    fric_kw = (f_drag + f_plunger) * (2.0 * stroke * spm / 60.0) / 1000.0 * 0.64
    motor_kw = (hyd_kw + fric_kw) / PU["system_efficiency"] + 1.5
    # rod-fall limit: rods must fall faster than the polished rod on the downstroke
    v_fall = w_rf / max(c_drag + plunger_friction_coeff(mu), 1e-6)
    spm_rodfall = 60.0 * v_fall / (math.pi * stroke)
    return dict(
        fo=fo, w_rf=w_rf, w_air=w_air, k_r=k_r, stretch=stretch, sp=sp, capacity=capacity,
        pprl=pprl, mprl=mprl, goodman=goodman, torque=torque, motor_kw=motor_kw,
        spm_rodfall=spm_rodfall, n_over_n0=n_over_n0, fo_over_skr=fo / (k_r * stroke),
        pound_severity=pound_severity, f_drag=f_drag + f_plunger,
    )


# ----------------------------------------------------------------------------- dynamic cards


@dataclass
class CardSpec:
    stroke: float
    spm: float
    pump_depth: float
    fo: float                 # fluid load, N
    rho_liq: float = 950.0
    mu_tubing_cp: float = 50.0
    mu_pump_cp: float = 50.0
    fill: float = 1.0         # liquid fillage of the barrel
    gas: float = 0.0          # 0..1 fraction of the unfilled volume that is compressible gas
    tv_leak: float = 0.0      # 0..1 travelling-valve / plunger leak severity
    sv_leak: float = 0.0      # 0..1 standing-valve leak severity
    parted_frac: float = 0.0  # >0 : rods parted at this fraction of pump depth
    noise: float = 0.01       # relative load-cell noise


def _build_arrays(specs: list[CardSpec], n_seg: int):
    b = len(specs)
    k = np.zeros((b, n_seg))
    m = np.zeros((b, n_seg + 1))
    c = np.zeros((b, n_seg + 1))
    dx = np.zeros(b)
    for i, s in enumerate(specs):
        secs = rod_string(s.pump_depth)
        dx[i] = s.pump_depth / n_seg
        mu = s.mu_tubing_cp / 1000.0
        c_struct = structural_damping(s.pump_depth)
        z_mid = (np.arange(n_seg) + 0.5) * dx[i]
        bounds = np.cumsum([sec.length for sec in secs])
        idx = np.minimum(np.searchsorted(bounds, z_mid), len(secs) - 1)
        area = np.array([secs[j].area for j in idx])
        mpl = np.array([secs[j].mass_per_m for j in idx])
        dia = np.sqrt(4 * area / np.pi)
        k[i] = E * area / dx[i]
        seg_m = mpl * dx[i]
        seg_c = np.array([drag_coefficient(mu, d) for d in dia]) * dx[i]  # N.s/m per segment
        m[i, :-1] += seg_m / 2
        m[i, 1:] += seg_m / 2
        cc = np.zeros(n_seg + 1)
        cc[:-1] += seg_c / 2
        cc[1:] += seg_c / 2
        m[i, -1] += 15.0  # plunger + pull rod
        c[i] = cc / m[i] + c_struct
        if s.parted_frac > 0:
            j = int(np.clip(s.parted_frac * n_seg, 1, n_seg - 1))
            k[i, j] = 0.0
    return k, m, c, dx


def simulate_cards(specs: list[CardSpec], n_seg: int = 20, n_cycles: float = 2.5, n_out: int = 200, seed: int | None = None):
    """Vectorised batch simulation. Returns dict of arrays (B, n_out):
    surface_pos, surface_load (N), pump_pos, pump_load (N) and per-card w_rf."""
    rng = np.random.default_rng(seed)
    b = len(specs)
    k, m, c, dx = _build_arrays(specs, n_seg)
    stroke = np.array([s.stroke for s in specs])
    period = 60.0 / np.array([s.spm for s in specs])
    v_wave = np.sqrt(np.max(k * dx[:, None], axis=1) / np.min(m[:, 1:-1] / dx[:, None], axis=1))
    dt_crit = dx / v_wave
    steps_per_cycle = int(np.ceil(np.max(period / (0.7 * dt_crit))))
    dt = period / steps_per_cycle
    n_steps = int(steps_per_cycle * n_cycles)
    fo = np.array([s.fo for s in specs])
    fill = np.array([s.fill for s in specs])
    gas = np.array([s.gas for s in specs])
    tv = np.array([s.tv_leak for s in specs])
    sv = np.array([s.sv_leak for s in specs])
    parted = np.array([s.parted_frac > 0 for s in specs])
    c_pl = np.array([plunger_friction_coeff(s.mu_pump_cp / 1000.0) for s in specs])
    dead = PUMP["dead_space_frac"] * stroke
    pr_ratio = np.array([10.0] * b)  # discharge / intake pressure ratio for gas compression

    # static initial condition: string stretched by the fluid load
    inv_k = np.where(k > 0, 1.0 / np.maximum(k, 1e-9), 0.0)
    u = np.zeros((b, n_seg + 1))
    u[:, 1:] = np.cumsum(inv_k * np.where(parted, 0.0, fo)[:, None], axis=1)
    u_prev = u.copy()

    state = np.ones(b)                # +1 upstroke (TV closed), -1 downstroke
    p_ext = np.zeros(b)               # running extreme of plunger position
    p_bottom = np.zeros(b)
    p_top = stroke * 0.9
    sp_prev = stroke * 0.9
    eps = 0.003 * stroke
    f_pump = np.where(parted, 0.0, fo)

    rec_start = n_steps - steps_per_cycle
    rec_idx = np.linspace(0, steps_per_cycle - 1, n_out).round().astype(int) + rec_start
    rec_set = {int(v): j for j, v in enumerate(rec_idx)}
    out_spos = np.zeros((b, n_out))
    out_sload = np.zeros((b, n_out))
    out_ppos = np.zeros((b, n_out))
    out_pload = np.zeros((b, n_out))

    dt2 = dt[:, None] ** 2
    cdt = c * dt[:, None] / 2.0
    two_pi = 2.0 * np.pi
    for n in range(1, n_steps + 1):
        t = n * dt
        theta = two_pi * t / period
        u_top = -polished_rod_position(theta, stroke)
        tension = k * (u[:, 1:] - u[:, :-1])
        force = np.zeros_like(u)
        force[:, 1:-1] = tension[:, 1:] - tension[:, :-1]
        # pump boundary
        p = -u[:, -1]
        vel = (u_prev[:, -1] - u[:, -1]) / dt  # upward plunger velocity
        # --- state transitions with hysteresis
        up = state > 0
        p_ext = np.where(up, np.maximum(p_ext, p), np.minimum(p_ext, p))
        # a reversal only counts once the plunger has travelled a meaningful part of the
        # previous stroke, so stress-wave wiggles cannot flip the valve state mid-stroke
        to_down = up & (p < p_ext - eps) & (p_ext - p_bottom > 0.3 * sp_prev)
        to_up = (~up) & (p > p_ext + eps) & (p_top - p_ext > 0.3 * sp_prev)
        sp_prev = np.where(to_down, np.maximum(p_ext - p_bottom, 1e-3), sp_prev)
        p_top = np.where(to_down, p_ext, p_top)
        p_bottom = np.where(to_up, p_ext, p_bottom)
        state = np.where(to_down, -1.0, np.where(to_up, 1.0, state))
        p_ext = np.where(to_down | to_up, p, p_ext)
        up = state > 0
        # --- upstroke load
        rel_up = np.clip((p - p_bottom) / np.maximum(sp_prev, 1e-3), 0.0, 1.2)
        pickup = np.clip(rel_up / (0.02 + 0.35 * tv), 0.0, 1.0)
        load_up = fo * pickup * (1.0 - 0.55 * tv * rel_up ** 2)
        # --- downstroke load
        d = np.maximum(p_top - p, 0.0)             # distance travelled down
        void = (1.0 - fill) * sp_prev
        # fluid pound: load held until plunger meets liquid, then sharp release
        pound = fo * np.clip(1.0 - (d - void) / (0.03 * stroke), 0.0, 1.0)
        # gas: Boyle compression of the free-gas column
        l0 = void + dead
        lg = np.maximum(l0 - d, 1e-4)
        p_rel = (l0 / lg - 1.0) / (pr_ratio - 1.0)
        gas_load = fo * np.clip(1.0 - p_rel, 0.0, 1.0)
        load_dn = (1.0 - gas) * pound + gas * gas_load
        # standing-valve leak: barrel cannot hold pressure -> slow, incomplete unloading
        sv_tail = fo * (0.08 + 0.3 * sv) * np.clip(1.0 - d / np.maximum(sp_prev, 1e-3), 0.0, 1.0)
        sv_load = fo * np.exp(-d / (0.06 * stroke + 0.25 * sv * stroke)) * (1 - 0.3 * sv) + sv_tail
        load_dn = np.where(sv > 0, np.maximum(load_dn * (1 - sv), sv_load * np.clip(sv * 2.5, 0, 1)), load_dn)
        target = np.where(up, load_up, load_dn)
        target = np.where(parted, 0.0, target)
        f_pump = target + c_pl * vel * np.where(parted, 0.0, 1.0)
        force[:, -1] = f_pump - tension[:, -1]
        # --- integrate (central difference, semi-implicit damping)
        u_new = (2.0 * u - (1.0 - cdt) * u_prev + dt2 * force / m) / (1.0 + cdt)
        u_new[:, 0] = u_top
        u_prev, u = u, u_new
        j = rec_set.get(n)
        if j is not None:
            tension_now = k * (u[:, 1:] - u[:, :-1])
            out_spos[:, j] = -u[:, 0]
            out_sload[:, j] = tension_now[:, 0]
            out_ppos[:, j] = -u[:, -1]
            out_pload[:, j] = f_pump
    w_rf_full = np.zeros(b)
    w_rf_surface = np.zeros(b)
    for i, s in enumerate(specs):
        secs = rod_string(s.pump_depth)
        _, w_rf_full[i] = rod_weights(secs, s.rho_liq)
        w_rf_surface[i] = w_rf_full[i] * (s.parted_frac if s.parted_frac > 0 else 1.0)
    out_sload = out_sload + w_rf_surface[:, None]
    noise_scale = np.array([s.noise for s in specs])[:, None] * (fo[:, None] + w_rf_full[:, None])
    out_sload = out_sload + rng.normal(0.0, 1.0, out_sload.shape) * noise_scale
    out_spos = out_spos - out_spos.min(axis=1, keepdims=True)
    return dict(surface_pos=out_spos, surface_load=out_sload, pump_pos=out_ppos - out_ppos.min(axis=1, keepdims=True),
                pump_load=out_pload, w_rf=w_rf_full)


# ----------------------------------------------------------------------------- Gibbs diagnostic


def gibbs_downhole(surface_pos: np.ndarray, surface_load: np.ndarray, spm: float, pump_depth: float, rho_liq: float,
                   mu_tubing_cp: float, n_harmonics: int = 30):
    """Surface card -> downhole pump card (Gibbs 1963, exact tapered transfer matrices).

    surface_pos : upward polished-rod position sampled uniformly in time over one stroke
    surface_load: polished-rod load (N) at the same instants
    Returns (pump_pos [m, min 0], pump_load [N])."""
    secs = rod_string(pump_depth)
    _, w_rf = rod_weights(secs, rho_liq)
    n = len(surface_pos)
    omega = 2.0 * math.pi * spm / 60.0
    u0 = np.fft.rfft(-np.asarray(surface_pos, float))          # downward displacement
    f0 = np.fft.rfft(np.asarray(surface_load, float) - w_rf)   # dynamic load
    mu = mu_tubing_cp / 1000.0
    c_struct = structural_damping(pump_depth)
    u_h = u0.astype(complex)
    f_h = f0.astype(complex)
    nh = min(n_harmonics, len(u0) - 1)
    u_h[nh + 1:] = 0.0
    f_h[nh + 1:] = 0.0
    for s in secs:
        dia = math.sqrt(4 * s.area / math.pi)
        c = c_struct + drag_coefficient(mu, dia) / s.mass_per_m
        ea = s.ea
        new_u = np.zeros_like(u_h)
        new_f = np.zeros_like(f_h)
        for h in range(nh + 1):
            if h == 0:
                new_u[0] = u_h[0] + f_h[0] * s.length / ea
                new_f[0] = f_h[0]
                continue
            w = h * omega
            gamma = np.sqrt((-w * w + 1j * c * w) * s.mass_per_m / ea)
            gl = gamma * s.length
            ch, sh = np.cosh(gl), np.sinh(gl)
            new_u[h] = ch * u_h[h] + sh / (gamma * ea) * f_h[h]
            new_f[h] = ea * gamma * sh * u_h[h] + ch * f_h[h]
        u_h, f_h = new_u, new_f
    u_pump = np.fft.irfft(u_h, n)
    f_pump = np.fft.irfft(f_h, n)
    pos = -u_pump
    return pos - pos.min(), f_pump


# ----------------------------------------------------------------------------- card analytics


def card_metrics(pump_pos: np.ndarray, pump_load: np.ndarray, fo_est: float, stroke: float) -> dict:
    """Engineering metrics from a downhole card."""
    pos = np.asarray(pump_pos)
    load = np.asarray(pump_load)
    sp = float(pos.max() - pos.min())
    # upstroke = samples where position increases
    dpos = np.gradient(pos)
    up = dpos > 0
    area = float(abs(np.trapezoid(load, pos))) if hasattr(np, "trapezoid") else float(abs(np.trapz(load, pos)))
    fo = max(fo_est, 1.0)
    upper = np.quantile(load, 0.9)
    lower = np.quantile(load, 0.1)
    # effective stroke: fraction of downstroke travelled before load drops below mid-level
    mid = 0.5 * (upper + lower)
    # downstroke = from the top of the stroke to the bottom, wrapping around the sampled cycle
    top_idx = int(np.argmax(pos))
    p2 = np.roll(pos, -top_idx)
    l2 = np.roll(load, -top_idx)
    bot = int(np.argmin(p2))
    drop = np.where(l2[: bot + 1] < mid)[0]
    if len(drop) and sp > 1e-6:
        fill_est = float(np.clip(1.0 - (p2[0] - p2[drop[0]]) / sp, 0.0, 1.0))
    else:
        fill_est = 0.0
    retention = float(np.mean(load[up]) / fo) if up.any() else 0.0
    return dict(
        net_stroke_m=sp,
        stroke_efficiency=sp / max(stroke, 1e-6),
        fillage_est=fill_est,
        upstroke_load_retention=retention,
        card_area_kj=area / 1000.0,
        load_range_kn=float((upper - lower) / 1000.0),
    )


def rasterize_card(pump_pos: np.ndarray, pump_load: np.ndarray, stroke: float, fo: float, size: int = 64) -> np.ndarray:
    """Render a downhole card into a (size,size) float image with fixed physical normalisation:
    x = position / stroke in [0, 1.1], y = load / Fo in [-0.8, 1.6]. Fixed scaling (not min-max)
    keeps collapsed cards (parted rods) distinguishable."""
    x = np.asarray(pump_pos) / max(stroke, 1e-6)
    y = np.asarray(pump_load) / max(fo, 1.0)
    xs = np.concatenate([x, x[:1]])
    ys = np.concatenate([y, y[:1]])
    t = np.linspace(0, 1, len(xs))
    tt = np.linspace(0, 1, len(xs) * 8)
    xd = np.interp(tt, t, xs)
    yd = np.interp(tt, t, ys)
    col = np.clip((xd / 1.1) * (size - 1), 0, size - 1)
    row = np.clip((1.0 - (yd + 0.8) / 2.4) * (size - 1), 0, size - 1)
    img = np.zeros((size, size), dtype=np.float32)
    r0 = np.floor(row).astype(int)
    c0 = np.floor(col).astype(int)
    fr = row - r0
    fc = col - c0
    r1 = np.minimum(r0 + 1, size - 1)
    c1 = np.minimum(c0 + 1, size - 1)
    np.add.at(img, (r0, c0), (1 - fr) * (1 - fc))
    np.add.at(img, (r0, c1), (1 - fr) * fc)
    np.add.at(img, (r1, c0), fr * (1 - fc))
    np.add.at(img, (r1, c1), fr * fc)
    img = np.clip(img, 0, 1.5)
    return img / max(float(img.max()), 1e-6)


def torque_and_power_from_card(surface_pos, surface_load, stroke, spm):
    """Net gearbox torque with an optimally set counterbalance, and average motor power."""
    n = len(surface_pos)
    theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
    tf = torque_factor(theta, stroke)
    load = np.asarray(surface_load)
    best = None
    for m_cb in np.linspace(0.0, float(np.max(np.abs(tf * load))) * 1.5, 120):
        tq = tf * load - m_cb * np.sin(theta)
        peak = float(np.max(np.abs(tq)))
        if best is None or peak < best[0]:
            best = (peak, m_cb, tq)
    peak, m_cb, tq = best
    omega = 2 * np.pi * spm / 60.0
    power_kw = float(np.mean(np.maximum(tq * omega, 0.0))) / 1000.0 / 0.85 + 1.0
    return dict(peak_torque_nm=peak, counterbalance_moment_nm=m_cb, torque_curve=tq.tolist(), motor_kw=power_kw)


def fo_from_pressures(pump_depth, pip_mpa, rho_liq, whp_mpa=config.WELLBORE["wellhead_pressure_mpa"]):
    p_dis = whp_mpa * 1e6 + rho_liq * config.G * pump_depth
    return max(PLUNGER_AREA * (p_dis - pip_mpa * 1e6), 0.0)


def liquid_density(t_c: float, water_cut: float) -> float:
    return (1 - water_cut) * fluid.oil_density(t_c) + water_cut * fluid.water_density(min(t_c, 250.0))
