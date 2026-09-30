import { useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CheckCircle2, Copy, Plus, RotateCcw, Trash2, XCircle } from "lucide-react";
import { get, post } from "../api";
import type { CycleSeries, CycleSummary, Design } from "../types";
import TimeChart from "../components/TimeChart";
import { Button, C, ChartTip, DataLabel, ErrorBox, Legend, Loading, Panel, axisProps, fmt, inr, pct } from "../components/ui";
import { go } from "../router";
import { DEMO } from "../demo/mock";
import { physicsReady } from "../demo/pyodide";

interface SimResp {
  design: Design; summary: CycleSummary; series: CycleSeries; scenario_id: string;
  current?: { design: Design; summary: CycleSummary; series: CycleSeries };
}
type Editable = Pick<Design, "steam_t" | "inj_rate_tpd" | "quality" | "soak_days" | "prod_days" | "spm_knots" | "stroke_m" | "pump_depth_m" | "poc">;
interface Tornado { base: { npv_per_day: number; oil_per_day: number }; rows: { variable: string; base: number; swing: number; low: { value: number; npv_per_day: number }; high: { value: number; npv_per_day: number } }[] }

const WELLS = Array.from({ length: 10 }, (_, i) => `BGW-${String(i + 1).padStart(3, "0")}`);
const SC_COLOR = [C.s1, C.s2, C.s3];
const VAR_LABEL: Record<string, string> = { steam_t: "Steam volume", inj_rate_tpd: "Injection rate", soak_days: "Soak time", prod_days: "Production cut-off", spm1: "SPM at start", spm2: "SPM at 15%", spm3: "SPM at 40%", spm4: "SPM at end", stroke_m: "Stroke length", pump_offset_m: "Pump setting depth" };

function Slider({ label, value, min, max, step, unit, onChange, digits = 0 }: { label: string; value: number; min: number; max: number; step: number; unit: string; onChange: (v: number) => void; digits?: number }) {
  return (
    <label className="block py-1.5">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-ink-2">{label}</span>
        <span className="num font-semibold text-ink">{fmt(value, digits)} <span className="font-normal text-ink-3">{unit}</span></span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="mt-1 w-full" />
    </label>
  );
}

const CONSTRAINT_LABEL: Record<string, string> = {
  goodman: "Rod loading ≤ 100% Goodman", gearbox_torque: "Gearbox torque ≤ rating", motor_power: "Motor power ≤ rating",
  steam_generator: "Injection rate ≤ generator capacity", pump_fillage: "Average fillage ≥ 60%", rod_fall: "SPM ≤ 90% of rod-fall limit",
};

