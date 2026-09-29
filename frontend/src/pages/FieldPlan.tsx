import { useEffect, useRef, useState } from "react";
import { CheckCircle2, MinusCircle, Play, Send } from "lucide-react";
import { engineerName, get, post } from "../api";
import { Button, C, ErrorBox, Panel, Stat, fmt, inr, pct } from "../components/ui";
import { go } from "../router";

interface Metrics { oil_bopd: number; sor: number; energy_per_bbl_kwh: number; failure_prob: number; avg_fillage: number; npv_per_day: number }
interface Row {
  well_id: string; base_steam_t: number; plan_steam_t: number; feasible: boolean; base: Metrics; plan: Metrics | null;
  gain_inr_per_year: number; chosen: boolean; reason: string; choice: "baseline" | "lean" | "full";
}
interface Plan {
  status: "idle" | "running" | "done" | "error"; progress: number; current?: string; error?: string | null; results: Row[];
  skipped?: { well_id: string; reason: string }[]; budget_t?: number; budget_pct?: number;
  allocation?: { total_steam_t: number; base_steam_t: number; total_gain_inr_per_year: number; min_steam_t: number; note: string | null };
}

export default function FieldPlan() {
  const [plan, setPlan] = useState<Plan>({ status: "idle", progress: 0, results: [] });
  const [budget, setBudget] = useState(100);
  const [err, setErr] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<number | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const realloc = useRef<number | undefined>(undefined);

  const poll = () => get<Plan>("/api/field/plan").then(setPlan).catch((e) => setErr(e.message));
  useEffect(() => {
    poll(); // pick up a plan that is already running or finished
    return () => { window.clearInterval(timer.current); window.clearTimeout(realloc.current); };
  }, []);
  useEffect(() => {
    window.clearInterval(timer.current);
    if (plan.status === "running") timer.current = window.setInterval(poll, 1500);
    if (plan.status === "done" && plan.budget_pct !== undefined) setBudget(plan.budget_pct);
  }, [plan.status]);

  const start = async () => {
    setErr(null); setSubmitted(null);
    try { await post("/api/field/plan", { budget_pct: budget }); setPlan({ status: "running", progress: 0, results: [] }); } catch (e) { setErr((e as Error).message); }
  };
  const changeBudget = (v: number) => {
    setBudget(v);
    if (plan.status !== "done") return;
    window.clearTimeout(realloc.current);
    realloc.current = window.setTimeout(async () => { try { setPlan(await post<Plan>("/api/field/plan/reallocate", { budget_pct: v })); } catch (e) { setErr((e as Error).message); } }, 250);
  };
  const submit = async () => {
    try { const r = await post<{ submitted: number }>("/api/field/plan/submit", { by: engineerName() }); setSubmitted(r.submitted); } catch (e) { setErr((e as Error).message); }
  };

  const done = plan.status === "done";
  const chosen = plan.results.filter((r) => r.chosen);
  const lean = chosen.filter((r) => r.choice === "lean").length;
  const maxGain = Math.max(...plan.results.map((r) => Math.abs(r.gain_inr_per_year)), 1);
  const a = plan.allocation;

  return (
    <div className="space-y-4">
      <p className="max-w-4xl text-sm text-ink-3">
        Steam is shared and limited, so the best plan for one well is not always the best for the field. This optimises every well's coupled CSS + SRP design (constraint-based search,
        each candidate verified with full physics), then decides which wells adopt their plan when the total steam is capped. Extra steam goes first to the wells that earn the most per tonne.
      </p>
      {err && <ErrorBox error={err} />}
      <Panel title="Steam budget for the next cycles" subtitle="As a share of the steam the wells' current plans would use. Move it after a run to re-allocate instantly.">
        <div className="flex flex-wrap items-center gap-4">
          <div className="min-w-[260px] flex-1">
            <div className="flex justify-between text-xs"><span className="text-ink-3">Budget</span><span className="num font-semibold text-ink">{budget}% {a && plan.budget_t ? `· ${fmt(plan.budget_t, 0)} t` : ""}</span></div>
            <input type="range" min={60} max={120} step={1} value={budget} onChange={(e) => changeBudget(Number(e.target.value))} className="mt-1 w-full" aria-label="Steam budget percent" />
          </div>
          <Button onClick={start} disabled={plan.status === "running"}><Play size={15} /> {plan.status === "running" ? "Optimising wells…" : done ? "Re-run for all wells" : "Optimise all wells"}</Button>
          {done && <Button variant="good" onClick={submit} disabled={!chosen.length || submitted !== null}><Send size={14} /> Submit {chosen.length} plan(s) for approval</Button>}
        </div>
        {plan.status === "running" && (
          <div className="mt-3">
            <div className="h-2 rounded-full bg-surface-3"><div className="h-full rounded-full bg-s1 transition-all" style={{ width: `${plan.progress * 100}%` }} /></div>
            <div className="mt-1 text-xs text-ink-3">Optimising {plan.current ?? "…"} · {pct(plan.progress, 0)} (about 5 s per well)</div>
          </div>
        )}
        {submitted !== null && <p className="mt-3 text-sm text-ink-2">Submitted {submitted} plan(s). An engineer approves each one on the <button className="text-s1 hover:underline" onClick={() => go({ page: "actions" })}>Actions</button> page.</p>}
        {plan.status === "error" && <div className="mt-3"><ErrorBox error={plan.error ?? "failed"} /></div>}
      </Panel>

      {done && a && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Extra net value from adopted plans" value={inr(a.total_gain_inr_per_year)} unit="/year" accent={C.s3} sub="simulated, synthetic economics" />
            <Stat label="Steam used" value={fmt(a.total_steam_t, 0)} unit="t" sub={`${fmt(a.base_steam_t, 0)} t today's plans · budget ${fmt(plan.budget_t, 0)} t`} accent={C.s2} />
            <Stat label="Wells adopting a new plan" value={`${chosen.length} / ${plan.results.length}`} sub={`${lean} steam-lean · ${chosen.length - lean} full${plan.skipped?.length ? ` · ${plan.skipped.length} skipped (down)` : ""}`} />
            <Stat label="Steam vs today's plans" value={`${a.total_steam_t - a.base_steam_t >= 0 ? "+" : "−"}${fmt(Math.abs(a.total_steam_t - a.base_steam_t), 0)}`} unit="t" sub={a.total_steam_t < a.base_steam_t ? "less steam, more value" : "extra steam placed where it pays most"} />
          </div>
          {a.note && <ErrorBox error={a.note} />}
          <Panel title="Plan by well" subtitle="Current plan → chosen plan. Each well picks one of: keep current, steam-lean plan, or full plan, so the field earns the most within the steam budget." bodyClass="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-ink-3"><tr className="border-b border-line">{["Well", "Oil BOPD", "SOR t/m³", "Energy kWh/bbl", "Failure risk", "Steam t", "Net value gain / year", "Decision"].map((h) => <th key={h} className="whitespace-nowrap px-4 py-2 font-medium">{h}</th>)}</tr></thead>
                <tbody>
                  {[...plan.results].sort((x, y) => y.gain_inr_per_year - x.gain_inr_per_year).map((r) => (
                    <tr key={r.well_id} className="cursor-pointer border-b border-line/60 hover:bg-surface-2" onClick={() => go({ page: "optimize", id: r.well_id })}>
                      <td className="px-4 py-2 font-semibold">{r.well_id}</td>
                      <td className="num px-4 py-2">{fmt(r.base.oil_bopd, 1)} → <b>{r.plan ? fmt(r.plan.oil_bopd, 1) : "–"}</b></td>
                      <td className="num px-4 py-2">{fmt(r.base.sor, 2)} → <b>{r.plan ? fmt(r.plan.sor, 2) : "–"}</b></td>
                      <td className="num px-4 py-2">{fmt(r.base.energy_per_bbl_kwh, 0)} → <b>{r.plan ? fmt(r.plan.energy_per_bbl_kwh, 0) : "–"}</b></td>
                      <td className="num px-4 py-2">{pct(r.base.failure_prob, 0)} → <b>{r.plan ? pct(r.plan.failure_prob, 0) : "–"}</b></td>
                      <td className="num px-4 py-2">{fmt(r.base_steam_t, 0)} → <b>{fmt(r.plan_steam_t, 0)}</b></td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2"><div className="h-2 w-24 rounded-full bg-surface-3"><div className="h-full rounded-full" style={{ width: `${(Math.max(r.gain_inr_per_year, 0) / maxGain) * 100}%`, background: r.chosen ? C.s3 : C.ink3 }} /></div><span className="num text-xs">{inr(r.gain_inr_per_year)}</span></div>
                      </td>
                      <td className="px-4 py-2 text-xs">
                        {r.chosen ? <span className="inline-flex items-center gap-1 text-ink"><CheckCircle2 size={13} color={C.good} /> {r.reason}</span> : <span className="inline-flex items-center gap-1 text-ink-3"><MinusCircle size={13} /> {r.reason}</span>}
                      </td>
                    </tr>
                  ))}
                  {(plan.skipped ?? []).map((s) => <tr key={s.well_id} className="border-b border-line/60 text-ink-3"><td className="px-4 py-2 font-semibold">{s.well_id}</td><td className="px-4 py-2 text-xs" colSpan={7}>{s.reason}</td></tr>)}
                </tbody>
              </table>
            </div>
          </Panel>
        </>
      )}
      {plan.status === "idle" && <Panel><p className="text-sm text-ink-3">Set a steam budget and run the field optimiser. It takes about a minute for ten wells; you can keep using the app meanwhile.</p></Panel>}
    </div>
  );
}
