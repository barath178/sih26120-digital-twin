import { ArrowRight, Boxes, CalendarClock, Check, Database, FileText, Gauge, Layers, ShieldCheck, Wrench } from "lucide-react";
import { useFetch, useLive } from "../live";
import { apiUrl, engineerName, get, post } from "../api";
import type { Recommendation } from "../types";
import { Button, C, Loading, Panel, fmt, inr, pct, useToast } from "../components/ui";
import { go } from "../router";

interface Mission {
  wells: number; producing: number; oil_m3d: number; oil_bpd: number;
  value_at_stake: { key: string; label: string; inr_per_year: number; detail: string; kind: "upside" | "loss" | "risk" }[];
  total_at_stake_inr: number;
  leak_rows: { well_id: string; cause: string; oil_gap_m3d: number; inr_per_year: number }[];
  top_actions: Recommendation[];
  failures_predicted: number;
  maint_jobs: { well_id: string; reasons: string[]; do_in_days: number; failed: boolean }[];
  twin_accuracy: number | null;
  alerts_active: number;
  models: { dynacard_accuracy: number; risk_macro_f1: number; forecast_mape: number; surrogate_r2_oil: number; n_tests: number };
  priority_well: string;
  chain: { well_id: string; steam_t: number; zone_c: number; viscosity_cp: number; fillage: number; pprl_kn: number; goodman: number; oil_m3d: number; energy_bbl: number | null; sor: number | null; phase: string; limiting: string; deliv: number | null; cap: number | null };
}

const SEG_COLOR: Record<string, string> = { actions: C.s3, leak: C.s2, steam: C.s4, failure: C.s8, maint: C.s1 };

function crores(v: number) {
  return v >= 1e7 ? `₹${(v / 1e7).toFixed(2)} Cr` : `₹${(v / 1e5).toFixed(1)} L`;
}

