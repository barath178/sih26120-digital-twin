import { lazy, Suspense, useMemo, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Cpu, Gauge, HeartPulse, Sliders, Wrench } from "lucide-react";
import { useFetch, useLive } from "../live";
import { engineerName, get, post } from "../api";
import type { Alert, CardData, Design, HistoryPoint, Measured, Recommendation, WellSummary } from "../types";
import CardPlot from "../components/CardPlot";
import TimeChart from "../components/TimeChart";
import { AlertItem, RecCard } from "../components/RecCard";
import { Button, C, ChartTip, DataLabel, EmptyState, ErrorBox, KV, Legend, Loading, Panel, PhaseBadge, Pill, Skeleton, Stat, Tabs, WellStatus, axisProps, fmt, inr, pct, useConfirm, useToast } from "../components/ui";
import { DEMO } from "../demo/mock";
import { go } from "../router";

const WellScene3D = lazy(() => import("../components/WellScene3D"));

interface WellDetail {
  summary: WellSummary;
  design: Design;
  next_design: Design | null;
  twin_params: Record<string, number>;
  twin_state: Record<string, number | string | null>;
  twin_now: Record<string, number | string | null>;
  measured: Measured | null;
  calibration: { status: string; confidence: number; params: Record<string, number>; mape?: number; mape_before?: number; n_days?: number };
  health: { rod_damage?: number; rod_rul_days?: number | null; pump_retention?: number | null; pump_rul_days?: number | null; failure_rate_per_year?: number };
  projection: { optimal_resteam_in_days?: number | null; planned_days_left?: number | null };
  anomaly: { score: number; anomalous: boolean };
  vfd: { enabled: boolean; spm: number | null; target_fillage: number };
  spm_override: number | null;
  alerts: Alert[];
  recommendations: Recommendation[];
}
interface CardResp { card: CardData | null; history: { diagnosis: string; confidence: number }[]; classes: string[]; labels: Record<string, string> }
interface ForecastResp {
  available: boolean; reason?: string; today_prod_day?: number; history?: { day: number; q_oil: number }[];
  ml?: { day: number; p10: number; p50: number; p90: number }[]; physics?: { day: number; q_oil: number }[];
  optimal_resteam_in_days?: number | null; planned_days_left?: number;
}
interface RiskResp {
  available: boolean; reason?: string; label?: string; risk_score?: number; focus_class?: string;
  factors?: { feature: string; text: string; value: number; contribution: number; direction: string }[]; ood?: { flag: boolean };
}
const RISK_TEXT: Record<string, string> = {
  NORMAL: "normal operation", LOW_FILLAGE: "low pump fillage", ROD_FLOAT_RISK: "rod-float risk", HIGH_IMPACT: "high impact loading", PUMP_UNSETTING_RISK: "pump-unsetting risk",
};
const WELLS = Array.from({ length: 10 }, (_, i) => `BGW-${String(i + 1).padStart(3, "0")}`);
type Tab = "overview" | "predict" | "trends";

