/** "Tech stack" + "twin loop" summary, static so it renders even when the API is unreachable. */

const STACK: { name: string; role: string; color: string }[] = [
  { name: "Python", role: "physics core", color: "#3987e5" },
  { name: "NumPy / SciPy", role: "numerics, least-squares", color: "#199e70" },
  { name: "PyTorch", role: "dynacard CNN", color: "#d95926" },
  { name: "XGBoost", role: "forecast, surrogate, risk", color: "#c98500" },
  { name: "pymoo", role: "NSGA-II optimiser", color: "#9085e9" },
  { name: "FastAPI", role: "REST + WebSocket", color: "#199e70" },
  { name: "MQTT", role: "sensor bus", color: "#d55181" },
  { name: "React + Three.js", role: "decision UI, 3D well", color: "#3987e5" },
];

const NOTES = [
  "Custom scratch physics core in Python/NumPy",
  "SciPy for history-matching (least squares) only",
  "PyTorch + XGBoost for diagnosis & forecasting",
  "Every value labelled: synthetic vs. field data",
];

const STEPS: { n: string; title: string; body: string; color: string }[] = [
  { n: "1", title: "Sense", body: "Telemetry + dynacards over MQTT", color: "#3987e5" },
  { n: "2", title: "Simulate", body: "Reservoir ↔ wellbore ↔ pump, solved as one fixed point", color: "#d95926" },
  { n: "3", title: "Learn", body: "Calibrate twin to data; CNN diagnoses cards; forecast P10/P50/P90", color: "#199e70" },
  { n: "4", title: "Optimise", body: "NSGA-II over steam + SPM; knapsack splits the field steam budget", color: "#9085e9" },
  { n: "5", title: "Decide", body: "Engineer approves or rejects; every action audited", color: "#c98500" },
];

export default function HowItWorks() {
  return (
    <div className="grid items-start gap-5 lg:grid-cols-2">
      <section className="rounded-xl border-2 border-s1/70 bg-surface p-5">
        <h2 className="mb-4 text-center font-cond text-lg font-semibold tracking-wide text-s1">Tech Stack</h2>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          {STACK.map((s) => (
            <div key={s.name} className="rounded-lg border border-line bg-surface-2 px-3 py-2.5 text-center">
              <div className="text-sm font-semibold" style={{ color: s.color }}>{s.name}</div>
              <div className="mt-0.5 text-[11px] leading-tight text-ink-3">{s.role}</div>
            </div>
          ))}
        </div>
        <ul className="mt-4 space-y-1 text-center font-mono text-[12px] italic text-ink-2">
          {NOTES.map((n) => <li key={n}>{n}</li>)}
        </ul>
      </section>

      <section className="rounded-xl border border-line bg-surface p-5">
        <h2 className="mb-4 font-cond text-lg font-bold uppercase tracking-wide text-ink">The twin loop (prototype)</h2>
        <div className="relative pr-9">
          <ol>
            {STEPS.map((s, i) => (
              <li key={s.n}>
                <div className="flex items-start gap-3 rounded-lg px-3 py-2 text-white shadow-sm" style={{ background: s.color }}>
                  <span className="font-mono text-sm font-bold opacity-80">{s.n}</span>
                  <span className="text-sm"><b>{s.title}</b> – {s.body}</span>
                </div>
                {i < STEPS.length - 1 && <div className="flex h-5 justify-center text-ink-3" aria-hidden><svg width="12" height="20" viewBox="0 0 12 20"><path d="M6 1v15M2 12l4 5 4-5" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg></div>}
              </li>
            ))}
          </ol>
          {/* feedback: the decision changes the plant, which is sensed again */}
          <div className="absolute bottom-[18px] right-0 top-[18px] w-7 rounded-r-lg border-y-2 border-r-2 border-ink-3" aria-hidden />
          <svg className="absolute right-[26px] top-[12px] text-ink-3" width="10" height="12" viewBox="0 0 10 12" aria-hidden><path d="M10 0L0 6l10 6z" fill="currentColor" /></svg>
          <span className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 rotate-90 whitespace-nowrap bg-surface px-1 text-[10px] uppercase tracking-wider text-ink-3">repeat every tick</span>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 text-center text-xs">
          <div className="rounded-lg border border-line bg-surface-2 px-2 py-2"><b>Diagnosis</b> – dynacard CNN</div>
          <div className="rounded-lg border border-line bg-surface-2 px-2 py-2"><b>Kernel</b> – coupled physics fixed point</div>
        </div>
        <p className="mt-3 text-[11px] text-ink-3">
          Shipped: physics twin, ML models, joint optimiser, human approval. Planned: live SCADA feed, calibration on OIL field data.
        </p>
      </section>
    </div>
  );
}
