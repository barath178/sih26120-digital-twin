import { useState } from "react";
import { Check, X } from "lucide-react";
import type { Alert, Recommendation } from "../types";
import { engineerName, post } from "../api";
import { Button, C, ErrorBox, Pill, SeverityBadge, inr, simDate, titleCase, useToast } from "./ui";
import { go } from "../router";

const TYPE_LABEL: Record<Recommendation["type"], string> = {
  spm: "Pump speed",
  vfd: "Closed-loop VFD",
  resteam: "Re-steam timing",
  workover: "Workover",
  design: "Joint CSS + SRP design",
};

export function RecCard({ rec, onDone }: { rec: Recommendation; onDone?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const toast = useToast();
  const decide = async (approve: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      await post(`/api/recommendations/${rec.id}/${approve ? "approve" : "reject"}`, { by: engineerName(), note });
      toast({ title: approve ? "Approved" : "Rejected", body: rec.title, tone: approve ? "good" : "info" });
      onDone?.();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card-flat p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <div className="text-[14px] font-semibold leading-snug text-ink">{rec.title}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
            <button className="font-medium text-ink-2 hover:underline" onClick={() => go({ page: "well", id: rec.well_id })}>{rec.well_id}</button>
            <Pill>{TYPE_LABEL[rec.type]}</Pill>
            <Pill>{rec.source === "optimizer" ? "Optimiser" : "Rule engine"}</Pill>
            <span>{simDate(rec.updated)}</span>
          </div>
        </div>
        <div className="text-right">
          <div className="mono text-base font-medium text-[#6fdc82]">{rec.gain_inr_per_day >= 0 ? "+" : ""}{inr(rec.gain_inr_per_day)}<span className="text-xs text-ink-3"> /day</span></div>
          <div className="text-[11px] text-ink-3">expected net value</div>
        </div>
      </div>
      <ul className="mt-3 list-disc space-y-1.5 pl-5 text-[13px] leading-relaxed text-ink-2 marker:text-ink-3">
        {rec.detail.map((d, i) => <li key={i}>{d}</li>)}
      </ul>
      {rec.status === "pending" ? (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" aria-label="Decision note"
            className="h-8 min-w-0 flex-1 px-2.5 text-xs" />
          <Button small variant="good" onClick={() => decide(true)} disabled={busy}><Check size={13} /> Approve</Button>
          <Button small variant="ghost" onClick={() => decide(false)} disabled={busy}><X size={13} /> Reject</Button>
        </div>
      ) : (
        <div className="mt-3 text-xs text-ink-3">
          {titleCase(rec.status)} {rec.decided_by ? `by ${rec.decided_by}` : ""} {rec.decided_at ? `· ${simDate(rec.decided_at)}` : ""} {rec.note ? `· “${rec.note}”` : ""}
        </div>
      )}
      {err && <div className="mt-2"><ErrorBox error={err} /></div>}
    </div>
  );
}

const SEV_COLOR = { critical: C.crit, warning: C.warn, info: C.ink3 } as const;

export function AlertItem({ alert, onAck }: { alert: Alert; onAck?: () => void }) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const ack = async () => {
    setBusy(true);
    try {
      await post(`/api/alerts/${alert.id}/ack`, { by: engineerName() });
      toast({ title: "Alert acknowledged", body: alert.title, tone: "info" });
      onAck?.();
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card-flat relative overflow-hidden p-4 pl-5">
      <span className="absolute inset-y-0 left-0 w-1" style={{ background: SEV_COLOR[alert.severity] }} aria-hidden />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={alert.severity} />
          <span className="text-[14px] font-semibold">{alert.title}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-ink-3">
          <button className="font-medium text-ink-2 hover:underline" onClick={() => go({ page: "well", id: alert.well_id })}>{alert.well_id}</button>
          <span>{simDate(alert.created)}</span>
          {alert.status === "active" && onAck && <Button small variant="ghost" onClick={ack} disabled={busy}>Acknowledge</Button>}
          {alert.status !== "active" && <Pill>{titleCase(alert.status)}{alert.ack_by ? ` · ${alert.ack_by}` : ""}</Pill>}
        </div>
      </div>
      <div className="mt-3 grid gap-x-6 gap-y-2 text-[13px] leading-relaxed md:grid-cols-2">
        <div><div className="eyebrow mb-0.5">Root cause</div><div className="text-ink-2">{alert.root_cause}</div></div>
        <div><div className="eyebrow mb-0.5">Suggested fix</div><div className="text-ink-2">{alert.fix}</div></div>
      </div>
    </div>
  );
}
