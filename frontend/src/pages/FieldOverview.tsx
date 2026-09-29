import { CalendarClock } from "lucide-react";
import { useFetch, useLive } from "../live";
import { get } from "../api";
import FieldMap from "../components/FieldMap";
import SteamCalendar, { type SteamEvent } from "../components/SteamCalendar";
import { C, Panel, PhaseBadge, Stat, WellStatus, fmt, inr, pct } from "../components/ui";
import { go } from "../router";
import type { Alert, Recommendation } from "../types";

interface ScheduleResp {
  schedule: { events: SteamEvent[]; utilization: Record<string, number>; horizon_days: number; unscheduled: string[] };
  generators: { id: string; capacity_tpd: number }[];
  sim_time: string;
}

export default function FieldOverview() {
  const { snap } = useLive();
  const sched = useFetch(() => get<ScheduleResp>("/api/steam-schedule"), [], 8000);
  const alerts = useFetch(() => get<Alert[]>("/api/alerts?status=active"), [], 5000);
  const recs = useFetch(() => get<Recommendation[]>("/api/recommendations?status=pending"), [], 5000);
  if (!snap) return null;
  const k = snap.kpis;
  const wells = snap.wells;

  // one line per well that needs attention: worst open alert + best pending action
  const sev = { critical: 0, warning: 1, info: 2 } as const;
  const priority = wells.map((w) => {
    const a = (alerts.data ?? []).filter((x) => x.well_id === w.id).sort((p, q) => sev[p.severity] - sev[q.severity])[0];
    const r = (recs.data ?? []).filter((x) => x.well_id === w.id).sort((p, q) => q.gain_inr_per_day - p.gain_inr_per_day)[0];
    return { w, a, r };
  }).filter((x) => (x.a && x.a.severity !== "info") || x.r)
    .sort((p, q) => (p.a ? sev[p.a.severity] : 3) - (q.a ? sev[q.a.severity] : 3) || (q.r?.gain_inr_per_day ?? 0) - (p.r?.gain_inr_per_day ?? 0)).slice(0, 7);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Field oil" value={fmt(k.oil_m3d, 1)} unit="m³/d" sub={`${fmt(k.oil_bpd, 0)} bbl/d · ${k.producing} producing`} accent={C.s1} />
        <Stat label="Steam injection" value={fmt(k.steam_tpd, 0)} unit="t/d" sub={`${k.injecting} injecting · ${k.soaking} soaking`} accent={C.s2} />
        <Stat label="Projected cycle SOR" value={fmt(k.sor, 2)} unit="t/m³" sub="steam ÷ expected cycle oil" />
        <Stat label="Oil revenue" value={inr(k.revenue_inr_d)} unit="/day" sub="before steam, power, water costs" />
        <Stat label="Average pump fillage" value={pct(k.avg_fillage)} sub={k.avg_fillage < 0.6 ? "below 60%: fluid pound across the field" : "healthy"} />
      </div>

      <div className="grid gap-4 xl:grid-cols-5">
        <Panel title="Field map" subtitle="Coloured by CSS phase; glow shows heated-zone temperature" className="xl:col-span-3"><FieldMap wells={wells} generators={k.generators} /></Panel>
        <Panel title="Where to look first" subtitle="Open alerts and the best pending action per well" className="xl:col-span-2" bodyClass="p-0">
          {priority.length ? (
            <ul className="divide-y divide-line">
              {priority.map(({ w, a, r }) => (
                <li key={w.id}>
                  <button className="block w-full px-4 py-2.5 text-left hover:bg-surface-2" onClick={() => go({ page: "well", id: w.id })}>
                    <div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold">{w.id}</span><WellStatus status={w.status} /></div>
                    {a && <div className="mt-0.5 text-xs text-ink-2">{a.title}</div>}
                    {r && <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-ink-3"><span className="truncate">Action: {r.title}</span><span className="num shrink-0 text-ink-2">+{inr(r.gain_inr_per_day)}/day</span></div>}
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="p-4 text-sm text-ink-3">All wells are within limits.</p>}
          <div className="border-t border-line px-4 py-2 text-xs text-ink-3">{k.pending_recommendations} action(s) awaiting engineer approval · <button className="text-s1 hover:underline" onClick={() => go({ page: "actions" })}>open Actions</button></div>
        </Panel>
      </div>

      <Panel title="Steam allocation" subtitle="Two shared generators. Conflicts are resolved by due date, then by value of steam (₹ net per tonne). ◆ marks each well's economic re-steam day."
        right={<>{sched.data && Object.entries(sched.data.schedule.utilization).map(([g, u]) => <span key={g} className="num text-xs text-ink-2">{g} {pct(u)} used</span>)}<CalendarClock size={16} className="text-ink-3" /></>}>
        {sched.data ? <SteamCalendar events={sched.data.schedule.events} generators={sched.data.generators} horizon={Math.min(sched.data.schedule.horizon_days, 180)} simTime={sched.data.sim_time} /> : <div className="h-32" />}
      </Panel>

      <Panel title="Wells" bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-ink-3">
              <tr className="border-b border-line">
                {["Well", "CSS phase and cycle progress", "Oil m³/d", "Pump fillage", "Zone °C", "Viscosity cP", "kWh/bbl", "Re-steam: plan / economic", "Card (CNN)", "Status"].map((h) => <th key={h} className="whitespace-nowrap px-4 py-2 font-medium">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {wells.map((w) => {
                const prog = Math.min(w.day_in_cycle / Math.max(w.cycle_days_plan, 1), 1);
                return (
                  <tr key={w.id} className="cursor-pointer border-b border-line/60 hover:bg-surface-2" onClick={() => go({ page: "well", id: w.id })}>
                    <td className="px-4 py-2 font-semibold">{w.id}<div className="text-xs font-normal text-ink-3">{w.pad} · cycle {w.cycle_no}</div></td>
                    <td className="px-4 py-2"><PhaseBadge phase={w.phase} tAvg={w.t_avg} />
                      <div className="mt-1 h-1 w-28 rounded-full bg-surface-3"><div className="h-full rounded-full" style={{ width: `${prog * 100}%`, background: C.s1 }} /></div></td>
                    <td className="num px-4 py-2">{fmt(w.oil, 2)}</td>
                    <td className="px-4 py-2">{w.phase === "production" ? (
                      <div className="flex items-center gap-2"><div className="h-1.5 w-14 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full bg-s1" style={{ width: `${Math.min(w.fillage, 1) * 100}%` }} /></div><span className="num text-xs">{pct(w.fillage)}</span></div>
                    ) : "–"}{w.vfd && <span className="ml-1 text-xs text-s7">VFD</span>}</td>
                    <td className="num px-4 py-2">{fmt(w.t_avg, 0)}</td>
                    <td className="num px-4 py-2">{fmt(w.mu_cp, 0)}</td>
                    <td className="num px-4 py-2">{fmt(w.energy_bbl, 0)}</td>
                    <td className="num px-4 py-2 text-xs">{w.phase === "production" ? <>{fmt(w.planned_days_left, 0)} d / <span className="text-s7">{w.resteam_in_days !== null && w.resteam_in_days !== undefined ? `${w.resteam_in_days} d` : "–"}</span></> : "–"}</td>
                    <td className="px-4 py-2 text-xs">{w.card ? <>{w.card.replace(/_/g, " ")} <span className="num text-ink-3">{pct(w.card_conf)}</span></> : "–"}</td>
                    <td className="px-4 py-2"><WellStatus status={w.status} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
