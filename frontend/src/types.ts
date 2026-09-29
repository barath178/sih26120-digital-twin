export type Phase = "injection" | "soak" | "production";
export type Severity = "critical" | "warning" | "info";

export interface WellSummary {
  id: string;
  pad: string;
  x: number;
  y: number;
  phase: Phase;
  cycle_no: number;
  day_in_cycle: number;
  t_phase_d: number;
  prod_days_plan: number;
  soak_days_plan: number;
  inj_days_plan: number;
  oil: number;
  water: number;
  liquid: number;
  steam_rate: number;
  spm: number;
  fillage: number;
  runtime: number;
  pprl: number;
  mprl: number;
  wht: number;
  whp: number;
  t_avg: number;
  mu_cp: number;
  r_h: number;
  goodman: number;
  limiting: string;
  card: string | null;
  card_conf: number | null;
  card_version: number;
  anomaly: number;
  alerts: number;
  status: "ok" | "warning" | "critical" | "down";
  vfd: boolean;
  generator: string | null;
  calib_conf: number | null;
  calib_status: string | null;
  resteam_in_days: number | null;
  planned_days_left: number | null;
  cash_inr_d: number;
  sor_cycle: number | null;
  cum_oil: number;
  next_design: boolean;
  energy_bbl: number | null;
  cycle_days_plan: number;
  optimal_resteam_day: number | null;
}

export interface Generator {
  id: string;
  capacity_tpd: number;
  busy_with: string | null;
  x: number;
  y: number;
}

export interface Kpis {
  oil_m3d: number;
  oil_bpd: number;
  water_m3d: number;
  steam_tpd: number;
  sor: number | null;
  cash_inr_d: number;
  revenue_inr_d: number;
  producing: number;
  injecting: number;
  soaking: number;
  down: number;
  alerts_active: number;
  alerts_critical: number;
  pending_recommendations: number;
  avg_fillage: number;
  generators: Generator[];
}

export interface Snapshot {
  type: "tick";
  sim_time: string;
  sim_day: number;
  hours_per_tick: number;
  paused: boolean;
  bus: string;
  tick_ms: number;
  kpis: Kpis;
  wells: WellSummary[];
}

export interface Measured {
  steam_rate_tpd: number;
  steam_quality: number;
  whp_mpa: number;
  wht_c: number;
  oil_rate_m3d: number;
  water_rate_m3d: number;
  liquid_rate_m3d: number;
  spm: number;
  stroke_m: number;
  runtime: number;
  pprl_kn: number;
  mprl_kn: number;
  motor_kw: number;
  motor_current_a: number;
  fillage: number;
  pip_mpa: number;
  pump_temp_c: number;
}

export interface TwinPoint {
  q_oil: number;
  q_water: number;
  q_liq: number;
  q_liq_deliv: number;
  cap: number;
  fillage: number;
  pprl_kn: number;
  mprl_kn: number;
  goodman: number;
  t_avg: number;
  mu_cp: number;
  r_h: number;
  wht_c: number;
  motor_kw: number;
  sor_cum: number | null;
  x_bh: number;
  heat_loss_mw: number;
  limiting: string;
  flash_sev: number;
}

export interface HistoryPoint {
  t: number;
  ts: string;
  phase: Phase;
  cycle_no: number;
  day: number;
  m: Measured;
  tw: TwinPoint;
  anomaly: number;
}

export interface Alert {
  id: string;
  key: string;
  well_id: string;
  type: string;
  severity: Severity;
  title: string;
  root_cause: string;
  fix: string;
  value: number | null;
  status: "active" | "acknowledged" | "resolved";
  created: string;
  updated: string;
  resolved?: string;
  ack_by?: string;
}

export interface Recommendation {
  id: string;
  well_id: string;
  type: "spm" | "vfd" | "resteam" | "workover" | "design";
  title: string;
  detail: string[];
  payload: Record<string, unknown>;
  gain_inr_per_day: number;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  source: string;
  created: string;
  updated: string;
  decided_by?: string;
  decided_at?: string;
  note?: string;
}

export interface Design {
  steam_t: number;
  inj_rate_tpd: number;
  quality: number;
  soak_days: number;
  prod_days: number;
  spm_knots: number[];
  stroke_m: number;
  pump_depth_m: number;
  vfd_auto: boolean;
  poc: boolean;
  inj_days: number;
  total_days: number;
  spm_schedule: { day: number; spm: number }[];
}

export interface CycleSummary {
  cum_oil_m3: number;
  cum_water_m3: number;
  steam_t: number;
  sor: number;
  oil_per_day: number;
  cycle_days: number;
  avg_fillage: number;
  pound_days: number;
  max_goodman: number;
  max_torque_nm: number;
  max_motor_kw: number;
  rodfall_ratio: number;
  failure_prob: number;
  npv_inr: number;
  npv_per_day: number;
  energy_kwh: number;
  peak_oil: number;
  constraints: Record<string, { value: number; limit: number; ok: boolean }>;
}

export interface CycleSeries {
  day: number[];
  phase: Phase[];
  q_oil: number[];
  q_water: number[];
  q_liq: number[];
  q_liq_deliv: number[];
  cap: number[];
  fillage: number[];
  t_avg: number[];
  mu_oil_cp: number[];
  spm: number[];
  pprl: number[];
  mprl: number[];
  goodman: number[];
  torque: number[];
  motor_kw: number[];
  r_h: number[];
  steam_rate_tpd: number[];
  sor_cum: (number | null)[];
  wht: number[];
  failure_rate: number[];
  cash_inr: number[];
  limiting: string[];
  flash_sev: number[];
}

export interface CardData {
  t: number;
  ts: string;
  spm: number;
  stroke: number;
  pump_depth: number;
  surface: { pos: number[]; load_kn: number[] };
  downhole: { pos: number[]; load_kn: number[] };
  fo_kn: number;
  diagnosis: string;
  label: string;
  confidence: number;
  probabilities: Record<string, number>;
  metrics: {
    net_stroke_m: number;
    stroke_efficiency: number;
    fillage_est: number;
    upstroke_load_retention: number;
    card_area_kj: number;
    load_range_kn: number;
  };
  peak_torque_knm: number;
  card_motor_kw: number;
  pprl_kn: number;
  mprl_kn: number;
}
