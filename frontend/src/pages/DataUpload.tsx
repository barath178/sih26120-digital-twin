import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Pause, Play, Upload, XCircle } from "lucide-react";
import { useFetch } from "../live";
import { apiUrl, get, post, postForm } from "../api";
import TimeChart from "../components/TimeChart";
import { Button, C, DataLabel, ErrorBox, KV, Loading, Panel, Stat, fmt, pct } from "../components/ui";

interface SchemaResp { columns: Record<string, { type: string; unit: string; description: string; min: number | null; max: number | null; required: boolean }>; files: string[] }
interface ValidateResp {
  ok: boolean; errors: string[]; warnings: string[]; rows: number; wells: string[]; missingness: Record<string, number>; token: string | null;
  preview: Record<string, string>[];
}
interface ReplayRow { t: string; phase: string; oil_bopd: number; spm: number; fillage: number; t_c: number; mu_cp: number; modelled_viscosity: number; risk: number | null; label: string | null; power_kw: number | null; sor: number | null }
interface ReplayResp { well_id: string; n: number; stride: number; rows: ReplayRow[]; risk_counts: Record<string, number>; ood: { fraction: number; flag: boolean; per_feature: Record<string, number> } | null; notes: string[] }

export default function DataUpload() {
  const schema = useFetch(() => get<SchemaResp>("/api/data/schema"), []);
  const [file, setFile] = useState<File | null>(null);
  const [val, setVal] = useState<ValidateResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [well, setWell] = useState("");
  const [rep, setRep] = useState<ReplayResp | null>(null);
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);

  const validate = async (f: File) => {
    setBusy(true); setErr(null); setRep(null); setVal(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const v = await postForm<ValidateResp>("/api/data/validate", fd);
      setVal(v);
      if (v.wells.length) setWell(v.wells[0]);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const loadSample = async () => {
    const txt = await get<string>("/api/data/sample-upload?well_id=BGW-001&rows=700");
    const f = new File([txt], "BGW-001_synthetic_sample.csv", { type: "text/csv" });
    setFile(f);
    validate(f);
  };
  const runReplay = async () => {
    if (!val?.token) return;
    setBusy(true); setErr(null);
    try {
      const r = await post<ReplayResp>("/api/data/replay", { token: val.token, well_id: well });
      setRep(r); setPos(0); setPlaying(false);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  useEffect(() => {
    if (!playing || !rep) return;
    const id = window.setInterval(() => setPos((p) => (p + Math.max(1, Math.round(rep.n / 150)) >= rep.n - 1 ? (setPlaying(false), rep.n - 1) : p + Math.max(1, Math.round(rep.n / 150)))), 120);
    return () => window.clearInterval(id);
  }, [playing, rep]);

  const rows = useMemo(() => (rep?.rows ?? []).map((r, i) => ({ t: i, oil: r.oil_bopd, fill: r.fillage, mu: r.mu_cp, temp: r.t_c, risk: r.risk === null ? null : r.risk * 100, spm: r.spm })), [rep]);
  const cur = rep?.rows[pos];
  const shown = rows.slice(0, pos + 1);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Data upload & replay</h1>
        <p className="text-sm text-ink-3">Bring a schema-compatible CSV, validate units and missingness, then replay it through the twin's viscosity and risk logic. Uploaded values are labelled <DataLabel kind="USER-ENTERED" />.</p>
      </div>
      <div className="grid gap-5 xl:grid-cols-3">
        <Panel title="1. Upload & validate" className="xl:col-span-2">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <input type="file" accept=".csv,text/csv" onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); if (f) validate(f); }}
              className="text-xs text-ink-2 file:mr-2 file:rounded-md file:border-0 file:bg-surface-3 file:px-2 file:py-1 file:text-ink" />
            <Button variant="ghost" small onClick={loadSample}><Upload size={13} /> Use a synthetic sample</Button>
            {schema.data && <span className="text-xs text-ink-3">Downloads: {schema.data.files.map((f) => <a key={f} className="mr-2 text-s1 hover:underline" href={apiUrl(`/api/data/files/${f}`)} download={f}>{f.replace("synthetic_", "").replace(".csv", "")}</a>)}</span>}
          </div>
          {busy && <p className="mt-3 text-sm text-ink-3">Working…</p>}
          {err && <div className="mt-3"><ErrorBox error={err} /></div>}
          {val && (
            <div className="mt-4 space-y-3">
              <div className="flex items-center gap-2 text-sm font-semibold">
                {val.ok ? <><CheckCircle2 size={16} color={C.good} /> Valid - {val.rows.toLocaleString("en-IN")} rows, {val.wells.length} well(s)</> : <><XCircle size={16} color={C.crit} /> Rejected - nothing was simulated</>}
                {file && <span className="font-normal text-ink-3">{file.name}</span>}
              </div>
              {val.errors.length > 0 && <ul className="list-disc space-y-1 rounded-lg border border-crit/40 bg-crit/10 py-2 pl-7 pr-3 text-sm text-ink">{val.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
              {val.warnings.length > 0 && <ul className="space-y-1 text-sm text-ink-2">{val.warnings.map((w) => <li key={w} className="flex gap-1.5"><AlertTriangle size={14} color={C.warn} className="mt-0.5 shrink-0" />{w}</li>)}</ul>}
              {val.ok && val.preview.length > 0 && (
                <div className="overflow-x-auto"><table className="text-xs"><thead><tr className="text-left text-ink-3">{Object.keys(val.preview[0]).slice(0, 12).map((k) => <th key={k} className="whitespace-nowrap pr-3 font-medium">{k}</th>)}</tr></thead>
                  <tbody>{val.preview.map((r, i) => <tr key={i} className="border-t border-line">{Object.values(r).slice(0, 12).map((v, j) => <td key={j} className="num whitespace-nowrap pr-3 py-0.5 text-ink-2">{v}</td>)}</tr>)}</tbody></table></div>
              )}
              {val.ok && (
                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                  <select value={well} onChange={(e) => setWell(e.target.value)} className="rounded-md border border-line bg-surface-2 px-2 py-1.5 text-sm" aria-label="Well to replay">{val.wells.map((w) => <option key={w}>{w}</option>)}</select>
                  <Button onClick={runReplay} disabled={busy}><Play size={14} /> Replay through the twin</Button>
                </div>
              )}
            </div>
          )}
        </Panel>
        <Panel title="Schema (units are strict)" subtitle="Ambiguous units are rejected, never silently converted" bodyClass="max-h-96 overflow-y-auto">
          {schema.data ? (
            <ul className="space-y-1 text-xs">
              {Object.entries(schema.data.columns).map(([k, c]) => (
                <li key={k} className="flex justify-between gap-2 border-b border-line/50 py-0.5"><span className={c.required ? "font-semibold text-ink" : "text-ink-2"}>{k}{c.required ? " *" : ""}</span><span className="text-ink-3">{c.unit}</span></li>
              ))}
            </ul>
          ) : <Loading />}
          <p className="mt-2 text-[11px] text-ink-3">* required. Phases: INJECTION, SOAK, PRODUCTION, COOLDOWN.</p>
        </Panel>
      </div>

      {rep && cur && (
        <>
          {rep.notes.map((n) => (
            <div key={n} className={`rounded-lg border px-3 py-2 text-sm ${n.startsWith("OUT-OF-DOMAIN") ? "border-[#c98500]/60 bg-[#c98500]/10 text-ink" : "border-line bg-surface text-ink-2"}`}>{n}</div>
          ))}
          <div className="flex items-center gap-3">
            <Button variant="ghost" small onClick={() => setPlaying(!playing)}>{playing ? <><Pause size={13} /> Pause</> : <><Play size={13} /> Play</>}</Button>
            <input type="range" min={0} max={rep.n - 1} value={pos} onChange={(e) => setPos(Number(e.target.value))} className="flex-1" aria-label="Replay position" />
            <span className="num text-xs text-ink-2">{cur.t.slice(0, 16).replace("T", " ")} · row {pos + 1}/{rep.n}{rep.stride > 1 ? ` (every ${rep.stride}th)` : ""}</span>
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <Stat label={<>Oil rate <DataLabel kind="USER-ENTERED" /></>} value={fmt(cur.oil_bopd, 1)} unit="BOPD" />
            <Stat label="Zone temp" value={fmt(cur.t_c, 0)} unit="°C" />
            <Stat label={<>Viscosity <DataLabel kind="MODELLED" /></>} value={fmt(cur.modelled_viscosity, 0)} unit="cP" sub={`file: ${fmt(cur.mu_cp, 0)} cP`} />
            <Stat label="Pump fillage" value={fmt(cur.fillage, 0)} unit="%" />
            <Stat label="SPM" value={fmt(cur.spm, 1)} />
            <Stat label={<>Risk score <DataLabel kind="MODELLED" /></>} value={cur.risk === null ? "–" : pct(cur.risk)} sub={cur.label ? cur.label.replace(/_/g, " ").toLowerCase() : "not estimated"} />
          </div>
          <div className="grid gap-5 lg:grid-cols-3">
            <Panel title="Oil rate (BOPD)"><TimeChart data={shown} dateOf={(t) => `#${Math.round(t)}`} unit="BOPD" digits={1} series={[{ key: "oil", name: "Oil rate", color: C.s1 }]} height={160} /></Panel>
            <Panel title="Viscosity (cP) and fillage (%)"><TimeChart data={shown} dateOf={(t) => `#${Math.round(t)}`} unit="" digits={0} series={[{ key: "fill", name: "Fillage %", color: C.s1 }, { key: "temp", name: "Zone temp °C", color: C.s2 }]} height={160} /></Panel>
            <Panel title="Model-estimated risk (%)" subtitle="synthetic-trained classifier"><TimeChart data={shown} dateOf={(t) => `#${Math.round(t)}`} unit="%" digits={0} yDomain={[0, 100]} series={[{ key: "risk", name: "Risk score", color: C.s2 }]} height={160} /></Panel>
          </div>
          <Panel title="Replay summary">
            <div className="grid gap-x-8 md:grid-cols-2">
              {Object.entries(rep.risk_counts).map(([k, n]) => <KV key={k} k={k.replace(/_/g, " ").toLowerCase()} v={n} unit="rows" />)}
              {rep.ood && <KV k="Features outside training range" v={pct(rep.ood.fraction, 0)} unit={rep.ood.flag ? "OUT-OF-DOMAIN" : "in domain"} />}
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}