/** Cross-section of a CSS well: pumpjack, steam line, heated zone and depth ticks (decorative; values are live elsewhere). */
function WellArt({ zone, steam }: { zone: number; steam: number }) {
  const depth = [0, 300, 600, 900, 1200];
  return (
    <svg viewBox="0 0 400 420" className="h-full w-full" role="img" aria-label="Cross-section of a steam-stimulated well with a rod pump">
      <defs>
        <radialGradient id="heatg" cx="50%" cy="50%" r="50%"><stop offset="0%" stopColor="#ffb26b" stopOpacity="0.95" /><stop offset="55%" stopColor="#f08a3c" stopOpacity="0.5" /><stop offset="100%" stopColor="#f08a3c" stopOpacity="0" /></radialGradient>
        <linearGradient id="earth" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#2a2621" /><stop offset="100%" stopColor="#1a1714" /></linearGradient>
      </defs>
      <rect x="0" y="92" width="400" height="328" fill="url(#earth)" />
      <rect x="0" y="286" width="400" height="44" fill="#4a3d2a" opacity="0.55" />
      {depth.map((m, i) => {
        const y = 92 + (m / 1200) * 234;
        return (
          <g key={m}>
            <line x1="44" x2="400" y1={y} y2={y} stroke="#48433b" strokeWidth="1" strokeDasharray={i === 0 ? undefined : "2 5"} />
            <text x="10" y={y + 4} fontSize="10" fill="#948d80" fontFamily="IBM Plex Mono, monospace">{m}</text>
          </g>
        );
      })}
      <text x="10" y="82" fontSize="9" fill="#948d80" fontFamily="IBM Plex Sans Condensed, sans-serif" letterSpacing="1">DEPTH, m</text>
      {/* wellbore + steam: x = 190, directly under the horsehead cable */}
      <line x1="190" x2="190" y1="92" y2="308" stroke="#cbc4b8" strokeWidth="3" />
      <line x1="190" x2="190" y1="96" y2="300" stroke="#f08a3c" strokeWidth="2" className="flow" />
      {/* heated zone */}
      <ellipse cx="190" cy="308" rx="112" ry="34" fill="url(#heatg)" className="glow" />
      <line x1="120" x2="260" y1="308" y2="308" stroke="#ffb26b" strokeWidth="1" strokeDasharray="3 3" opacity="0.8" />
      {/* pump barrel */}
      <rect x="185" y="262" width="10" height="26" rx="2" fill="#3987e5" opacity="0.85" />
      {/* surface pumpjack: samson post, walking beam with horsehead, counterweight, motor */}
      <line x1="30" x2="400" y1="92" y2="92" stroke="#948d80" strokeWidth="1.5" />
      <path d="M96 92 L120 40 L144 92" fill="none" stroke="#f08a3c" strokeWidth="4" strokeLinejoin="round" />
      <g className="rock">
        <path d="M52 48 L192 34" stroke="#f08a3c" strokeWidth="6" strokeLinecap="round" />
        <path d="M178 33 Q 206 36 202 62" fill="none" stroke="#f08a3c" strokeWidth="5" strokeLinecap="round" />
        <rect x="44" y="50" width="26" height="18" rx="3" fill="#24211d" stroke="#f08a3c" />
      </g>
      <line x1="190" x2="190" y1="60" y2="84" stroke="#f4efe6" strokeWidth="1.6" />
      <rect x="60" y="72" width="30" height="20" rx="3" fill="#24211d" stroke="#48433b" />
      <circle cx="75" cy="82" r="6" fill="#3987e5" opacity="0.9" />
      <rect x="178" y="82" width="24" height="10" rx="2" fill="#cbc4b8" />
      {/* annotations */}
      <g fontFamily="IBM Plex Mono, monospace" fontSize="10">
        <rect x="236" y="112" width="152" height="34" rx="6" fill="#100f0d" opacity="0.85" /><text x="246" y="127" fill="#948d80">STEAM SLUG</text><text x="246" y="140" fill="#f4efe6">{fmt(steam, 0)} t / cycle</text>
        <rect x="236" y="186" width="152" height="34" rx="6" fill="#100f0d" opacity="0.85" /><text x="246" y="201" fill="#948d80">VISCOSITY FALLS</text><text x="246" y="214" fill="#f4efe6">14,000 → ~150 cP</text>
        <rect x="30" y="352" width="152" height="34" rx="6" fill="#100f0d" opacity="0.85" /><text x="40" y="367" fill="#948d80">HEATED ZONE</text><text x="40" y="380" fill="#ffb26b">{fmt(zone, 0)} °C now</text>
        <rect x="236" y="352" width="152" height="34" rx="6" fill="#100f0d" opacity="0.85" /><text x="246" y="367" fill="#948d80">JODHPUR SANDSTONE</text><text x="246" y="380" fill="#f4efe6">17–19° API crude</text>
      </g>
    </svg>
  );
}

function ChainNode({ n, label, value, unit, sub, color }: { n: number; label: string; value: string; unit?: string; sub: string; color: string }) {
  return (
    <div className="card-flat relative min-w-0 px-3.5 py-3">
      <div className="flex items-center gap-2"><span className="flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold text-black" style={{ background: color }}>{n}</span><span className="eyebrow truncate">{label}</span></div>
      <div className="mt-1.5 flex items-baseline gap-1"><span className="mono text-[22px] font-medium leading-none">{value}</span>{unit && <span className="text-xs text-ink-3">{unit}</span>}</div>
      <div className="mt-1 truncate text-[11px] text-ink-3">{sub}</div>
    </div>
  );
}

