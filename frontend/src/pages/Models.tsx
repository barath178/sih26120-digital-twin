import { useState, type FormEvent } from "react";
import { CartesianGrid, ComposedChart, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import { Upload } from "lucide-react";
import { useFetch } from "../live";
import { apiUrl, engineerName, get, postForm } from "../api";
import TimeChart from "../components/TimeChart";
import { Button, C, ChartTip, ErrorBox, KV, Legend, Loading, Panel, axisProps, fmt, pct } from "../components/ui";

interface RiskMetrics {
  model: string; classes: string[]; accuracy: number; macro_f1: number; precision: Record<string, number>; recall: Record<string, number>;
  f1: Record<string, number>; support: Record<string, number>; confusion: number[][]; n_train: number; n_test: number; split: string; classes_without_support: string[];
}
interface ModelsResp {
  risk: RiskMetrics;
  dynacard: { model: string; accuracy: number; per_class_recall: Record<string, number>; confusion: number[][]; classes: string[]; n_train: number; n_test: number };
  forecaster: { model: string; mape_p50: number; r2_log: number; p10_p90_coverage: number; n_train_rows: number; n_cycles: number };
  surrogate: { model: string; r2: Record<string, number>; mae: Record<string, number>; n_train: number; n_test: number };
  anomaly: { model: string; features: string[]; n_train: number };
  calibration: { well_id: string; status: string; confidence: number; mape: number | null; mape_before: number | null; params: Record<string, number>; n_days: number | null }[];
  physics: { walther: { A: number; B: number; r2: number }; models: string[] };
  bus: string;
  telemetry_rows: number;
}
interface ConfigResp {
  viscosity_table: { t_c: number; mu_cp: number }[];
  viscosity_curve: { t_c: number; mu_cp: number }[];
  reservoir: Record<string, number>;
  economics: Record<string, number>;
  published: string[];
}
interface UploadResp {
  status: string; params: Record<string, number>; previous?: Record<string, number>; mape_recent?: number; mape_before?: number; confidence?: number;
  rows: number; applied: boolean; fit_series?: { day: number[]; measured_oil: number[]; model_oil: number[] };
}

const WELLS = Array.from({ length: 10 }, (_, i) => `BGW-${String(i + 1).padStart(3, "0")}`);
const OUT_LABEL: Record<string, string> = {
  oil_per_day: "Oil per day", sor: "Steam-oil ratio", failure_prob: "Failure probability", npv_per_day: "Net value per day",
  avg_fillage: "Average fillage", max_goodman: "Peak rod loading", max_torque_nm: "Peak torque", max_motor_kw: "Peak motor power", rodfall_ratio: "Rod-fall ratio",
};

function blue(v: number) {
  // sequential single-hue ramp (reference palette blue 100 -> 700) for counts
  const steps = ["#1a1a19", "#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#86b6ef"];
  return steps[Math.min(steps.length - 1, Math.round(v * (steps.length - 1)))];
}

export default function Models() {
  const m = useFetch(() => get<ModelsResp>("/api/models"), [], 15000);
  const cfg = useFetch(() => get<ConfigResp>("/api/config"), []);
  const [well, setWell] = useState("BGW-001");
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ steam_t: 1200, inj_rate_tpd: 200, soak_days: 5, apply: false });
  const [up, setUp] = useState<UploadResp | null>(null);
  const [upErr, setUpErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return setUpErr("Choose a CSV file first.");
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(form).forEach(([k, v]) => fd.append(k, String(v)));
    fd.append("by", engineerName());
    setBusy(true);
    setUpErr(null);
    try {
      setUp(await postForm<UploadResp>(`/api/wells/${well}/upload`, fd));
    } catch (err) {
      setUpErr((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!m.data) return m.error ? <ErrorBox error={m.error} /> : <Loading />;
  const d = m.data;
  const maxCell = Math.max(...d.dynacard.confusion.flat(), 1);
  const fitRows = up?.fit_series ? up.fit_series.day.map((t, i) => ({ t, meas: up.fit_series!.measured_oil[i], model: up.fit_series!.model_oil[i] })) : [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Models & data</h1>
        <p className="text-sm text-ink-3">Physics core, ML models, calibration status and data upload. Telemetry: {d.bus} · {d.telemetry_rows.toLocaleString("en-IN")} rows stored.</p>
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Panel title="Dynacard classifier" subtitle={`${d.dynacard.model.toUpperCase()} on 64×64 downhole-card images · held-out synthetic test set (${d.dynacard.n_test})`}>
          <div className="mb-3 flex items-baseline gap-2"><span className="num text-3xl font-semibold">{pct(d.dynacard.accuracy, 1)}</span><span className="text-sm text-ink-3">accuracy</span></div>
          <div className="overflow-x-auto">
            <table className="text-[11px]">
              <thead><tr><th className="pr-2 text-left font-normal text-ink-3">true ↓ / predicted →</th>{d.dynacard.classes.map((c, i) => <th key={c} className="w-9 font-normal text-ink-3" title={c}>{i + 1}</th>)}</tr></thead>
              <tbody>{d.dynacard.confusion.map((row, i) => (
                <tr key={i}>
                  <td className="pr-2 text-ink-2">{i + 1}. {d.dynacard.classes[i].replace(/_/g, " ")}</td>
                  {row.map((v, j) => (
                    <td key={j} className="num h-7 w-9 text-center" style={{ background: blue(v / maxCell), color: v / maxCell > 0.45 ? "#fff" : C.ink2 }} title={`${v} cards`}>{v}</td>
                  ))}
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-ink-3">Trained on cards generated by the damped wave-equation model and converted with the Gibbs diagnostic using a deliberately perturbed damping estimate (±30%) and fluid load (±10%), as happens in the field.</p>
        </Panel>

        <Panel title="Production forecaster" subtitle={d.forecaster.model}>
          <KV k="Median error (P50 MAPE)" v={pct(d.forecaster.mape_p50, 1)} />
          <KV k="R² (log rate)" v={fmt(d.forecaster.r2_log, 3)} />
          <KV k="P10–P90 coverage (target 80%)" v={pct(d.forecaster.p10_p90_coverage, 0)} />
          <KV k="Training rows / simulated cycles" v={`${d.forecaster.n_train_rows.toLocaleString("en-IN")} / ${d.forecaster.n_cycles.toLocaleString("en-IN")}`} />
          <div className="mt-4 border-t border-line pt-3">
            <div className="mb-1 text-sm font-semibold">Anomaly detector</div>
            <KV k="Model" v={d.anomaly.model} />
            <KV k="Residual features" v={d.anomaly.features.join(", ").replace(/_/g, " ")} />
            <KV k="Healthy training vectors" v={d.anomaly.n_train.toLocaleString("en-IN")} />
          </div>
        </Panel>

        <Panel title="Optimisation surrogate" subtitle={`${d.surrogate.model} · test set ${d.surrogate.n_test}`}>
          <div className="space-y-1.5">
            {Object.entries(d.surrogate.r2).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2 text-xs">
                <span className="w-36 text-ink-2">{OUT_LABEL[k] ?? k}</span>
                <div className="h-1.5 flex-1 rounded-full bg-surface-3"><div className="h-full rounded-full bg-s1" style={{ width: `${Math.max(v, 0) * 100}%` }} /></div>
                <span className="num w-12 text-right">R² {fmt(v, 2)}</span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-ink-3">Low R² on average fillage reflects its tiny spread under pump-off control (mean absolute error {pct(d.surrogate.mae?.avg_fillage, 1)}). Every Pareto design is re-verified with full physics.</p>
        </Panel>
      </div>

      <Panel title="SRP risk classifier (model-estimated risks)" subtitle={`${d.risk.model} · ${d.risk.split} · held-out SYNTHETIC test set of ${d.risk.n_test.toLocaleString("en-IN")} rows - this is not field accuracy`} bodyClass="p-0">
        <div className="grid xl:grid-cols-2">
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead className="text-left text-xs text-ink-3"><tr className="border-b border-line">{["Class", "Precision", "Recall", "F1", "Test rows"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody>{d.risk.classes.map((c) => (
              <tr key={c} className="num border-b border-line/60"><td className="px-4 py-1.5 font-medium">{c.replace(/_/g, " ").toLowerCase()}</td><td className="px-4">{pct(d.risk.precision[c], 0)}</td><td className="px-4">{pct(d.risk.recall[c], 0)}</td><td className="px-4">{fmt(d.risk.f1[c], 2)}</td>
                <td className="px-4">{d.risk.support[c]}{d.risk.support[c] < 30 ? <span className="ml-1 text-xs text-[#f2c14e]">low support</span> : null}</td></tr>
            ))}</tbody>
          </table></div>
          <div className="p-4 text-xs text-ink-3">
            <div className="mb-2 flex gap-6 text-sm text-ink"><span>Accuracy <b>{pct(d.risk.accuracy, 1)}</b></span><span>Macro-F1 <b>{fmt(d.risk.macro_f1, 2)}</b></span></div>
            Labels come from simulator ground truth (fluid pound at speed, rod-fall ratio, viscous drag with high rod stress); the classifier sees only noisy field-measurable inputs. Rare classes have few examples, so their scores are uncertain. The UI always calls these outputs "model-estimated risks".
          </div>
        </div>
      </Panel>

      <Panel title="Twin calibration status" subtitle="Productivity, wellbore heat-loss and heated-zone cooling multipliers history-matched to each well's current-cycle data" bodyClass="p-0">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-ink-3"><tr className="border-b border-line">{["Well", "Status", "Confidence", "Oil error before → after", "Productivity ×", "Heat loss ×", "Cooling ×", "Days"].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
          <tbody>{d.calibration.map((c) => (
            <tr key={c.well_id} className="num border-b border-line/60">
              <td className="px-4 py-1.5 font-semibold">{c.well_id}</td><td className="px-4 py-1.5 text-ink-2">{c.status}</td>
              <td className="px-4 py-1.5">{pct(c.confidence)}</td>
              <td className="px-4 py-1.5">{c.mape !== null && c.mape !== undefined ? `${pct(c.mape_before, 1)} → ${pct(c.mape, 1)}` : "–"}</td>
              <td className="px-4 py-1.5">{fmt(c.params?.pi_mult, 3)}</td><td className="px-4 py-1.5">{fmt(c.params?.u_mult, 3)}</td>
              <td className="px-4 py-1.5">{fmt(c.params?.cool_mult, 3)}</td><td className="px-4 py-1.5">{c.n_days ?? "–"}</td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="Upload field data and recalibrate" subtitle="Daily production-test data for one CSS cycle (CSV)">
          <form onSubmit={submit} className="space-y-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <select value={well} onChange={(e) => setWell(e.target.value)} className="rounded-md border border-line bg-surface-2 px-2 py-1.5" aria-label="Well">
                {WELLS.map((w) => <option key={w}>{w}</option>)}
              </select>
              <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-xs text-ink-2 file:mr-2 file:rounded-md file:border-0 file:bg-surface-3 file:px-2 file:py-1 file:text-ink" />
              <a className="text-xs text-s1 hover:underline" href={apiUrl(`/api/sample-csv?well_id=${well}`)} download={`${well}_sample.csv`}>Download sample CSV</a>
            </div>
            <div className="grid grid-cols-3 gap-3">
              {([["steam_t", "Steam injected (t)"], ["inj_rate_tpd", "Injection rate (t/d)"], ["soak_days", "Soak (days)"]] as const).map(([k, l]) => (
                <label key={k} className="text-xs text-ink-3">{l}
                  <input type="number" value={form[k]} min={0} step="any" onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })}
                    className="mt-1 w-full rounded-md border border-line bg-surface-2 px-2 py-1 text-sm text-ink" />
                </label>
              ))}
            </div>
            <label className="flex items-center gap-2 text-xs text-ink-2"><input type="checkbox" checked={form.apply} onChange={(e) => setForm({ ...form, apply: e.target.checked })} /> Apply the fitted parameters to the live twin</label>
            <p className="text-xs text-ink-3">Columns: <code>prod_day, oil_rate_m3d, water_rate_m3d, spm</code> and optionally <code>wellhead_temp_c</code>. One row per production day.</p>
            <Button type="submit" disabled={busy}><Upload size={14} /> {busy ? "Calibrating…" : "Upload & calibrate"}</Button>
          </form>
          {upErr && <div className="mt-3"><ErrorBox error={upErr} /></div>}
          {up && (
            <div className="mt-4 border-t border-line pt-3">
              <div className="grid grid-cols-2 gap-x-6">
                <KV k="Rows" v={up.rows} /><KV k="Status" v={up.status} />
                <KV k="Oil error" v={`${pct(up.mape_before, 1)} → ${pct(up.mape_recent, 1)}`} /><KV k="Confidence" v={pct(up.confidence)} />
                {Object.entries(up.params).map(([k, v]) => <KV key={k} k={k} v={`${fmt(up.previous?.[k], 3)} → ${fmt(v, 3)}`} />)}
                <KV k="Applied to twin" v={up.applied ? "yes" : "no"} />
              </div>
              {fitRows.length > 0 && <div className="mt-3"><TimeChart data={fitRows} dateOf={(t) => `d${t}`} unit="m³/d" digits={2} height={150}
                series={[{ key: "meas", name: "Uploaded", color: C.s1 }, { key: "model", name: "Calibrated twin", color: C.s2, dashed: true }]} /></div>}
            </div>
          )}
        </Panel>

        <Panel title="Viscosity-temperature model" subtitle={cfg.data ? `Walther / ASTM D341 fit, R² = ${fmt(d.physics.walther.r2, 5)} · log scale` : ""}
          right={<Legend items={[{ label: "Walther fit", color: C.s2 }, { label: "Calibration table", color: C.s1 }]} />}>
          {cfg.data ? (
            <div className="h-64">
              <ResponsiveContainer>
                <ComposedChart margin={{ top: 6, right: 12, bottom: 12, left: 4 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis type="number" dataKey="t_c" domain={[40, 300]} {...axisProps} label={{ value: "Temperature (°C)", position: "insideBottom", offset: -6, fill: C.ink3, fontSize: 11 }} />
                  <YAxis type="number" dataKey="mu_cp" scale="log" domain={[1, 30000]} ticks={[1, 10, 100, 1000, 10000]} {...axisProps} width={52} tickFormatter={(v) => Number(v).toLocaleString("en-IN")} />
                  <Tooltip content={<ChartTip labelFmt={() => "Oil viscosity"} valueFmt={(v) => `${fmt(v, 0)} cP`} />} />
                  <Line data={cfg.data.viscosity_curve} dataKey="mu_cp" name="Walther fit" stroke={C.s2} strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Scatter data={cfg.data.viscosity_table} dataKey="mu_cp" name="Table" fill={C.s1} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          ) : <Loading />}
          <p className="mt-1 text-xs text-ink-3">50 °C point matches the published 10,000–13,000 cP for Baghewala crude; other points are engineering assumptions to be replaced by OIL laboratory PVT data.</p>
        </Panel>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="Physics core" subtitle="Analytical and numerical models in the twin (a fast proxy for a thermal simulator such as CMG STARS)">
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">{d.physics.models.map((x) => <li key={x}>{x}</li>)}</ul>
        </Panel>
        <Panel title="Data basis" subtitle="What is published vs assumed">
          {cfg.data ? (
            <>
              <div className="mb-2 text-xs font-medium text-ink-3">Published Baghewala properties used</div>
              <ul className="mb-3 list-disc pl-5 text-sm text-ink-2">{cfg.data.published.map((p) => <li key={p}>{p}</li>)}</ul>
              <div className="mb-1 text-xs font-medium text-ink-3">Key assumptions (replaceable with OIL data)</div>
              <div className="grid grid-cols-2 gap-x-6">
                <KV k="Net pay" v={fmt(cfg.data.reservoir.net_pay_m, 0)} unit="m" />
                <KV k="Permeability" v={fmt(cfg.data.reservoir.permeability_md, 0)} unit="mD" />
                <KV k="Reservoir pressure" v={fmt(cfg.data.reservoir.pressure_mpa, 1)} unit="MPa" />
                <KV k="Mid-perforation depth" v={fmt(cfg.data.reservoir.depth_mid_perf_m, 0)} unit="m" />
                <KV k="Oil price" v={`₹${fmt(cfg.data.economics.oil_price_inr_per_m3, 0)}`} unit="/m³" />
                <KV k="Workover cost" v={`₹${fmt(cfg.data.economics.workover_cost_inr / 1e5, 0)} L`} />
              </div>
            </>
          ) : <Loading />}
        </Panel>
      </div>
    </div>
  );
}

