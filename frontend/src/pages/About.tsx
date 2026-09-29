import { useFetch } from "../live";
import { get } from "../api";
import { Loading, Panel, Pill } from "../components/ui";

interface Prov {
  data_mode: string;
  model_version: string;
  disclaimer: string;
  safety: string;
  parameters: { parameter: string; value: string; status: string; source: string; note: string }[];
  sources: { id: string; title: string; url: string }[];
  status_codes: Record<string, string>;
  data_labels: Record<string, string>;
  non_goals: string[];
}

const STATUS_COLOR: Record<string, string> = {
  VERIFIED_FIELD: "#0ca30c",
  PUBLIC_REFERENCE: "#3987e5",
  HISTORICAL_REFERENCE: "#9085e9",
  SYNTHETIC_ASSUMPTION: "#c98500",
  USER_CONFIGURED: "#199e70",
};

export default function About() {
  const p = useFetch(() => get<Prov>("/api/provenance"), []);
  if (!p.data) return <Loading />;
  const d = p.data;
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">About & assumptions</h1>
        <p className="text-sm text-ink-3">Data mode: <b className="text-ink">{d.data_mode}</b> · model version {d.model_version}</p>
      </div>

      <div className="rounded-xl border border-[#c98500]/60 bg-[#c98500]/10 p-4 text-sm leading-relaxed text-ink">
        <div className="mb-1 font-semibold">DEMO DATA MODE</div>
        {d.disclaimer.replace("DEMO DATA MODE - ", "")}
      </div>
      <Panel title="Decision-support statement">
        <p className="text-sm leading-relaxed text-ink-2">{d.safety}</p>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-ink-2">{d.non_goals.map((n) => <li key={n}>{n}</li>)}</ul>
      </Panel>

      <Panel title="Parameter provenance" subtitle="Every parameter used in simulation, with its status and source (all values live in backend/app/config.py)" bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-ink-3"><tr className="border-b border-line">{["Parameter", "Value used", "Status", "Source", "Note"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody>
              {d.parameters.map((r) => (
                <tr key={r.parameter} className="border-b border-line/60 align-top">
                  <td className="px-4 py-1.5 font-medium">{r.parameter}</td>
                  <td className="px-4 py-1.5 text-ink-2">{r.value}</td>
                  <td className="px-4 py-1.5"><span className="inline-flex items-center gap-1.5 text-xs"><span className="h-2 w-2 rounded-full" style={{ background: STATUS_COLOR[r.status] }} />{r.status}</span></td>
                  <td className="px-4 py-1.5 text-ink-3">{r.source}</td>
                  <td className="px-4 py-1.5 text-xs text-ink-3">{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="Status codes">
          <dl className="space-y-2 text-sm">
            {Object.entries(d.status_codes).map(([k, v]) => (
              <div key={k}><dt className="flex items-center gap-1.5 font-medium"><span className="h-2 w-2 rounded-full" style={{ background: STATUS_COLOR[k] }} />{k}</dt><dd className="pl-3.5 text-xs text-ink-3">{v}</dd></div>
            ))}
          </dl>
        </Panel>
        <Panel title="Data labels used in the UI">
          <dl className="space-y-2 text-sm">
            {Object.entries(d.data_labels).map(([k, v]) => (
              <div key={k}><dt><Pill>{k}</Pill></dt><dd className="mt-0.5 text-xs text-ink-3">{v}</dd></div>
            ))}
          </dl>
        </Panel>
      </div>

      <Panel title="Source register" bodyClass="p-0">
        <table className="w-full text-sm">
          <tbody>
            {d.sources.map((s) => (
              <tr key={s.id} className="border-b border-line/60">
                <td className="w-14 px-4 py-1.5 font-semibold">{s.id}</td>
                <td className="px-4 py-1.5 text-ink-2">{s.title}</td>
                <td className="px-4 py-1.5"><a className="break-all text-xs text-s1 hover:underline" href={s.url} target="_blank" rel="noreferrer">{s.url}</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <p className="text-xs text-ink-3">Public field sources mix current pages with historical tender documents. The same parameter can differ by well, completion and reporting period, so no single historical number is treated as a universal live operating value or safety limit.</p>
    </div>
  );
}
