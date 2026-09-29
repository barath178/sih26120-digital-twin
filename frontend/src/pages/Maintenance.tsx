import { useState } from "react";
import { CalendarCheck, Wrench } from "lucide-react";
import { useFetch } from "../live";
import { engineerName, get, post } from "../api";
import { Button, C, Loading, Panel, Stat, fmt, inr } from "../components/ui";
import { go } from "../router";

interface Job {
  well_id: string; reasons: string[]; rul_days: number | null; do_in_days: number; combined_with_resteam: boolean; failed: boolean;
  planned_cost_inr: number; run_to_failure_cost_inr: number; saving_inr: number; lost_oil_m3d: number; action: string;
}
interface Plan {
  jobs: Job[]; batches: { batch: number; start_in_days: number; wells: string[]; mobilisation_saving_inr: number }[]; total_saving_inr: number; n_jobs: number;
  assumptions: { unplanned_down_days: number; planned_down_days: number; combined_job_saving: number; rig_mobilisation_inr: number; group_window_days: number };
}

const HORIZON = 130;

export default function Maintenance() {
  const plan = useFetch(() => get<Plan>("/api/maintenance-plan"), [], 8000);
  const [done, setDone] = useState<Record<string, boolean>>({});
  if (!plan.data) return <Loading />;
  const p = plan.data;
  const pos = (d: number) => `${Math.min(Math.max(d, 0) / HORIZON, 1) * 100}%`;
  const schedule = async (j: Job) => {
    await post(`/api/maintenance/${j.well_id}/schedule`, { by: engineerName(), do_in_days: j.do_in_days, reasons: j.reasons });
    setDone({ ...done, [j.well_id]: true });
  };
  const ticks = [0, 30, 60, 90, 120];

  return (
    <div className="space-y-4">
      <p className="max-w-4xl text-sm text-ink-3">
        Reliability is half of the problem statement. The twin tracks rod fatigue (Miner's rule on measured loads) and pump wear (downhole-card load retention), predicts when each well will need a rig,
        and picks the date: alongside the next re-steam pull when the equipment will last that long, otherwise at 80 % of remaining life. Jobs close together share one rig mobilisation.
      </p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Interventions predicted" value={p.n_jobs} sub="within the next 120 days" accent={C.s2} />
        <Stat label="Saved by planning vs running to failure" value={inr(p.total_saving_inr)} accent={C.s3} sub="workover + deferred oil + rig sharing" />
        <Stat label="Rig batches" value={p.batches.length} sub={p.batches.length ? p.batches.map((b) => b.wells.join(" + ")).join(" | ") : "none"} />
        <Stat label="Downtime per job" value={`${p.assumptions.planned_down_days} d`} unit={`vs ${p.assumptions.unplanned_down_days} d unplanned`} sub="planned vs fishing and rig wait" />
      </div>

      {p.n_jobs === 0 ? <Panel><p className="text-sm text-ink-3">No rod or pump failures are predicted within the next 120 days.</p></Panel> : (
        <>
          <Panel title="Rig calendar (next 120 days)" subtitle="Bar = planned job date · ✕ = predicted end of life if left alone · ◆ = the re-steam pull it can share">
            <div className="relative">
              <div className="relative ml-24 h-5 text-[11px] text-ink-3">{ticks.map((t) => <span key={t} className="absolute -translate-x-1/2" style={{ left: pos(t) }}>{t === 0 ? "now" : `+${t} d`}</span>)}</div>
              {p.jobs.map((j) => (
                <div key={j.well_id} className="flex items-center gap-2 py-1.5">
                  <button className="w-22 shrink-0 text-left text-sm font-semibold hover:underline" style={{ width: "5.5rem" }} onClick={() => go({ page: "well", id: j.well_id })}>{j.well_id}</button>
                  <div className="relative h-7 flex-1 rounded bg-surface-2">
                    {ticks.map((t) => <span key={t} className="absolute inset-y-0 w-px bg-line" style={{ left: pos(t) }} />)}
                    <span className={`absolute top-1 h-5 w-3 rounded ${j.failed ? "bg-crit" : j.combined_with_resteam ? "bg-s3" : "bg-s2"}`} style={{ left: `calc(${pos(j.do_in_days)} - 6px)` }} title={j.action} />
                    {j.rul_days !== null && j.rul_days > 0 && <span className="absolute top-1 -translate-x-1/2 text-sm font-bold text-crit" style={{ left: pos(j.rul_days) }} title="predicted end of life">✕</span>}
                    {j.combined_with_resteam && <span className="absolute top-1.5 -translate-x-1/2 text-xs text-s7" style={{ left: pos(j.do_in_days) }}>◆</span>}
                  </div>
                </div>
              ))}
              <div className="ml-24 mt-1 flex flex-wrap gap-4 text-[11px] text-ink-2">
                <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-crit" />failed: act now</span>
                <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-s3" />combined with the re-steam pull</span>
                <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-s2" />stand-alone workover</span>
              </div>
            </div>
          </Panel>
          <Panel title="Jobs" bodyClass="p-0">
            <div className="overflow-x-auto"><table className="w-full text-sm">
              <thead className="text-left text-xs text-ink-3"><tr className="border-b border-line">{["Well", "Why", "When", "Plan", "Planned cost", "If run to failure", "Saving", ""].map((h) => <th key={h} className="whitespace-nowrap px-4 py-2 font-medium">{h}</th>)}</tr></thead>
              <tbody>{p.jobs.map((j) => (
                <tr key={j.well_id} className="border-b border-line/60 align-top">
                  <td className="px-4 py-2 font-semibold">{j.well_id}</td>
                  <td className="max-w-xs px-4 py-2 text-xs text-ink-2">{j.reasons.map((r) => <div key={r}>{r}</div>)}</td>
                  <td className="num whitespace-nowrap px-4 py-2">{j.failed ? <span className="text-crit">now</span> : `in ${fmt(j.do_in_days, 0)} d`}</td>
                  <td className="px-4 py-2 text-xs text-ink-2">{j.action}</td>
                  <td className="num px-4 py-2">{inr(j.planned_cost_inr)}</td>
                  <td className="num px-4 py-2">{inr(j.run_to_failure_cost_inr)}</td>
                  <td className="num px-4 py-2 font-semibold text-[#5fd35f]">{j.failed ? "–" : inr(j.saving_inr)}</td>
                  <td className="px-4 py-2">{done[j.well_id] ? <span className="inline-flex items-center gap-1 text-xs text-ink-2"><CalendarCheck size={13} /> in work plan</span> : <Button small variant="ghost" onClick={() => schedule(j)}><Wrench size={12} /> Add to work plan</Button>}</td>
                </tr>
              ))}</tbody>
            </table></div>
          </Panel>
        </>
      )}
      <p className="text-[11px] text-ink-3">Cost model (SYNTHETIC_ASSUMPTION, replace with OIL workover records): workover ₹12 L; unplanned {p.assumptions.unplanned_down_days} d vs planned {p.assumptions.planned_down_days} d downtime; {fmt(p.assumptions.combined_job_saving * 100, 0)}% cheaper when combined with the re-steam pull; ₹{fmt(p.assumptions.rig_mobilisation_inr / 1e5, 0)} L rig mobilisation saved per extra job in a {p.assumptions.group_window_days} d window. Lost oil is valued at the well's deliverability.</p>
    </div>
  );
}