export default function WhatIf({ id }: { id: string }) {
  const [base, setBase] = useState<Editable | null>(null);
  const [depth, setDepth] = useState(1100);
  const [scen, setScen] = useState<Editable[]>([]);
  const [active, setActive] = useState(0);
  const [res, setRes] = useState<(SimResp | null)[]>([]);
  const [cur, setCur] = useState<SimResp["current"] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tor, setTor] = useState<Tornado | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    setScen([]); setRes([]); setCur(null); setTor(null); setActive(0);
    get<{ design: Design; next_design: Design | null; twin_params: Record<string, number> }>(`/api/wells/${id}`).then((w) => {
      const src = w.next_design ?? w.design;
      const e: Editable = { steam_t: src.steam_t, inj_rate_tpd: src.inj_rate_tpd, quality: src.quality, soak_days: src.soak_days, prod_days: src.prod_days, spm_knots: [...src.spm_knots], stroke_m: src.stroke_m, pump_depth_m: src.pump_depth_m, poc: src.poc };
      setBase(e); setScen([e]); setDepth(w.twin_params.depth_m);
    }).catch((e) => setErr(e.message));
    get<Tornado>(`/api/wells/${id}/sensitivity`).then(setTor).catch(() => undefined);
  }, [id]);

  useEffect(() => {
    if (!scen.length) return;
    const my = ++seq.current;
    const t = window.setTimeout(async () => {
      setBusy(true);
      try {
        const out = await Promise.all(scen.map((d, i) => post<SimResp>("/api/simulate", { well_id: id, design: { ...d, vfd_auto: false }, compare_current: i === 0 })));
        if (my === seq.current) { setRes(out); setCur(out[0].current ?? null); setErr(null); }
      } catch (e) { if (my === seq.current) setErr((e as Error).message); } finally { if (my === seq.current) setBusy(false); }
    }, 350);
    return () => window.clearTimeout(t);
  }, [scen, id]);

  const rows = useMemo(() => {
    if (!res.length || !cur) return [];
    const map = new Map<number, Record<string, number | null>>();
    const put = (key: string, s: CycleSeries, f: (i: number) => number | null) => s.day.forEach((d, i) => { const k = Math.round(d); map.set(k, { ...(map.get(k) ?? { t: k }), [key]: f(i) }); });
    const prod = (s: CycleSeries, i: number, v: number) => (s.phase[i] === "production" ? v : null);
    put("oil_b", cur.series, (i) => cur.series.q_oil[i]);
    put("spm_b", cur.series, (i) => prod(cur.series, i, cur.series.spm[i]));
    put("fill_b", cur.series, (i) => prod(cur.series, i, cur.series.fillage[i] * 100));
    put("good_b", cur.series, (i) => prod(cur.series, i, cur.series.goodman[i] * 100));
    put("temp_b", cur.series, (i) => cur.series.t_avg[i]);
    res.forEach((r, k) => {
      if (!r) return;
      put(`oil${k}`, r.series, (i) => r.series.q_oil[i]);
      put(`spm${k}`, r.series, (i) => prod(r.series, i, r.series.spm[i]));
      put(`fill${k}`, r.series, (i) => prod(r.series, i, r.series.fillage[i] * 100));
      put(`good${k}`, r.series, (i) => prod(r.series, i, r.series.goodman[i] * 100));
      put(`temp${k}`, r.series, (i) => r.series.t_avg[i]);
    });
    return [...map.values()].sort((a, b) => Number(a.t) - Number(b.t));
  }, [res, cur]);

  if (!base || !scen.length) return err ? <ErrorBox error={err} /> : <Loading />;
  const d = scen[active];
  const set = (patch: Partial<Editable>) => setScen(scen.map((s, i) => (i === active ? { ...s, ...patch } : s)));
  const setKnot = (i: number, v: number) => set({ spm_knots: d.spm_knots.map((k, j) => (j === i ? v : k)) });
  const dayOf = (t: number) => `d${Math.round(t)}`;
  const mk = (key: string) => res.map((_, k) => ({ key: `${key}${k}`, name: `Scenario ${"ABC"[k]}`, color: SC_COLOR[k] })).concat([{ key: `${key}_b`, name: "Current settings", color: C.ink3, dashed: true } as never]);
  const cs = cur?.summary;
  const metrics: { label: string; get: (s: CycleSummary) => number; fmt: (v: number) => string; better: "up" | "down" }[] = [
    { label: "Oil (BOPD, cycle average)", get: (s) => s.oil_per_day * 6.2898, fmt: (v) => fmt(v, 1), better: "up" },
    { label: "Steam-oil ratio (t/m³)", get: (s) => s.sor, fmt: (v) => fmt(v, 2), better: "down" },
    { label: "Energy per barrel (kWh/bbl)", get: (s) => (s as unknown as { energy_per_bbl_kwh: number }).energy_per_bbl_kwh, fmt: (v) => fmt(v, 0), better: "down" },
    { label: "Failure risk (cycle)", get: (s) => s.failure_prob * 100, fmt: (v) => `${fmt(v, 1)}%`, better: "down" },
    { label: "Average pump fillage", get: (s) => s.avg_fillage * 100, fmt: (v) => `${fmt(v, 0)}%`, better: "up" },
    { label: "Peak rod loading (Goodman)", get: (s) => s.max_goodman * 100, fmt: (v) => `${fmt(v, 0)}%`, better: "down" },
    { label: "Net value (₹/day)", get: (s) => s.npv_per_day, fmt: (v) => inr(v), better: "up" },
  ];
  const tornadoData = tor ? tor.rows.map((r) => ({ name: VAR_LABEL[r.variable] ?? r.variable, low: r.low.npv_per_day - tor.base.npv_per_day, high: r.high.npv_per_day - tor.base.npv_per_day })) : [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold">What-if simulator</h1>
          <select value={id} onChange={(e) => go({ page: "optimize", id: e.target.value, tab: "whatif" })} className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm font-semibold" aria-label="Select well">{WELLS.map((w) => <option key={w}>{w}</option>)}</select>
          <DataLabel kind="MODELLED" />
          <span className="text-xs text-ink-3">{busy ? (DEMO && !physicsReady() ? "Loading the physics engine in your browser (first time only, up to ~30 s)…" : "Simulating…") : "Full coupled physics, whole-cycle trajectories; compare the current baseline with up to 3 scenarios"}</span>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => { setScen([base]); setActive(0); }}><RotateCcw size={14} /> Reset</Button>
          <Button variant="ghost" onClick={() => go({ page: "optimize", id })}>Let the optimiser search</Button>
        </div>
      </div>
      {err && <ErrorBox error={err} />}

      <div className="flex flex-wrap items-center gap-2">
        {scen.map((_, i) => (
          <button key={i} onClick={() => setActive(i)} className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm ${active === i ? "border-line-2 bg-surface-3 text-ink" : "border-line bg-surface text-ink-2 hover:text-ink"}`}>
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: SC_COLOR[i] }} />Scenario {"ABC"[i]}
          </button>
        ))}
        {scen.length < 3 && <Button small variant="ghost" onClick={() => { setScen([...scen, { ...d, spm_knots: [...d.spm_knots] }]); setActive(scen.length); }}><Copy size={13} /> Duplicate as new scenario</Button>}
        {scen.length < 3 && <Button small variant="ghost" onClick={() => { setScen([...scen, { ...base, spm_knots: [...base.spm_knots] }]); setActive(scen.length); }}><Plus size={13} /> Add from current</Button>}
        {scen.length > 1 && <Button small variant="ghost" onClick={() => { setScen(scen.filter((_, i) => i !== active)); setActive(0); }}><Trash2 size={13} /> Remove {"ABC"[active]}</Button>}
      </div>

      <div className="grid gap-5 xl:grid-cols-4">
        <div className="space-y-5">
          <Panel title={`Scenario ${"ABC"[active]}: steam cycle (CSS)`}>
            <Slider label="Steam volume" value={d.steam_t} min={300} max={2500} step={50} unit="t" onChange={(v) => set({ steam_t: v })} />
            <Slider label="Injection rate" value={d.inj_rate_tpd} min={100} max={250} step={10} unit="t/d" onChange={(v) => set({ inj_rate_tpd: v })} />
            <Slider label="Steam quality at wellhead" value={d.quality} min={0.5} max={0.95} step={0.01} unit="" digits={2} onChange={(v) => set({ quality: v })} />
            <Slider label="Soak time" value={d.soak_days} min={0} max={15} step={0.5} unit="days" digits={1} onChange={(v) => set({ soak_days: v })} />
            <Slider label="Production cut-off" value={d.prod_days} min={40} max={300} step={5} unit="days" onChange={(v) => set({ prod_days: v })} />
          </Panel>
          <Panel title={`Scenario ${"ABC"[active]}: rod pump (SRP)`} subtitle="VFD speed at cycle start, 15%, 40% and end of production">
            {["Start", "15%", "40%", "End"].map((lbl, i) => <Slider key={lbl} label={`SPM at ${lbl}`} value={d.spm_knots[i]} min={1.5} max={9} step={0.1} unit="SPM" digits={1} onChange={(v) => setKnot(i, v)} />)}
            <Slider label="Stroke length" value={d.stroke_m / 0.0254} min={64} max={144} step={1} unit="in" onChange={(v) => set({ stroke_m: v * 0.0254 })} />
            <Slider label="Pump setting depth" value={d.pump_depth_m} min={Math.round(depth - 260)} max={Math.round(depth - 10)} step={5} unit="m" onChange={(v) => set({ pump_depth_m: v })} />
            <label className="mt-2 flex items-center gap-2 text-xs text-ink-2"><input type="checkbox" checked={d.poc} onChange={(e) => set({ poc: e.target.checked })} /> Pump-off control (idle when the barrel can't fill)</label>
          </Panel>
        </div>

        <div className="space-y-5 xl:col-span-3">
          <Panel title="Scenario comparison" subtitle="Delta vs the current settings (green = better, red = worse)" bodyClass="p-0">
            <div className="overflow-x-auto"><table className="w-full text-sm">
              <thead className="text-left text-xs text-ink-3"><tr className="border-b border-line"><th className="px-4 py-2 font-medium">Metric</th><th className="px-3 py-2 font-medium">Current</th>{res.map((_, k) => <th key={k} className="px-3 py-2 font-medium"><span className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: SC_COLOR[k] }} />Scenario {"ABC"[k]}</th>)}</tr></thead>
              <tbody>
                {cs && metrics.map((m) => (
                  <tr key={m.label} className="num border-b border-line/60">
                    <td className="px-4 py-1.5 text-ink-2">{m.label}</td><td className="px-3 py-1.5">{m.fmt(m.get(cs))}</td>
                    {res.map((r, k) => {
                      if (!r) return <td key={k} />;
                      const dv = m.get(r.summary) - m.get(cs);
                      const good = m.better === "up" ? dv > 0 : dv < 0;
                      return <td key={k} className="px-3 py-1.5"><span className="font-semibold">{m.fmt(m.get(r.summary))}</span> <span className={`text-xs ${Math.abs(dv) < 1e-9 ? "text-ink-3" : good ? "text-[#5fd35f]" : "text-[#f28b8b]"}`}>{dv >= 0 ? "+" : ""}{Math.abs(m.get(cs)) > 1e4 ? inr(dv) : fmt(dv, 2)}</span></td>;
                    })}
                  </tr>
                ))}
                <tr className="border-b border-line/60"><td className="px-4 py-1.5 text-ink-2">All constraints</td><td className="px-3">{cs ? (Object.values(cs.constraints).every((c) => c.ok) ? "PASS" : "FAIL") : "–"}</td>
                  {res.map((r, k) => <td key={k} className="px-3 py-1.5 text-xs">{r ? (Object.values(r.summary.constraints).every((c) => c.ok) ? "PASS" : `FAIL: ${Object.entries(r.summary.constraints).filter(([, c]) => !c.ok).map(([n]) => n.replace(/_/g, " ")).join(", ")}`) : ""}</td>)}</tr>
              </tbody>
            </table></div>
          </Panel>

          <div className="grid gap-5 lg:grid-cols-2">
            <Panel title="Oil rate over the cycle" subtitle="m³/d"><TimeChart data={rows} dateOf={dayOf} unit="m³/d" digits={2} series={mk("oil")} /></Panel>
            <Panel title="Pump speed" subtitle="SPM during production"><TimeChart data={rows} dateOf={dayOf} unit="SPM" digits={1} series={mk("spm")} /></Panel>
            <Panel title="Heated-zone temperature" subtitle="°C: injection heats, cooldown decays"><TimeChart data={rows} dateOf={dayOf} unit="°C" digits={0} series={mk("temp")} /></Panel>
            <Panel title="Pump fillage" subtitle="% of barrel while running"><TimeChart data={rows} dateOf={dayOf} unit="%" digits={0} yDomain={[0, 100]} series={mk("fill")} /></Panel>
            <Panel title="Rod stress" subtitle="% of modified-Goodman allowable"><TimeChart data={rows} dateOf={dayOf} unit="%" digits={0} refY={{ y: 100, label: "limit" }} series={mk("good")} /></Panel>
            <Panel title="Constraints (scenario A)" subtitle="Checked on the full physics simulation">
              <ul className="space-y-1.5 text-sm">
                {res[0] && Object.entries(res[0].summary.constraints).map(([k, c]) => (
                  <li key={k} className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2 text-ink-2">{c.ok ? <CheckCircle2 size={15} color={C.good} aria-label="satisfied" /> : <XCircle size={15} color={C.crit} aria-label="violated" />}{CONSTRAINT_LABEL[k] ?? k}</span>
                    <span className="num text-xs text-ink">{k === "gearbox_torque" ? `${fmt(c.value / 1000, 1)} / ${fmt(c.limit / 1000, 1)} kN·m` : k === "motor_power" ? `${fmt(c.value, 1)} / ${fmt(c.limit, 0)} kW` : k === "steam_generator" ? `${fmt(c.value, 0)} / ${fmt(c.limit, 0)} t/d` : `${pct(c.value)} / ${pct(c.limit)}`}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          </div>

          <Panel title="Sensitivity (tornado)" subtitle="Change in net value (₹/day) when each decision moves ±15% of its search range, all else at current settings"
            right={<Legend items={[{ label: "decrease the variable", color: C.s2 }, { label: "increase the variable", color: C.s1 }]} />}>
            {tor ? (
              <div style={{ height: 34 * tornadoData.length + 40 }}>
                <ResponsiveContainer>
                  <BarChart data={tornadoData} layout="vertical" margin={{ top: 4, right: 20, bottom: 4, left: 110 }} stackOffset="sign">
                    <CartesianGrid horizontal={false} />
                    <XAxis type="number" {...axisProps} tickFormatter={(v) => inr(Number(v))} />
                    <YAxis type="category" dataKey="name" {...axisProps} width={110} />
                    <Tooltip content={<ChartTip labelFmt={(l) => String(l)} valueFmt={(v) => `${v >= 0 ? "+" : ""}${inr(v)}/day`} />} />
                    <ReferenceLine x={0} stroke={C.ink3} />
                    <Bar dataKey="low" stackId="t" name="Decrease variable" fill={C.s2} isAnimationActive={false}>{tornadoData.map((_, i) => <Cell key={i} fill={C.s2} />)}</Bar>
                    <Bar dataKey="high" stackId="t" name="Increase variable" fill={C.s1} isAnimationActive={false}>{tornadoData.map((_, i) => <Cell key={i} fill={C.s1} />)}</Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : <Loading label="Computing sensitivities…" />}
          </Panel>
        </div>
      </div>
    </div>
  );
}
