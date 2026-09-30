import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Activity, Bell, Database, Factory, FileText, Gauge, LayoutDashboard, Pause, Play, UserRound } from "lucide-react";
import { useLive } from "./live";
import { href, useRoute, type Route } from "./router";
import { apiUrl, engineerName, post, setEngineerName } from "./api";
import { Loading, Segmented, simDate } from "./components/ui";
import Logo from "./components/Logo";
import ErrorBoundary from "./components/ErrorBoundary";
import HowItWorks from "./components/HowItWorks";
import type { Snapshot } from "./types";

// pages are split into their own chunks; the 3D scene (three.js) only loads with the well twin
const Home = lazy(() => import("./pages/Home"));
const WellTwin = lazy(() => import("./pages/WellTwin"));
const Field = lazy(() => import("./pages/Field"));
const Optimize = lazy(() => import("./pages/Optimize"));
const Alerts = lazy(() => import("./pages/Alerts"));
const DataModels = lazy(() => import("./pages/DataModels"));

const NAV: { page: Route["page"]; label: string; short: string; hint: string; Icon: typeof Factory }[] = [
  { page: "home", label: "Mission control", short: "Mission", hint: "the project at a glance", Icon: LayoutDashboard },
  { page: "well", label: "Well twin", short: "Twin", hint: "state and next action", Icon: Activity },
  { page: "field", label: "Field", short: "Field", hint: "steam, optimiser, rigs", Icon: Factory },
  { page: "optimize", label: "Optimise & what-if", short: "Optimise", hint: "CSS + SRP together", Icon: Gauge },
  { page: "actions", label: "Actions", short: "Actions", hint: "approvals, alerts, audit", Icon: Bell },
  { page: "data", label: "Data & models", short: "Data", hint: "upload, models, sources", Icon: Database },
];

/** The well an engineer should look at first: a down/critical well, else the one with most open alerts. */
function priorityWell(snap: Snapshot | null): string {
  if (!snap) return "BGW-001";
  const rank = { down: 3, critical: 3, warning: 1, ok: 0 } as const;
  return [...snap.wells].sort((a, b) => rank[b.status] - rank[a.status] || b.alerts - a.alerts)[0]?.id ?? "BGW-001";
}

