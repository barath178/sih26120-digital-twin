import { Fragment, useEffect, useState } from "react";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { CheckCircle2, ChevronDown, ChevronRight, Play, Send } from "lucide-react";
import { engineerName, post } from "../api";
import type { CycleSeries, CycleSummary, Design } from "../types";
import TimeChart from "../components/TimeChart";
import { Button, C, DataLabel, ErrorBox, Panel, Stat, axisProps, fmt, inr, pct } from "../components/ui";
import { go } from "../router";

interface Metrics { oil_per_day: number; oil_bopd: number; cum_oil_bbl: number; sor: number; energy_per_bbl_kwh: number; failure_prob: number; avg_fillage: number; max_goodman: number; npv_per_day: number; pound_days: number }
interface ParetoPoint { oil_per_day: number; sor: number; failure_prob: number; npv_per_day: number; energy_per_bbl_kwh: number; front: boolean; recommended: boolean; J: number; design: Design }
interface Candidate {
  candidate_id: string; design: Design; metrics: Metrics; objective: number; feasibility: { status: string; reasons: string[] };
  uncertainty: { oil_low: number; oil_high: number; sor_low: number; sor_high: number; basis: string } | null;
  constraints: Record<string, { value: number; limit: number; ok: boolean }>;
}
interface OptResp {
  method: string;
  info: Record<string, number | null>;
  limits: Record<string, number>;
  weights: Record<string, number>;
  baseline: { design: Design; summary: CycleSummary; series: CycleSeries; metrics: Metrics };
  recommended: { design: Design; summary: CycleSummary; series: CycleSeries; metrics: Metrics } | null;
  gain: Record<string, number> | null;
  explanation: string[];
  factors: { factor: string; contribution: number; detail: string }[];
  pareto: ParetoPoint[];
  candidates: Candidate[];
  rejected: Record<string, number>;
  n_candidates: number;
  n_feasible: number;
  scenario_id: string;
  model_version: string;
  disclaimer: string;
}

const WELLS = Array.from({ length: 10 }, (_, i) => `BGW-${String(i + 1).padStart(3, "0")}`);
const LIMIT_DEF: { k: string; label: string; unit: string; step: number; scale?: number }[] = [
  { k: "min_fillage", label: "Min average fillage", unit: "%", step: 5, scale: 100 },
  { k: "max_goodman", label: "Max rod loading (Goodman)", unit: "%", step: 5, scale: 100 },
  { k: "max_risk", label: "Max cycle failure risk", unit: "%", step: 5, scale: 100 },
  { k: "max_steam_t", label: "Steam budget per cycle", unit: "t", step: 100 },
  { k: "max_motor_kw", label: "Max motor power", unit: "kW", step: 1 },
];
const W_DEF = [{ k: "oil", label: "Oil production" }, { k: "sor", label: "Steam-oil ratio" }, { k: "energy", label: "Energy per barrel" }, { k: "risk", label: "Failure risk" }];