/** Injection / soak / production bar with today's position, the planned end and the twin's economic re-steam day. */
function CycleTimeline({ s, d, optimalIn }: { s: WellSummary; d: Design; optimalIn: number | null | undefined }) {
  const total = d.inj_days + d.soak_days + d.prod_days;
  const w = (x: number) => `${(x / total) * 100}%`;
  const now = Math.min(s.day_in_cycle, total);
  const optDay = optimalIn !== null && optimalIn !== undefined && s.phase === "production" ? d.inj_days + d.soak_days + s.t_phase_d + optimalIn : null;
  return (
    <div>
      <div className="relative flex h-5 overflow-hidden rounded-md">
        <div style={{ width: w(d.inj_days), background: C.s2 }} title={`Steam injection ${fmt(d.inj_days, 1)} d`} />
        <div style={{ width: w(d.soak_days), background: C.s3 }} title={`Soak ${fmt(d.soak_days, 1)} d`} />
        <div style={{ width: w(d.prod_days), background: "#1d4f8f" }} title={`Production ${fmt(d.prod_days, 0)} d`} />
        <div className="absolute inset-y-0 w-0.5 bg-white shadow" style={{ left: w(now) }} />
        {optDay !== null && <div className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 border border-white bg-s7" style={{ left: `${Math.min((optDay / total) * 100, 99)}%` }} title="Twin's economic re-steam day" />}
      </div>
      <div className="relative mt-1 h-4 text-[11px] text-ink-3">
        <span className="mono absolute text-ink" style={{ left: `min(${(now / total) * 100}%, calc(100% - 4.5rem))` }}>▲ day {fmt(s.day_in_cycle, 0)}</span>
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-2">
        <span><span className="mr-1.5 inline-block h-2 w-3 rounded-sm" style={{ background: C.s2 }} />Steam {fmt(d.inj_days, 1)} d · {fmt(d.steam_t, 0)} t</span>
        <span><span className="mr-1.5 inline-block h-2 w-3 rounded-sm" style={{ background: C.s3 }} />Soak {fmt(d.soak_days, 1)} d</span>
        <span><span className="mr-1.5 inline-block h-2 w-3 rounded-sm" style={{ background: "#1d4f8f" }} />Production {fmt(d.prod_days, 0)} d</span>
        {s.phase === "production" && <span>Planned re-steam in <b className="mono text-ink">{fmt(s.planned_days_left, 0)}</b> d{optimalIn !== null && optimalIn !== undefined ? <> · <span className="text-s7">◆</span> economic optimum in <b className="mono text-s7">{optimalIn}</b> d</> : null}</span>}
      </div>
    </div>
  );
}

function RateBar({ label, value, max, color, note }: { label: string; value: number; max: number; color: string; note?: string }) {
  return (
    <div className="mb-2.5">
      <div className="mb-1 flex justify-between text-xs"><span className="text-ink-2">{label}</span><span className="mono text-ink">{fmt(value, 1)} m³/d</span></div>
      <div className="h-2.5 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.min((value / max) * 100, 100)}%`, background: color }} /></div>
      {note && <div className="mt-1 text-[11px] text-ink-3">{note}</div>}
    </div>
  );
}

export default function WellTwin({ id }: { id: string }) {
  const { snap } = useLive();
  const confirm = useConfirm();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("overview");
  const detail = useFetch(() => get<WellDetail>(`/api/wells/${id}`), [id], 3000);
  const hist = useFetch(() => get<HistoryPoint[]>(`/api/wells/${id}/history?n=2400&max_points=500`), [id], 10000);
  const summary = snap?.wells.find((w) => w.id === id);
  const cardVersion = summary?.card_version ?? 0;
  const card = useFetch(() => get<CardResp>(`/api/wells/${id}/card`), [id, cardVersion]);
  const forecast = useFetch(() => get<ForecastResp>(`/api/wells/${id}/forecast?horizon=120`), [id], 20000);
  const risk = useFetch(() => get<RiskResp>(`/api/wells/${id}/risk`), [id], 6000);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const dateOf = useMemo(() => {
    const now = snap ? new Date(snap.sim_time).getTime() : Date.now();
    const day = snap?.sim_day ?? 0;
    return (t: number) => new Date(now - (day - t) * 86400000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  }, [snap?.sim_time, snap?.sim_day]);

  const rows = useMemo(() => (hist.data ?? []).map((h) => {
    const prod = h.phase === "production";
    return { t: h.t, oil: h.m.oil_rate_m3d, temp: h.tw.t_avg, mu: h.tw.mu_cp, fill: prod ? h.m.fillage * 100 : null, fill_tw: prod ? h.tw.fillage * 100 : null,
      pprl: prod ? h.m.pprl_kn : null, mprl: prod ? h.m.mprl_kn : null, pprl_tw: prod ? h.tw.pprl_kn : null, mprl_tw: prod ? h.tw.mprl_kn : null };
  }), [hist.data]);
  const trend = (key: "oil" | "temp" | "mu" | "fill") => rows.slice(-90).map((r) => r[key]);

  if (!summary) return <Loading />;
  const d = detail.data;
  const tw = d?.twin_now ?? {};
  const down = summary.status === "down";
  const producing = summary.phase === "production" && !down;
  const topRec = d?.recommendations.slice().sort((a, b) => b.gain_inr_per_day - a.gain_inr_per_day)[0];

  const toggleVfd = async () => {
    if (!d) return;
    const enable = !d.vfd.enabled;
    const ok = await confirm(enable
      ? { title: `Enable closed-loop VFD on ${id}?`, confirmLabel: "Enable", body: <>The controller adjusts speed (at most 0.25 SPM per step) to hold pump fillage near {Math.round(d.vfd.target_fillage * 100)}%, inside the rod-load, gearbox and rod-fall limits, and idles the unit when the annulus is pumped off. This is recorded as approved by <b>{engineerName()}</b>.</>}
      : { title: `Return ${id} to fixed speed?`, confirmLabel: "Disable", body: "The well goes back to its scheduled SPM." });
    if (!ok) return;
    try { setActionErr(null); await post(`/api/wells/${id}/vfd`, { enabled: enable, by: engineerName() }); detail.reload(); toast({ title: enable ? "Closed-loop VFD enabled" : "Fixed speed restored", body: id }); } catch (e) { setActionErr((e as Error).message); }
  };
  const inject = async (kind: string) => {
    if (!kind) return;
    const ok = await confirm({ title: `Inject "${kind.replace("_", " ")}" into ${id}?`, confirmLabel: "Inject", tone: "danger",
      body: "Demonstration only. The fault is applied to the synthetic field; the twin is not told and has to detect it from the data." });
    if (!ok) return;
    await post(`/api/wells/${id}/fault`, { kind, by: engineerName() });
    toast({ title: kind === "clear" ? "Faults cleared" : "Fault injected", body: "Watch the alerts for detection.", tone: "info" });
  };

  const c = card.data?.card;
  const fc = forecast.data;
  const fcRows = fc?.available ? (() => {
    const map = new Map<number, Record<string, number | null>>();
    (fc.history ?? []).forEach((h) => map.set(h.day, { day: h.day, measured: h.q_oil }));
    (fc.ml ?? []).forEach((m) => map.set(m.day, { ...(map.get(m.day) ?? { day: m.day }), p50: m.p50, lo: m.p10, span: m.p90 - m.p10 }));
    (fc.physics ?? []).forEach((p) => map.set(p.day, { ...(map.get(p.day) ?? { day: p.day }), physics: p.q_oil }));
    return [...map.values()].sort((a, b) => Number(a.day) - Number(b.day));
  })() : [];

  const deliv = Number(tw.q_liq_deliv ?? 0), cap = Number(tw.cap ?? 0), got = Number(tw.q_liq ?? 0);
  const maxBar = Math.max(deliv, cap, got, 1) * 1.05;
  const limiting = String(tw.limiting ?? "");
  const verdict = !producing ? null
    : limiting === "reservoir inflow"
      ? `The reservoir is the bottleneck: the heated zone (${fmt(summary.t_avg, 0)} °C, ${fmt(summary.mu_cp, 0)} cP) delivers ${fmt(deliv, 1)} m³/d but the pump could lift ${fmt(cap, 1)} m³/d, so the barrel fills only ${pct(summary.fillage)} and the plunger pounds fluid. Slower pumping saves the rods without losing oil.`
      : limiting.includes("flash") ? `The pump is the bottleneck: fluid at ${fmt(summary.t_avg, 0)} °C flashes to steam at the pump intake, gas-locking the barrel. More submergence (a deeper pump or a higher fluid level) would let the well deliver up to ${fmt(deliv, 1)} m³/d.`
      : limiting.includes("viscous") ? `The pump is the bottleneck: viscous oil (${fmt(summary.mu_cp, 0)} cP) cannot fill the barrel at this speed. The reservoir could deliver ${fmt(deliv, 1)} m³/d.`
      : `The pump is the bottleneck (${limiting || "capacity"}): the reservoir can deliver ${fmt(deliv, 1)} m³/d but only ${fmt(got, 1)} m³/d is lifted, so faster pumping would raise oil.`;

  return (
    <div className="space-y-5">
      {/* header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative">
            <select value={id} onChange={(e) => go({ page: "well", id: e.target.value })} aria-label="Select well"
              className="h-11 appearance-none rounded-xl py-0 pl-4 pr-9 text-xl font-semibold tracking-tight">
              {WELLS.map((w) => <option key={w}>{w}</option>)}
            </select>
            <span aria-hidden className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-3">▾</span>
          </div>
          <PhaseBadge phase={summary.phase} tAvg={summary.t_avg} />
          <span className="mono text-xs text-ink-3">cycle {summary.cycle_no}</span>
          <WellStatus status={summary.status} />
          {d?.vfd.enabled && <Pill tone="blue">Closed-loop VFD active</Pill>}
          {d?.spm_override !== null && d?.spm_override !== undefined && <Pill>SPM set to {fmt(d.spm_override, 1)} by engineer</Pill>}
          {d?.next_design && <Pill tone="accent">Optimised design queued</Pill>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant={d?.vfd.enabled ? "ghost" : "primary"} onClick={toggleVfd} disabled={!d || (!producing && !d.vfd.enabled)}>
            <Cpu size={15} /> {d?.vfd.enabled ? "Disable closed-loop VFD" : "Enable closed-loop VFD"}
          </Button>
          <Button variant="ghost" onClick={() => go({ page: "optimize", id })}><Gauge size={15} /> Optimise</Button>
          <Button variant="ghost" onClick={() => go({ page: "optimize", id, tab: "whatif" })}><Sliders size={15} /> What-if</Button>
          {!DEMO && <select defaultValue="" onChange={(e) => { inject(e.target.value); e.target.value = ""; }} className="h-9 px-2 text-xs text-ink-3" aria-label="Inject a demonstration fault">
            <option value="">Demo: inject fault…</option><option value="rod_parted">Parted rods</option><option value="tv_leak">Pump (TV) leak</option><option value="clear">Clear faults</option>
          </select>}
        </div>
      </div>
      {actionErr && <ErrorBox error={actionErr} />}
      {d && <Panel bodyClass="py-4"><CycleTimeline s={summary} d={d.design} optimalIn={d.projection.optimal_resteam_in_days} /></Panel>}

      {/* KPI row (Build Bible dashboard) */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Oil rate" value={fmt(summary.oil, 2)} unit="m³/d" sub={<>{fmt(summary.oil * 6.2898, 1)} bbl/d · twin {fmt(Number(tw.q_oil ?? 0), 2)}</>} accent={C.s1} spark={trend("oil")} />
        <Stat label="Heated zone" value={fmt(summary.t_avg, 0)} unit="°C" sub={`radius ${fmt(summary.r_h, 1)} m`} accent={C.s2} spark={trend("temp")} />
        <Stat label="Viscosity" value={fmt(summary.mu_cp, 0)} unit="cP" sub={<DataLabel kind="MODELLED" />} accent={C.s4} spark={trend("mu")} />
        <Stat label="Cycle SOR" value={fmt(summary.sor_cycle, 2)} unit="t/m³" sub="projected, steam ÷ oil" />
        <Stat label="Pump fillage" value={producing ? pct(summary.fillage) : "–"} sub={producing ? `${fmt(summary.spm, 1)} SPM` : down ? "well down" : "not pumping"} accent={C.s1} spark={trend("fill")} />
        <Stat label="Energy" value={fmt(summary.energy_bbl, 0)} unit="kWh/bbl" sub="projected cycle, incl. steam" />
        <Stat label="Rod-pump risk" value={risk.data?.available && !down ? pct(risk.data.risk_score) : "–"} sub={down ? "well down" : risk.data?.available ? (RISK_TEXT[risk.data.label ?? ""] ?? risk.data.label) : "model-estimated"} />
        <Stat label="Twin confidence" value={pct(d?.calibration.confidence)} sub={d?.calibration.mape !== undefined ? `oil error ${pct(d.calibration.mape, 1)}` : "calibrating"} />
      </div>

      <Tabs<Tab> label="Well views" value={tab} onChange={setTab} tabs={[
        { key: "overview", label: "Overview", hint: "twin, bottleneck, next action", count: d?.alerts.length },
        { key: "predict", label: "Predict & health", hint: "forecast, calibration, failures" },
        { key: "trends", label: "Trends & diagnostics", hint: "thermal, lift, dynamometer card" },
      ]} />

      {tab === "overview" && (
        <div className="space-y-5">
          <div className="grid gap-5 xl:grid-cols-5">
            <Panel title="Well-to-surface twin" subtitle="Live state: pumpjack, wellbore, heated zone" className="flex flex-col xl:col-span-3" bodyClass="p-0 relative min-h-[560px] flex-1"
              right={<><DataLabel kind="SYNTHETIC" /><DataLabel kind="MODELLED" /></>}>
              <div className="absolute inset-0 overflow-hidden rounded-b-[12px]">
                <Suspense fallback={<Skeleton className="h-full w-full rounded-none" />}>
                  <WellScene3D phase={summary.phase} spm={summary.spm} stroke={d?.design.stroke_m ?? 2.54} rh={summary.r_h} tAvg={summary.t_avg} mu={summary.mu_cp}
                    pumpDepth={d?.design.pump_depth_m ?? 1000} depth={d?.twin_params.depth_m ?? 1150} fillage={producing ? summary.fillage : 0} steamRate={summary.steam_rate} running={!down} />
                </Suspense>
                <div className="pointer-events-none absolute left-3 top-3 space-y-1.5 text-[11px]">
                  {[
                    ["Surface", producing ? `Pumpjack ${fmt(summary.spm, 1)} SPM · stroke ${fmt((d?.design.stroke_m ?? 2.54) / 0.0254, 0)} in` : summary.phase === "injection" ? `Injecting ${fmt(summary.steam_rate, 0)} t/d steam` : "Shut in (soak)"],
                    ["Pump", `${fmt(d?.design.pump_depth_m, 0)} m${producing ? ` · barrel ${pct(summary.fillage)} full` : ""}`],
                    ["Heated zone", `r = ${fmt(summary.r_h, 1)} m · ${fmt(summary.t_avg, 0)} °C · ${fmt(summary.mu_cp, 0)} cP`],
                    ["Reservoir", `${fmt(d?.twin_params.depth_m, 0)} m · Jodhpur Sandstone`],
                  ].map(([k, v]) => <div key={k} className="rounded-md bg-black/55 px-2.5 py-1 text-ink-2 backdrop-blur-sm"><span className="eyebrow mr-2 !text-[10px]">{k}</span><span className="mono text-ink">{v}</span></div>)}
                </div>
                <div className="pointer-events-none absolute bottom-2 left-3 text-[10px] text-ink-3">Depth compressed · drag to orbit, scroll to zoom</div>
              </div>
            </Panel>
            <div className="space-y-5 xl:col-span-2">
              <Panel title="Why production is what it is" subtitle="Reservoir deliverability vs pump capacity: the smaller one sets the rate">
                {down ? (
                  <p className="text-sm leading-relaxed text-ink-2">The well is down: the twin and the downhole card indicate mechanical failure, so nothing is being lifted although the reservoir could still deliver about {fmt(deliv, 1)} m³/d of liquid ({fmt(summary.t_avg, 0)} °C, {fmt(summary.mu_cp, 0)} cP). Every day down defers oil; approve the workover below.</p>
                ) : producing ? (
                  <>
                    <RateBar label="Reservoir can deliver (liquid)" value={deliv} max={maxBar} color={C.s2} />
                    <RateBar label="Pump can lift" value={cap} max={maxBar} color={C.s3} />
                    <RateBar label="Actually produced" value={got} max={maxBar} color={C.s1} note={`limiting factor: ${limiting || "–"}`} />
                    <p className="mt-3 text-sm leading-relaxed text-ink-2">{verdict}</p>
                  </>
                ) : summary.phase === "injection" ? (
                  <p className="text-sm leading-relaxed text-ink-2">Injecting {fmt(summary.steam_rate, 0)} t/d. Wellbore heat loss ({fmt(Number(tw.heat_loss_mw ?? 0), 2)} MW) lets {pct(Number(tw.x_bh ?? 0))} steam quality reach the sand face. The heated zone has grown to {fmt(summary.r_h, 1)} m at {fmt(summary.t_avg, 0)} °C, so oil viscosity has dropped to {fmt(summary.mu_cp, 0)} cP (about 14,000 cP cold).</p>
                ) : (
                  <p className="text-sm leading-relaxed text-ink-2">Shut in so the heat spreads into the rock. The zone is at {fmt(summary.t_avg, 0)} °C ({fmt(summary.mu_cp, 0)} cP) and slowly cooling; production starts after the soak.</p>
                )}
              </Panel>
              <Panel title="Recommended next action" subtitle="The twin proposes; an engineer approves" bodyClass="pt-2">
                {topRec ? <RecCard rec={topRec} onDone={detail.reload} /> : (
                  <p className="text-sm text-ink-2">No action recommended: current settings are within the twin's limits. <button className="text-accent-2 hover:underline" onClick={() => go({ page: "optimize", id })}>Search for a better CSS + SRP plan →</button></p>
                )}
                {d && d.recommendations.length > 1 && <p className="mt-2 text-xs text-ink-3">{d.recommendations.length - 1} more pending for this well on the Actions page.</p>}
              </Panel>
            </div>
          </div>
          {d && d.alerts.length > 0 && (
            <Panel title={`Alerts for ${id}`} subtitle="Each with its root cause and a suggested fix">
              <div className="grid gap-3 xl:grid-cols-2">{d.alerts.map((a) => <AlertItem key={a.id} alert={a} onAck={detail.reload} />)}</div>
            </Panel>
          )}
        </div>
      )}

      {tab === "predict" && (
        <div className="grid gap-5 xl:grid-cols-5">
          <Panel title="Production forecast for the rest of the cycle" className="xl:col-span-3"
            subtitle={fc?.available ? "Measured daily oil, XGBoost P10-P90 band and median, and the physics twin's projection" : fc?.reason}
            right={fc?.available && <Legend items={[{ label: "Measured", color: C.s1 }, { label: "ML P50", color: C.s2 }, { label: "Physics twin", color: C.s3, dashed: true }]} />}>
            {fc?.available ? (
              <div className="h-80">
                <ResponsiveContainer>
                  <ComposedChart data={fcRows} margin={{ top: 8, right: 12, bottom: 0, left: -6 }}>
                    <CartesianGrid vertical={false} strokeDasharray="2 4" />
                    <XAxis dataKey="day" type="number" domain={["dataMin", "dataMax"]} {...axisProps} tickFormatter={(v) => `d${v}`} />
                    <YAxis {...axisProps} width={44} />
                    <Tooltip cursor={{ stroke: C.ink3, strokeDasharray: "3 3" }} content={<ChartTip labelFmt={(l) => `Production day ${l}`} hide={["P10", "P10–P90"]} valueFmt={(v) => `${fmt(v, 2)} m³/d`} />} />
                    <Area dataKey="lo" stackId="band" stroke="none" fill="transparent" name="P10" isAnimationActive={false} />
                    <Area dataKey="span" stackId="band" stroke="none" fill={C.s2} fillOpacity={0.18} name="P10–P90" isAnimationActive={false} />
                    <Line dataKey="measured" name="Measured" stroke={C.s1} strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line dataKey="p50" name="ML P50" stroke={C.s2} strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line dataKey="physics" name="Physics twin" stroke={C.s3} strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
                    {fc.today_prod_day !== undefined && <ReferenceLine x={fc.today_prod_day} stroke={C.ink3} label={{ value: "today", fill: C.ink3, fontSize: 10, position: "insideTopLeft" }} />}
                    {fc.optimal_resteam_in_days !== null && fc.optimal_resteam_in_days !== undefined && fc.today_prod_day !== undefined && (
                      <ReferenceLine x={fc.today_prod_day + fc.optimal_resteam_in_days} stroke={C.s7} strokeDasharray="4 3" label={{ value: "economic re-steam", fill: C.s7, fontSize: 10, position: "insideTopRight" }} />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            ) : <EmptyState title="Forecast not available yet">{fc?.reason ?? "Loading…"}</EmptyState>}
            {down && <p className="mt-2 text-xs text-warn">The well is down: the physics projection shows what production would be after a repair.</p>}
          </Panel>
          <div className="space-y-5 xl:col-span-2">
            <Panel title="Twin calibration" subtitle="History matching keeps the twin in sync with the well" right={<HeartPulse size={16} className="text-ink-3" />}>
              {d ? (
                <>
                  <div className="mb-2 flex items-center gap-3">
                    <div className="h-2 flex-1 rounded-full bg-surface-3"><div className="h-full rounded-full bg-accent" style={{ width: `${(d.calibration.confidence ?? 0) * 100}%` }} /></div>
                    <span className="mono text-sm font-medium">{pct(d.calibration.confidence)}</span>
                  </div>
                  <KV k="Oil-rate error, last 14 d" v={d.calibration.mape !== undefined ? `${pct(d.calibration.mape_before, 1)} → ${pct(d.calibration.mape, 1)}` : "–"} />
                  <KV k="Productivity × / heat loss × / cooling ×" v={`${fmt(d.calibration.params?.pi_mult, 2)} / ${fmt(d.calibration.params?.u_mult, 2)} / ${fmt(d.calibration.params?.cool_mult, 2)}`} />
                  <div className="mt-3"><Button small variant="ghost" onClick={async () => { try { await post(`/api/wells/${id}/calibrate`); detail.reload(); toast({ title: "Twin recalibrated", body: id }); } catch (e) { setActionErr((e as Error).message); } }}>Recalibrate now</Button></div>
                </>
              ) : <Loading />}
            </Panel>
            <Panel title="Equipment health" subtitle="Failure prediction" right={<Wrench size={16} className="text-ink-3" />}>
              {d ? (
                <>
                  <KV k="Rod fatigue used (Miner)" v={pct(d.health.rod_damage, 1)} />
                  <KV k="Rod failure predicted in" v={d.health.rod_rul_days === null || d.health.rod_rul_days === undefined ? "–" : d.health.rod_rul_days > 3650 ? "> 10 years" : `${fmt(d.health.rod_rul_days, 0)} days`} />
                  <KV k="Pump load retention" v={fmt(d.health.pump_retention, 2)} unit="(worn < 0.62)" />
                  <KV k="Pump failure predicted in" v={d.health.pump_rul_days === null || d.health.pump_rul_days === undefined ? "no downward trend" : `${fmt(d.health.pump_rul_days, 0)} days`} />
                  <KV k="Anomaly score" v={`${fmt(d.anomaly.score, 2)}${d.anomaly.anomalous ? " (anomalous)" : ""}`} unit="(1 = threshold)" />
                  {risk.data?.available && risk.data.factors && (
                    <details className="mt-2 text-xs text-ink-2">
                      <summary className="cursor-pointer text-ink-2">Why the risk model says "{RISK_TEXT[risk.data.focus_class ?? ""] ?? risk.data.focus_class}"</summary>
                      <ul className="mt-1 list-disc space-y-0.5 pl-5">{risk.data.factors.map((f) => <li key={f.feature}>{f.text} = {fmt(f.value, 2)} {f.direction} the estimate</li>)}</ul>
                      {risk.data.ood?.flag && <p className="mt-1 text-warn">OUT-OF-DOMAIN: treat as indicative only.</p>}
                    </details>
                  )}
                </>
              ) : <Loading />}
            </Panel>
          </div>
        </div>
      )}

      {tab === "trends" && (
        <div className="space-y-5">
          <div className="grid gap-5 md:grid-cols-2">
            <Panel title="Heated-zone temperature" subtitle="°C, injection heats and cooldown decays"><TimeChart data={rows} dateOf={dateOf} unit="°C" digits={0} series={[{ key: "temp", name: "Temperature", color: C.s2 }]} height={190} /></Panel>
            <Panel title="Oil viscosity" subtitle="cP, rises as the zone cools"><TimeChart data={rows} dateOf={dateOf} unit="cP" digits={0} series={[{ key: "mu", name: "Viscosity", color: C.s4 }]} height={190} /></Panel>
            <Panel title="Pump fillage" subtitle="%, measured vs twin"><TimeChart data={rows} dateOf={dateOf} unit="%" digits={0} yDomain={[0, 100]} refY={{ y: 85, label: "VFD target" }} series={[{ key: "fill", name: "Measured", color: C.s1 }, { key: "fill_tw", name: "Twin", color: C.s2, dashed: true }]} height={190} /></Panel>
            <Panel title="Polished-rod loads" subtitle="kN, peak and minimum"><TimeChart data={rows} dateOf={dateOf} unit="kN" digits={1} series={[{ key: "pprl", name: "PPRL", color: C.s1 }, { key: "mprl", name: "MPRL", color: C.s3 }, { key: "pprl_tw", name: "PPRL twin", color: C.s1, dashed: true }, { key: "mprl_tw", name: "MPRL twin", color: C.s3, dashed: true }]} height={190} showLegend={false} /></Panel>
          </div>
          <Panel title="Dynamometer card" subtitle={c ? `${fmt(c.spm, 1)} SPM · ${fmt(c.stroke / 0.0254, 0)} in stroke · surface card converted to a pump card with the Gibbs wave-equation method` : "Cards are recorded every 6 simulated hours while pumping"}>
            {c ? (
              <div className="grid gap-6 lg:grid-cols-3">
                <CardPlot title="Surface card (load cell)" pos={c.surface.pos} load={c.surface.load_kn} color={C.s1} />
                <CardPlot title="Downhole pump card (computed)" pos={c.downhole.pos} load={c.downhole.load_kn} color={C.s2} xMax={Math.max(...c.surface.pos)} refLines={[{ y: c.fo_kn, label: "Fo (fluid load)" }, { y: 0, label: "0" }]} />
                <div>
                  <div className="eyebrow mb-1">CNN diagnosis</div>
                  <div className="mb-3 text-lg font-semibold">{c.label} <span className="mono text-sm font-normal text-ink-3">{pct(c.confidence)}</span></div>
                  <div className="space-y-2">
                    {Object.entries(c.probabilities).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, p]) => (
                      <div key={k} className="flex items-center gap-2 text-xs">
                        <span className="w-44 truncate text-ink-2">{card.data?.labels[k] ?? k}</span>
                        <div className="h-1.5 flex-1 rounded-full bg-surface-3"><div className="h-full rounded-full bg-accent" style={{ width: `${p * 100}%` }} /></div>
                        <span className="mono w-10 text-right text-ink">{pct(p)}</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-x-5">
                    <KV k="Net plunger stroke" v={fmt(c.metrics.net_stroke_m, 2)} unit="m" />
                    <KV k="Fillage (card)" v={pct(c.metrics.fillage_est)} />
                    <KV k="Load retention" v={fmt(c.metrics.upstroke_load_retention, 2)} />
                    <KV k="Peak torque" v={fmt(c.peak_torque_knm, 1)} unit="kN·m" />
                  </div>
                </div>
              </div>
            ) : <EmptyState title="No card yet">The well is not pumping.</EmptyState>}
          </Panel>
        </div>
      )}
      <p className="pb-2 text-center text-[11px] text-ink-3">Net value today {inr(summary.cash_inr_d)}/day · cumulative oil {fmt(summary.cum_oil, 0)} m³ · all values synthetic</p>
    </div>
  );
}