export default function App() {
  const route = useRoute();
  const { snap, connected } = useLive();
  const [name, setName] = useState(engineerName());
  const lastWell = useRef<string | null>(null);
  const main = useRef<HTMLElement>(null);
  if ((route.page === "well" || route.page === "optimize") && route.id) lastWell.current = route.id;
  const wellId = (route.page === "well" || route.page === "optimize") && route.id ? route.id : lastWell.current ?? priorityWell(snap);
  const current = NAV.find((n) => n.page === route.page) ?? NAV[0];
  const routeKey = `${route.page}/${"id" in route ? route.id ?? "" : ""}/${"tab" in route ? route.tab ?? "" : ""}`;

  useEffect(() => { document.title = `${current.label} · Baghewala Twin`; }, [current.label]);
  useEffect(() => { main.current?.scrollTo({ top: 0 }); }, [route.page]);

  const navHref = (p: Route["page"]) => (p === "well" || p === "optimize" ? href({ page: p, id: wellId } as Route) : href({ page: p } as Route));
  const setSpeed = (h: number) => post("/api/sim/control", { hours_per_tick: h });

  return (
    <div className="flex h-full">
      {/* instrument rail */}
      <aside className="hidden w-[236px] shrink-0 flex-col bg-surface lg:flex">
        <a href="#/" className="flex items-center gap-3 px-5 pb-4 pt-5">
          <Logo />
          <span className="leading-tight">
            <span className="block text-[15px] font-semibold tracking-tight">Baghewala</span>
            <span className="eyebrow block">digital twin</span>
          </span>
        </a>
        <div className="logrule mx-5 mb-2" aria-hidden />
        <nav aria-label="Main" className="flex-1 space-y-0.5 px-3 py-2">
          {NAV.map(({ page, label, hint, Icon }) => {
            const active = route.page === page;
            const badge = page === "actions" && snap ? snap.kpis.pending_recommendations : 0;
            return (
              <a key={page} href={navHref(page)} aria-current={active ? "page" : undefined}
                className={`group flex items-center gap-3 rounded-lg px-2.5 py-2 transition-colors ${active ? "bg-surface-3" : "hover:bg-surface-2"}`}>
                <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors ${active ? "bg-accent/15 text-accent" : "bg-surface-2 text-ink-3 group-hover:text-ink-2"}`}><Icon size={16} /></span>
                <span className="min-w-0 flex-1 leading-tight">
                  <span className={`block text-[13px] font-medium ${active ? "text-ink" : "text-ink-2"}`}>{label}</span>
                  <span className="block truncate text-[11px] text-ink-3">{hint}</span>
                </span>
                {badge > 0 && <span className="num rounded-md bg-accent/15 px-1.5 text-[11px] font-medium text-accent-2">{badge}</span>}
              </a>
            );
          })}
        </nav>
        <div className="space-y-2 px-4 pb-4 pt-3 text-xs text-ink-3">
          <label className="flex items-center gap-2 rounded-lg bg-surface-2 px-2.5 py-1.5 ring-1 ring-inset ring-line focus-within:ring-accent">
            <UserRound size={14} aria-hidden />
            <input id="engineer" type="text" value={name} onChange={(e) => { setName(e.target.value); setEngineerName(e.target.value); }}
              className="w-full border-0 bg-transparent p-0 text-[13px] text-ink outline-none" aria-label="Engineer name, recorded with approvals" style={{ border: 0, background: "transparent" }} />
          </label>
          <p className="leading-snug">Oil India Limited · SIH26120<br />Field data are synthetic, calibrated to published Baghewala properties.</p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* status bar */}
        <header className="flex flex-wrap items-center gap-x-4 gap-y-2 bg-surface/80 px-4 py-2.5 backdrop-blur lg:px-8">
          <a href="#/" className="flex items-center gap-2 lg:hidden"><Logo size={26} /><span className="text-sm font-semibold">Baghewala</span></a>
          <h2 className="hidden text-[13px] font-medium text-ink-2 lg:block">{current.label}</h2>
          <span className="inline-flex items-center gap-2 rounded-full bg-surface-2 px-2.5 py-1 text-xs ring-1 ring-inset ring-line" role="status">
            <span className={`h-2 w-2 rounded-full ${connected ? "live-dot bg-good" : "bg-crit"}`} />
            <span className="font-medium text-ink-2">{connected ? "Live" : "Reconnecting"}</span>
            <span className="mono text-ink">{snap ? simDate(snap.sim_time) : "–"}</span>
          </span>
          <span title="Values are generated by a demonstration simulator calibrated to published Baghewala properties. They are not Oil India measurements and must not be used as field operating instructions."
            className="inline-flex items-center gap-1.5 rounded-full bg-warn/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-warn ring-1 ring-inset ring-warn/40">
            <span className="h-1.5 w-1.5 rounded-full bg-warn" />
            <span className="hidden sm:inline">Synthetic demo data · not live Oil India data</span>
            <span className="sm:hidden">Synthetic data</span>
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-2 text-xs text-ink-3">
            <span className="hidden md:inline">Sim speed</span>
            <Segmented label="Simulated hours per second" value={snap?.hours_per_tick ?? 2} onChange={setSpeed}
              options={[{ value: 1, label: "1h" }, { value: 2, label: "2h" }, { value: 6, label: "6h" }, { value: 12, label: "12h" }]} />
            <button onClick={() => post("/api/sim/control", { paused: !snap?.paused })} aria-label={snap?.paused ? "Resume simulation" : "Pause simulation"}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-surface-2 px-2.5 text-ink-2 ring-1 ring-inset ring-line hover:bg-surface-3 hover:text-ink">
              {snap?.paused ? <><Play size={13} /> Resume</> : <><Pause size={13} /> Pause</>}
            </button>
            <a href={apiUrl("/api/report")} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-surface-2 px-2.5 text-ink-2 ring-1 ring-inset ring-line hover:bg-surface-3 hover:text-ink"><FileText size={13} /> Report</a>
          </div>
        </header>

        <main ref={main} className="min-h-0 flex-1 overflow-y-auto pb-24 lg:pb-8">
          <div className="mx-auto w-full max-w-[1480px] px-4 py-6 lg:px-8">
            <ErrorBoundary resetKey={routeKey}>
              <Suspense fallback={<Loading />}>
                {!snap ? (
                  <div className="space-y-5">
                    <div>
                      <h1 className="text-xl font-semibold">Digital twin for CSS + SRP optimisation · Baghewala heavy-oil field</h1>
                      <p className="mt-1 text-sm text-ink-3">SIH26120 · Oil India Limited · one twin that couples steam injection, the reservoir, the wellbore and the sucker-rod pump, and recommends settings an engineer approves.</p>
                    </div>
                    <div className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink-2">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-s4" aria-hidden />
                      Connecting to the live twin… Live pages appear once the backend answers. If it is not running, start it locally with <code className="font-mono text-ink">start.ps1</code>.
                    </div>
                    <HowItWorks />
                  </div>
                ) : (
                  <div key={routeKey} className="rise">
                    {route.page === "home" ? <Home />
                      : route.page === "field" ? <Field tab={route.tab ?? "overview"} />
                      : route.page === "well" ? <WellTwin id={route.id ?? wellId} />
                      : route.page === "optimize" ? <Optimize id={route.id ?? wellId} tab={route.tab ?? "optimize"} />
                      : route.page === "actions" ? <Alerts />
                      : <DataModels tab={route.tab ?? "replay"} />}
                  </div>
                )}
              </Suspense>
            </ErrorBoundary>
          </div>
        </main>

        {/* small screens: bottom tab bar */}
        <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-40 flex bg-surface/95 pb-[env(safe-area-inset-bottom,0px)] backdrop-blur lg:hidden">
          {NAV.map(({ page, short, Icon }) => {
            const active = route.page === page;
            return (
              <a key={page} href={navHref(page)} aria-current={active ? "page" : undefined} className={`flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] ${active ? "text-accent" : "text-ink-3"}`}>
                <Icon size={18} />{short}
              </a>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