function ParetoTip({ active, payload }: { active?: boolean; payload?: { payload: ParetoPoint & { kind: string } }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-line-2 bg-[#10100f]/95 px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 font-medium text-ink">{p.kind}</div>
      <div className="grid grid-cols-2 gap-x-3 text-ink-2">
        <span>Oil</span><span className="num text-right text-ink">{fmt(p.oil_per_day, 2)} m³/d</span>
        <span>SOR</span><span className="num text-right text-ink">{fmt(p.sor, 2)}</span>
        <span>Energy</span><span className="num text-right text-ink">{fmt(p.energy_per_bbl_kwh, 0)} kWh/bbl</span>
        <span>Failure risk</span><span className="num text-right text-ink">{pct(p.failure_prob, 1)}</span>
      </div>
    </div>
  );
}

export default function Optimizer({ id }: { id: string }) {
  const [method, setMethod] = useState<"nsga2" | "bayes">("nsga2");
  const [limits, setLimits] = useState<Record<string, number>>({ min_fillage: 0.6, max_goodman: 0.95, max_risk: 0.35, max_steam_t: 2000, max_motor_kw: 37 });
  const [weights, setWeights] = useState<Record<string, number>>({ oil: 3, sor: 1, energy: 0.5, risk: 0.5 });
  const [res, setRes] = useState<OptResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>("C01");

  const run = async () => {
    setBusy(true); setErr(null); setSubmitted(null);
    try {
      setRes(await post<OptResp>(`/api/wells/${id}/optimize`, { method, pop: 80, gens: 60, trials: 120, limits, weights }));
      setOpen("C01");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const submit = async () => {
    try { setSubmitted((await post<{ id: string }>(`/api/wells/${id}/optimize/submit`, { by: engineerName() })).id); } catch (e) { setErr((e as Error).message); }
  };

  useEffect(() => {
    if (location.hash.includes("autorun")) run(); // demo convenience: #/optimize/BGW-007/autorun
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const b = res?.baseline.metrics;
  const r = res?.recommended?.metrics;
  const g = res?.gain;
  const cand = (res?.pareto ?? []).filter((p) => !p.front).map((p) => ({ ...p, kind: "Feasible candidate" }));
  const front = (res?.pareto ?? []).filter((p) => p.front && !p.recommended).map((p) => ({ ...p, kind: "Pareto-optimal design" }));
  const rec = (res?.pareto ?? []).filter((p) => p.recommended).map((p) => ({ ...p, kind: "Top-ranked by your weights" }));
  const baseline = b ? [{ oil_per_day: b.oil_per_day, sor: b.sor, failure_prob: b.failure_prob, energy_per_bbl_kwh: b.energy_per_bbl_kwh, npv_per_day: b.npv_per_day, kind: "Deterministic baseline (current settings)" }] : [];
  const series = (() => {
    if (!res?.recommended) return [];
    const bs = res.baseline.series; const rs = res.recommended.series;
    const n = Math.max(bs.day.length, rs.day.length);
    return Array.from({ length: n }, (_, i) => ({ t: rs.day[i] ?? bs.day[i], oil: rs.q_oil[i] ?? null, oil_b: bs.q_oil[i] ?? null,
      spm: rs.phase[i] === "production" ? rs.spm[i] : null, spm_b: bs.phase[i] === "production" ? bs.spm[i] : null }));
  })();
  const dayOf = (t: number) => `d${Math.round(t)}`;
  const setRows: [string, (d: Design) => string][] = [
    ["Steam volume", (d) => `${fmt(d.steam_t, 0)} t`], ["Injection rate", (d) => `${fmt(d.inj_rate_tpd, 0)} t/d`], ["Soak time", (d) => `${fmt(d.soak_days, 1)} d`],
    ["Production cut-off", (d) => `${fmt(d.prod_days, 0)} d`], ["SPM schedule", (d) => d.spm_knots.map((s) => fmt(s, 1)).join(" → ")],
    ["Stroke length", (d) => `${fmt(d.stroke_m / 0.0254, 0)} in`], ["Pump depth", (d) => `${fmt(d.pump_depth_m, 0)} m`], ["Pump-off control", (d) => (d.poc ? "on" : "off")],
  ];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">Coupled CSS + SRP optimiser</h1>
          <select value={id} onChange={(e) => { setRes(null); go({ page: "optimize", id: e.target.value }); }} className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm font-semibold" aria-label="Select well">
            {WELLS.map((w) => <option key={w}>{w}</option>)}
          </select>
          <div className="flex overflow-hidden rounded-lg border border-line text-sm">
            {(["nsga2", "bayes"] as const).map((m) => (
              <button key={m} onClick={() => setMethod(m)} className={`px-3 py-1.5 ${method === m ? "bg-surface-3 text-ink" : "bg-surface text-ink-3 hover:text-ink"}`}>{m === "nsga2" ? "NSGA-II (Pareto)" : "Bayesian (Optuna)"}</button>
            ))}
          </div>
        </div>
        <Button onClick={run} disabled={busy}><Play size={15} /> {busy ? "Optimising…" : "Run optimisation"}</Button>
      </div>
      <p className="max-w-4xl text-sm text-ink-3">
        A numerical, constraint-based search (never an LLM) over steam volume, injection rate, soak, production cut-off, a 4-point VFD speed schedule, stroke and pump depth. Every candidate is re-simulated with the full coupled model: steam → heated zone → viscosity → fillage/load → oil, energy and risk. Only candidates that pass every constraint below are returned.
      </p>
      {err && <ErrorBox error={err} />}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Constraints" subtitle="USER_CONFIGURED - prototype limits, not verified field limits. A plan may never earn less than the current one.">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            {LIMIT_DEF.map((l) => (
              <label key={l.k} className="text-xs text-ink-3">{l.label} ({l.unit})
                <input type="number" step={l.step} value={Math.round((limits[l.k] * (l.scale ?? 1)) * 100) / 100} onChange={(e) => setLimits({ ...limits, [l.k]: Number(e.target.value) / (l.scale ?? 1) })}
                  className="mt-1 w-full rounded-md border border-line bg-surface-2 px-2 py-1 text-sm text-ink" />
              </label>
            ))}
          </div>
        </Panel>
        <Panel title="Objective weights" subtitle="J = −w_oil·oil + w_sor·SOR + w_energy·energy/bbl + w_risk·risk (each normalised by the baseline; lower is better)">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {W_DEF.map((w) => (
              <label key={w.k} className="text-xs text-ink-3">
                <div className="flex justify-between"><span>{w.label}</span><span className="num text-ink">{fmt(weights[w.k], 1)}</span></div>
                <input type="range" min={0} max={5} step={0.1} value={weights[w.k]} onChange={(e) => setWeights({ ...weights, [w.k]: Number(e.target.value) })} className="w-full" />
              </label>
            ))}
          </div>
        </Panel>
      </div>
      {busy && <Panel><p className="text-sm text-ink-2">{method === "nsga2" ? "NSGA-II: 80 designs × 60 generations on the surrogate, then full-physics verification" : "120 Bayesian trials on the physics model"}… a few seconds.</p></Panel>}

      {res && (
        <>
          {g && b && r ? (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <Stat label="Net value change" value={inr(g.npv_per_year_inr)} unit="/year" sub={`${inr(g.npv_per_day)} per day (simulated)`} />
              <Stat label="Oil" value={`${g.oil_per_day >= 0 ? "+" : ""}${fmt(g.oil_per_day_pct, 1)}%`} sub={`${fmt(b.oil_bopd, 1)} → ${fmt(r.oil_bopd, 1)} BOPD`} />
              <Stat label="Steam-oil ratio" value={fmt(r.sor, 2)} sub={`from ${fmt(b.sor, 2)} t/m³`} />
              <Stat label="Energy per barrel" value={fmt(r.energy_per_bbl_kwh, 0)} unit="kWh" sub={`from ${fmt(b.energy_per_bbl_kwh, 0)}`} />
              <Stat label="Failure risk (model)" value={pct(r.failure_prob, 1)} sub={`from ${pct(b.failure_prob, 1)}`} />
            </div>
          ) : <ErrorBox error={res.explanation[0]} />}
          <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink-3">{res.disclaimer} Scenario <span className="num">{res.scenario_id}</span> logged to the audit table (model {res.model_version}, data mode SYNTHETIC). <DataLabel kind="MODELLED" /></div>

          <Panel title="Feasible candidates" subtitle={`${res.n_feasible} of ${res.n_candidates} evaluated candidates pass every constraint · ranked by your objective weights · trade-offs shown as numbers`} bodyClass="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-ink-3"><tr className="border-b border-line">
                  {["", "ID", "Steam t", "Soak d", "SPM schedule", "Stroke in", "Oil BOPD (range)", "SOR (range)", "kWh/bbl", "Risk", "Fillage", "J", "Feasibility"].map((h) => <th key={h} className="whitespace-nowrap px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                <tbody>
                  {b && (
                    <tr className="num border-b border-line/60 bg-surface-2 text-ink-2">
                      <td /><td className="px-3 py-1.5 font-semibold">Baseline</td><td className="px-3">{fmt(res.baseline.design.steam_t, 0)}</td><td className="px-3">{fmt(res.baseline.design.soak_days, 1)}</td>
                      <td className="px-3">{res.baseline.design.spm_knots.map((s) => fmt(s, 1)).join(" → ")}</td><td className="px-3">{fmt(res.baseline.design.stroke_m / 0.0254, 0)}</td>
                      <td className="px-3">{fmt(b.oil_bopd, 1)}</td><td className="px-3">{fmt(b.sor, 2)}</td><td className="px-3">{fmt(b.energy_per_bbl_kwh, 0)}</td><td className="px-3">{pct(b.failure_prob, 1)}</td><td className="px-3">{pct(b.avg_fillage)}</td><td className="px-3">0.00</td><td className="px-3 text-xs">deterministic reference</td>
                    </tr>
                  )}
                  {res.candidates.map((c) => (
                    <Fragment key={c.candidate_id}>
                      <tr className="num cursor-pointer border-b border-line/60 hover:bg-surface-2" onClick={() => setOpen(open === c.candidate_id ? null : c.candidate_id)}>
                        <td className="px-2">{open === c.candidate_id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                        <td className="px-3 py-1.5 font-semibold">{c.candidate_id}</td><td className="px-3">{fmt(c.design.steam_t, 0)}</td><td className="px-3">{fmt(c.design.soak_days, 1)}</td>
                        <td className="px-3 whitespace-nowrap">{c.design.spm_knots.map((s) => fmt(s, 1)).join(" → ")}</td><td className="px-3">{fmt(c.design.stroke_m / 0.0254, 0)}</td>
                        <td className="px-3 whitespace-nowrap">{fmt(c.metrics.oil_bopd, 1)}{c.uncertainty && <span className="text-xs text-ink-3"> ({fmt(c.uncertainty.oil_low * 6.2898, 1)}–{fmt(c.uncertainty.oil_high * 6.2898, 1)})</span>}</td>
                        <td className="px-3 whitespace-nowrap">{fmt(c.metrics.sor, 2)}{c.uncertainty && <span className="text-xs text-ink-3"> ({fmt(c.uncertainty.sor_low, 1)}–{fmt(c.uncertainty.sor_high, 1)})</span>}</td>
                        <td className="px-3">{fmt(c.metrics.energy_per_bbl_kwh, 0)}</td><td className="px-3">{pct(c.metrics.failure_prob, 1)}</td><td className="px-3">{pct(c.metrics.avg_fillage)}</td><td className="px-3">{fmt(c.objective, 2)}</td>
                        <td className="px-3"><span className="inline-flex items-center gap-1 text-xs"><CheckCircle2 size={13} color={C.good} />PASS</span></td>
                      </tr>
                      {open === c.candidate_id && (
                        <tr className="border-b border-line/60 bg-surface-2">
                          <td /><td colSpan={12} className="px-3 py-3">
                            <div className="grid gap-4 lg:grid-cols-3">
                              <div>
                                <div className="mb-1 text-xs font-medium text-ink-3">Why? - what changed vs baseline</div>
                                <ul className="space-y-0.5 text-xs text-ink-2">{setRows.map(([k, f]) => <li key={k} className="flex justify-between gap-2"><span>{k}</span><span className="num">{f(res.baseline.design)} → <b className="text-ink">{f(c.design)}</b></span></li>)}</ul>
                              </div>
                              <div>
                                <div className="mb-1 text-xs font-medium text-ink-3">Constraint checks</div>
                                <ul className="space-y-0.5 text-xs text-ink-2">{Object.entries(c.constraints).map(([k, v]) => <li key={k} className="flex items-center gap-1.5"><CheckCircle2 size={12} color={v.ok ? C.good : C.crit} />{k.replace(/_/g, " ")}<span className="num ml-auto text-ink-3">{k === "gearbox_torque" ? `${fmt(v.value / 1000, 1)}/${fmt(v.limit / 1000, 1)} kN·m` : k === "motor_power" ? `${fmt(v.value, 1)}/${fmt(v.limit, 0)} kW` : k === "steam_generator" ? `${fmt(v.value, 0)}/${fmt(v.limit, 0)} t/d` : `${pct(v.value)}/${pct(v.limit)}`}</span></li>)}</ul>
                              </div>
                              <div>
                                <div className="mb-1 text-xs font-medium text-ink-3">Uncertainty</div>
                                <p className="text-xs leading-relaxed text-ink-2">{c.uncertainty ? c.uncertainty.basis : "Interval computed for the top 6 candidates only."}</p>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            {Object.keys(res.rejected).length > 0 && (
              <div className="border-t border-line px-4 py-2 text-xs text-ink-3">Candidates rejected by full-physics verification (constraint: count): {Object.entries(res.rejected).map(([k, n]) => `${k} ${n}`).join(" · ")}</div>
            )}
          </Panel>

          <div className="grid gap-5 xl:grid-cols-5">
            <Panel title="Trade-off: oil vs steam-oil ratio" subtitle={`marker size = failure risk · ${res.method}`} className="xl:col-span-3">
              <div className="mb-2 flex flex-wrap gap-4 text-xs text-ink-2">
                {[["Feasible candidate", C.ink3], ["Pareto-optimal", C.s1], ["Top-ranked", C.s2], ["Baseline", C.ink]].map(([l, col]) => <span key={l} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: col }} />{l}</span>)}
              </div>
              <div className="h-72"><ResponsiveContainer><ScatterChart margin={{ top: 8, right: 16, bottom: 18, left: 0 }}>
                <CartesianGrid />
                <XAxis type="number" dataKey="oil_per_day" name="Oil" {...axisProps} domain={["auto", "auto"]} tickFormatter={(v) => fmt(v, 1)} label={{ value: "Cycle-average oil (m³/d)", position: "insideBottom", offset: -10, fill: C.ink3, fontSize: 11 }} />
                <YAxis type="number" dataKey="sor" name="SOR" {...axisProps} width={46} domain={["auto", "auto"]} tickFormatter={(v) => fmt(v, 1)} label={{ value: "SOR (t/m³)", angle: -90, position: "insideLeft", fill: C.ink3, fontSize: 11 }} />
                <ZAxis type="number" dataKey="failure_prob" range={[50, 260]} />
                <Tooltip content={<ParetoTip />} cursor={{ strokeDasharray: "3 3" }} />
                <Scatter data={cand} fill={C.ink3} fillOpacity={0.45} isAnimationActive={false} />
                <Scatter data={front} fill={C.s1} stroke={C.surface} strokeWidth={2} isAnimationActive={false} />
                <Scatter data={baseline} fill={C.ink} stroke={C.surface} strokeWidth={2} shape="diamond" isAnimationActive={false} />
                <Scatter data={rec} fill={C.s2} stroke={C.surface} strokeWidth={2} shape="star" isAnimationActive={false} />
              </ScatterChart></ResponsiveContainer></div>
            </Panel>
            <Panel title="Explanation of the top-ranked candidate" className="xl:col-span-2"
              right={res.recommended && (submitted ? <span className="text-xs text-ink-2">Submitted for approval ✓</span> : <Button small onClick={submit}><Send size={13} /> Submit for approval</Button>)}>
              <div className="mb-3 space-y-1.5">
                {res.factors.map((f) => (
                  <div key={f.factor} className="text-xs">
                    <div className="flex justify-between text-ink-2"><span>{f.factor}</span><span className="num text-ink-3">{f.detail}</span></div>
                    <div className="mt-0.5 h-1.5 rounded-full bg-surface-3"><div className="h-full rounded-full" style={{ width: `${Math.min(Math.abs(f.contribution) * 100, 100)}%`, background: f.contribution >= 0 ? C.s3 : C.s2 }} /></div>
                  </div>
                ))}
                <p className="text-[11px] text-ink-3">Bars show each objective term's weighted improvement vs the baseline (green = better, orange = worse).</p>
              </div>
              <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-ink-2">{res.explanation.map((e, i) => <li key={i}>{e}</li>)}</ul>
              {submitted && <p className="mt-3 text-xs text-ink-3">An engineer must approve it on Alerts & approvals. The SPM schedule applies immediately; steam, soak, stroke and pump depth apply at the next cycle.</p>}
            </Panel>
          </div>

          {res.recommended && (
            <div className="grid gap-5 xl:grid-cols-3">
              <Panel title="Top-ranked settings vs baseline" bodyClass="p-0">
                <table className="w-full text-sm"><thead className="text-left text-xs text-ink-3"><tr className="border-b border-line"><th className="px-4 py-2 font-medium">Decision</th><th className="px-2 py-2 font-medium">Baseline</th><th className="px-4 py-2 font-medium">Candidate</th></tr></thead>
                  <tbody>{setRows.map(([k, f]) => <tr key={k} className="border-b border-line/60"><td className="px-4 py-1.5 text-ink-2">{k}</td><td className="num px-2 py-1.5 text-ink-2">{f(res.baseline.design)}</td><td className="num px-4 py-1.5 font-medium text-ink">{f(res.recommended!.design)}</td></tr>)}</tbody></table>
              </Panel>
              <Panel title="Time-varying pump speed" subtitle="Fast while the zone is hot, slower as it cools"><TimeChart data={series} dateOf={dayOf} unit="SPM" digits={1} series={[{ key: "spm", name: "Candidate", color: C.s2 }, { key: "spm_b", name: "Baseline", color: C.ink3, dashed: true }]} /></Panel>
              <Panel title="Oil rate over the cycle" subtitle="m³/d"><TimeChart data={series} dateOf={dayOf} unit="m³/d" digits={2} series={[{ key: "oil", name: "Candidate", color: C.s2 }, { key: "oil_b", name: "Baseline", color: C.ink3, dashed: true }]} /></Panel>
            </div>
          )}
        </>
      )}
      {!res && !busy && <Panel><p className="text-sm text-ink-3">Set constraints and weights, then run the optimisation for {id}. The twin's current calibrated state is the starting point; the current settings are the deterministic baseline.</p></Panel>}
    </div>
  );
}