export default function Home() {
  const { snap } = useLive();
  const toast = useToast();
  const m = useFetch(() => get<Mission>("/api/mission"), [], 6000);
  if (!m.data || !snap) return <Loading label="Loading mission control" />;
  const d = m.data;
  const stake = d.value_at_stake.filter((v) => v.inr_per_year > 0);
  const total = stake.reduce((a, v) => a + v.inr_per_year, 0) || 1;
  const ch = d.chain;

  const tiles = [
    { Icon: Layers, title: "Well twin", text: "Live well-to-surface state, prediction and the next best action for one well.", to: () => go({ page: "well", id: d.priority_well }) },
    { Icon: Boxes, title: "Field optimiser", text: "Optimises all wells together under a shared steam budget and shows the annual ₹ gain.", to: () => go({ page: "field", tab: "plan" }) },
    { Icon: Wrench, title: "Maintenance planner", text: "Predicts rod and pump failures and schedules rigs to cut downtime and cost.", to: () => go({ page: "field", tab: "maintenance" }) },
    { Icon: Gauge, title: "Optimise & what-if", text: "Coupled CSS + SRP search under your constraints; test up to three scenarios.", to: () => go({ page: "optimize", id: d.priority_well }) },
    { Icon: Database, title: "Data & replay", text: "Validate a CSV strictly (units, gaps, duplicates) and replay it through the twin.", to: () => go({ page: "data" }) },
    { Icon: FileText, title: "Shift report", text: "One-click printable field report: status, actions, alerts and maintenance.", to: () => window.open(apiUrl("/api/report"), "_blank") },
  ];

  return (
    <div className="space-y-6">
      {/* hero */}
      <section className="card overflow-hidden">
        <div className="grid items-stretch gap-0 lg:grid-cols-[1.15fr_0.85fr]">
          <div className="flex flex-col justify-center p-6 sm:p-8 lg:p-10">
            <div className="eyebrow !text-accent">SIH26120 · Oil India Limited · Baghewala heavy-oil field</div>
            <h1 className="mt-3 text-[34px] font-semibold leading-[1.1] tracking-tight sm:text-[40px]">Tune steam and pumps as one system.</h1>
            <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-ink-2">
              Baghewala crude is about 11,000 cP at 50 °C. It flows only while steam keeps it hot, and the heat fades through every cycle. Steam programmes and rod pumps are tuned
              separately today, so wells pound fluid, rods fail early and steam is wasted. This twin models the whole path from reservoir to surface, predicts the next weeks,
              and recommends steam <em>and</em> pump settings together. Engineers approve every change.
            </p>
            <div className="mt-6 flex flex-wrap gap-2.5">
              <Button onClick={() => go({ page: "well", id: d.priority_well })}>Open {d.priority_well}, the well that needs attention <ArrowRight size={15} /></Button>
              <Button variant="ghost" onClick={() => go({ page: "field", tab: "plan" })}>Optimise the whole field</Button>
            </div>
            <dl className="mt-7 grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-4">
              {[["Wells monitored", `${d.wells}`], ["Oil now", `${fmt(d.oil_bpd, 0)} bbl/d`], ["Open alerts", `${d.alerts_active}`], ["Failures predicted", `${d.failures_predicted}`]].map(([k, v]) => (
                <div key={k}><dt className="eyebrow">{k}</dt><dd className="mono mt-0.5 text-xl font-medium">{v}</dd></div>
              ))}
            </dl>
          </div>
          <div className="depthgrid relative min-h-[300px] bg-[#16130f]/70 p-3 lg:min-h-[420px]"><WellArt zone={ch.zone_c} steam={ch.steam_t} /></div>
        </div>
      </section>

      {/* value at stake */}
      <Panel title="Value at stake per year" subtitle="Live from the twin. Economics are synthetic assumptions (see Assumptions & sources)." right={<span className="mono text-2xl font-medium text-accent-2">{crores(d.total_at_stake_inr)}</span>}>
        <div className="flex h-3 overflow-hidden rounded-full bg-surface-3" role="img" aria-label="Breakdown of value at stake">
          {stake.map((v) => <div key={v.key} style={{ width: `${(v.inr_per_year / total) * 100}%`, background: SEG_COLOR[v.key] ?? C.ink3 }} title={`${v.label}: ${crores(v.inr_per_year)}`} className="transition-[width] duration-500 first:rounded-l-full last:rounded-r-full" />)}
        </div>
        <div className="mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
          {stake.map((v) => (
            <div key={v.key} className="min-w-0">
              <div className="flex items-start gap-2 text-xs text-ink-2"><span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: SEG_COLOR[v.key] ?? C.ink3 }} /><span className="leading-snug">{v.label}</span></div>
              <div className="mono mt-0.5 text-lg font-medium">{crores(v.inr_per_year)}</div>
              <div className="text-[11px] text-ink-3">{v.detail}</div>
            </div>
          ))}
        </div>
      </Panel>

      {/* method */}
      <div className="grid gap-3 md:grid-cols-4">
        {[
          ["Sense", `${d.wells} wells stream SCADA-style telemetry and dynamometer cards; a neural network reads every card.`, C.s1],
          ["Predict", `Physics twin plus ML forecast each cycle and flag rod and pump failures (${d.failures_predicted} now). The twin fits oil rate to ${d.twin_accuracy === null ? "–" : pct(d.twin_accuracy, 0)}.`, C.s3],
          ["Recommend", "A constraint-based optimiser searches steam volume, soak, VFD speed, stroke and pump depth together.", C.s2],
          ["Approve", "Nothing changes without an engineer's approval. Every decision is audited and no equipment is controlled.", C.s7],
        ].map(([t, text, col], i) => (
          <div key={t} className="relative">
            <div className="card h-full p-4">
              <div className="flex items-center gap-2.5"><span className="flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold text-black" style={{ background: col }}>{i + 1}</span><span className="text-[15px] font-semibold">{t}</span></div>
              <p className="mt-2 text-[13px] leading-relaxed text-ink-2">{text}</p>
            </div>
            {i < 3 && <ArrowRight aria-hidden size={16} className="absolute -right-3.5 top-1/2 z-10 hidden -translate-y-1/2 text-ink-3 md:block" />}
          </div>
        ))}
      </div>

      {/* coupling + actions */}
      <div className="grid gap-5 xl:grid-cols-5">
        <Panel title={`The coupling, live on ${ch.well_id}`} subtitle="One change ripples through the whole chain. A steam-only tool and a pump-only tool each see half of it." className="xl:col-span-3">
          <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3">
            <ChainNode n={1} label="Steam slug" value={fmt(ch.steam_t, 0)} unit="t" sub="CSS decision" color={C.s2} />
            <ChainNode n={2} label="Heated zone" value={fmt(ch.zone_c, 0)} unit="°C" sub="cools through the cycle" color={C.s2} />
            <ChainNode n={3} label="Viscosity" value={fmt(ch.viscosity_cp, 0)} unit="cP" sub="rises as it cools" color={C.s4} />
            <ChainNode n={4} label="Pump fillage" value={ch.phase === "production" ? pct(ch.fillage) : "–"} sub="SRP decision (speed)" color={C.s1} />
            <ChainNode n={5} label="Rod stress" value={ch.phase === "production" ? pct(ch.goodman) : "–"} unit="Goodman" sub={`peak load ${fmt(ch.pprl_kn, 0)} kN`} color={C.s7} />
            <ChainNode n={6} label="Result" value={fmt(ch.oil_m3d, 1)} unit="m³/d" sub={`${fmt(ch.energy_bbl, 0)} kWh/bbl · SOR ${fmt(ch.sor, 1)}`} color={C.s3} />
          </div>
          {ch.phase === "production" && ch.deliv !== null && ch.cap !== null && (
            <p className="mt-4 text-sm leading-relaxed text-ink-2">Right now the reservoir can deliver {fmt(ch.deliv, 1)} m³/d of liquid while the pump could lift {fmt(ch.cap, 1)} m³/d, so the limit is <b className="text-ink">{ch.limiting || "the pump"}</b>. Steam sets what the reservoir can give; the pump has to match it.</p>
          )}
          <div className="mt-3"><Button small variant="ghost" onClick={() => go({ page: "well", id: ch.well_id })}>Open this well's twin <ArrowRight size={13} /></Button></div>
        </Panel>

        <Panel title="Top actions for the engineer" subtitle="Best expected value first" className="xl:col-span-2" bodyClass="space-y-2.5">
          {d.top_actions.length ? d.top_actions.map((r) => (
            <div key={r.id} className="card-flat p-3.5">
              <div className="flex items-start justify-between gap-3"><span className="text-[13px] font-semibold leading-snug">{r.title}</span><span className="mono shrink-0 text-sm font-medium text-[#6fdc82]">+{inr(r.gain_inr_per_day)}<span className="text-[11px] text-ink-3">/d</span></span></div>
              <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-ink-3">{r.detail[0]}</p>
              <div className="mt-2.5 flex gap-2">
                <Button small variant="good" onClick={async () => { await post(`/api/recommendations/${r.id}/approve`, { by: engineerName(), note: "" }); toast({ title: "Approved", body: r.title }); m.reload(); }}><Check size={12} /> Approve</Button>
                <Button small variant="ghost" onClick={() => go({ page: "well", id: r.well_id })}>Open {r.well_id}</Button>
              </div>
            </div>
          )) : <p className="text-sm text-ink-3">No pending actions.</p>}
          {d.maint_jobs.length > 0 && (
            <div className="rounded-lg bg-surface-2 p-3 text-xs text-ink-2">
              <div className="mb-1 flex items-center gap-1.5 text-[13px] font-semibold text-ink"><CalendarClock size={14} /> Predicted maintenance</div>
              {d.maint_jobs.map((j) => <div key={j.well_id}>{j.well_id}: {j.reasons[0]}, {j.failed ? "act now" : `plan in ${fmt(j.do_in_days, 0)} d`}</div>)}
              <button className="mt-1 text-accent-2 hover:underline" onClick={() => go({ page: "field", tab: "maintenance" })}>Open the maintenance planner →</button>
            </div>
          )}
        </Panel>
      </div>

      {/* features */}
      <section aria-labelledby="do-here">
        <h2 id="do-here" className="eyebrow mb-2.5">What you can do here</h2>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {tiles.map(({ Icon, title, text, to }) => (
            <button key={title} onClick={to} className="card group flex items-start gap-3.5 p-4 text-left transition-colors hover:bg-surface-2">
              <span className="rounded-lg bg-accent/12 p-2.5 text-accent"><Icon size={18} /></span>
              <span className="min-w-0"><span className="flex items-center gap-1.5 text-[14px] font-semibold">{title} <ArrowRight size={13} className="-translate-x-1 opacity-0 transition group-hover:translate-x-0 group-hover:opacity-100" /></span><span className="mt-0.5 block text-xs leading-relaxed text-ink-3">{text}</span></span>
            </button>
          ))}
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl bg-surface px-5 py-3.5 text-xs text-ink-2">
        <span className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-ink"><ShieldCheck size={15} className="text-good" /> Evidence</span>
        <span>Dynacard CNN <b className="mono text-ink">{pct(d.models.dynacard_accuracy, 0)}</b></span>
        <span>Rod-risk macro-F1 <b className="mono text-ink">{fmt(d.models.risk_macro_f1, 2)}</b></span>
        <span>Forecast error <b className="mono text-ink">{pct(d.models.forecast_mape, 1)}</b></span>
        <span>Surrogate R² <b className="mono text-ink">{fmt(d.models.surrogate_r2_oil, 2)}</b></span>
        <span><b className="mono text-ink">{d.models.n_tests}</b> automated tests</span>
        <span className="text-warn">All on synthetic data, not field accuracy</span>
      </div>
    </div>
  );
}
