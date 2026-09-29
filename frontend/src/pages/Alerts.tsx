import { useState } from "react";
import { useFetch } from "../live";
import { get } from "../api";
import type { Alert, Recommendation } from "../types";
import { AlertItem, RecCard } from "../components/RecCard";
import { EmptyState, Loading, PageHeader, Panel, Tabs, inr, simDate } from "../components/ui";

interface AuditRow { time: string; sim_time: string; actor: string; action: string; well_id: string | null; detail: string }
type Tab = "approvals" | "alerts" | "audit";

export default function Alerts() {
  const [tab, setTab] = useState<Tab>("approvals");
  const [well, setWell] = useState("all");
  const recs = useFetch(() => get<Recommendation[]>("/api/recommendations"), [], 4000);
  const alerts = useFetch(() => get<Alert[]>("/api/alerts"), [], 4000);
  const audit = useFetch(() => get<AuditRow[]>("/api/audit?limit=300"), [tab], tab === "audit" ? 5000 : undefined);
  const byWell = <T extends { well_id: string | null }>(xs: T[]) => (well === "all" ? xs : xs.filter((x) => x.well_id === well));
  const pending = byWell((recs.data ?? []).filter((r) => r.status === "pending"));
  const sev = { critical: 0, warning: 1, info: 2 } as const;
  const active = byWell((alerts.data ?? []).filter((a) => a.status !== "resolved")).sort((a, b) => sev[a.severity] - sev[b.severity]);
  const totalGain = pending.reduce((s, r) => s + Math.max(r.gain_inr_per_day, 0), 0);
  const reload = () => { recs.reload(); alerts.reload(); };

  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Actions" title="Approvals, alerts and the audit trail" subtitle="The twin recommends; engineers decide. Every decision is recorded with who, when and why."
        actions={
          <select value={well} onChange={(e) => setWell(e.target.value)} className="h-9 px-2.5 text-[13px]" aria-label="Filter by well">
            <option value="all">All wells</option>
            {Array.from({ length: 10 }, (_, i) => `BGW-${String(i + 1).padStart(3, "0")}`).map((w) => <option key={w}>{w}</option>)}
          </select>
        } />
      <Tabs<Tab> label="Action views" value={tab} onChange={setTab} tabs={[
        { key: "approvals", label: "Awaiting approval", hint: "recommendations from the twin", count: pending.length },
        { key: "alerts", label: "Active alerts", hint: "with root cause and fix", count: active.length },
        { key: "audit", label: "Audit trail", hint: "approvals, changes, uploads" },
      ]} />

      {tab === "approvals" && (
        <div className="space-y-3">
          {pending.length > 0 && <p className="text-sm text-ink-2">Approving everything below is worth about <span className="mono font-medium text-ink">{inr(totalGain)}</span> per day in expected net value.</p>}
          {recs.loading && !recs.data ? <Loading /> : pending.length ? pending.map((r) => <RecCard key={r.id} rec={r} onDone={reload} />) : <EmptyState title="Nothing awaiting approval">New recommendations appear here as the twin detects opportunities.</EmptyState>}
        </div>
      )}
      {tab === "alerts" && (
        <div className="space-y-3">
          {alerts.loading && !alerts.data ? <Loading /> : active.length ? active.map((a) => <AlertItem key={a.id} alert={a} onAck={reload} />) : <EmptyState title="No active alerts">Every well is within its limits.</EmptyState>}
        </div>
      )}
      {tab === "audit" && (
        <Panel bodyClass="p-0">
          {audit.data ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left"><tr className="border-b border-line">{["Sim time", "Who", "Action", "Well", "Detail"].map((h) => <th key={h} className="px-4 py-2.5">{h}</th>)}</tr></thead>
                <tbody>
                  {byWell(audit.data).map((a, i) => (
                    <tr key={i} className="border-b border-line/40 align-top">
                      <td className="mono whitespace-nowrap px-4 py-2 text-xs text-ink-2">{simDate(a.sim_time)}</td>
                      <td className="px-4 py-2">{a.actor}</td>
                      <td className="px-4 py-2 text-ink-2">{a.action.replace(/_/g, " ")}</td>
                      <td className="px-4 py-2">{a.well_id ?? "–"}</td>
                      <td className="max-w-xl truncate px-4 py-2 text-xs text-ink-3" title={a.detail}>{a.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Loading />}
        </Panel>
      )}
    </div>
  );
}
