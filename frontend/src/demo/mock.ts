/** Static demo: serves a recorded run of the real twin so the whole UI works without a backend (e.g. on Vercel).
 *  The live stream is replayed, page data comes from the recording, decisions (approve, reject, acknowledge,
 *  schedule, VFD) update state in the browser, and what-if runs the real physics in the browser (Pyodide).
 *  Recorded by backend/scripts/record_demo.py. */
import type { Alert, Recommendation, Snapshot } from "../types";

export const DEMO = import.meta.env.MODE === "demo" || import.meta.env.VITE_DEMO === "1";
const ROOT = `${import.meta.env.BASE_URL}demo/`;

export class DemoError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const LIVE_ONLY = "needs the live twin. Run the project locally (start.ps1) to use it.";
const OPT_DEFAULTS = {
  pop: 80, gens: 60, trials: 120,
  limits: { min_fillage: 0.6, max_goodman: 0.95, max_risk: 0.35, max_steam_t: 2000, max_motor_kw: 37 },
  weights: { oil: 3, sor: 1, energy: 0.5, risk: 0.5 },
};

type Json = Record<string, unknown>;
interface AuditRow { time: string; sim_time: string; actor: string; action: string; well_id: string | null; detail: string }
interface Recorded { status: number; body: Json; submit?: { status: number; body: Json; recs: Recommendation[] } }

interface State {
  api: Record<string, unknown>;
  actions: Record<string, unknown>;
  stream: Snapshot[];
  recs: Recommendation[];
  alerts: Alert[];
  audit: AuditRow[];
  plan: Json;
  lastOpt: Record<string, string>;
}

const fetchJson = (f: string) => fetch(ROOT + f).then((r) => { if (!r.ok) throw new Error(`demo data missing: ${f}`); return r.json(); });

let loading: Promise<State> | null = null;
let actionsLoading: Promise<void> | null = null;
/** Recorded action results (optimiser, field plan, ...) are only fetched when a button needs them. */
async function withActions(): Promise<State> {
  const st = await load();
  actionsLoading ??= fetchJson("actions.json").then((a) => { st.actions = a; });
  await actionsLoading.catch((e) => { actionsLoading = null; throw e; });
  return st;
}
function load(): Promise<State> {
  loading ??= (async () => {
    const [api, stream] = await Promise.all([fetchJson("api.json"), fetchJson("stream.json")]);
    return {
      api, stream, actions: {},
      recs: structuredClone(api["GET /api/recommendations"]) as Recommendation[],
      alerts: structuredClone(api["GET /api/alerts"]) as Alert[],
      audit: structuredClone(api["GET /api/audit?limit=300"]) as AuditRow[],
      plan: structuredClone(api["GET /api/field/plan"]) as Json,
      lastOpt: {},
    };
  })();
  return loading;
}

/* ------------------------------------------------------------------ live stream replay */

const player = { i: 0, paused: false, hours: 2, timer: 0 as number | undefined, subs: new Set<(s: Snapshot) => void>() };
let current: Snapshot | null = null;

function emit(st: State) {
  const s = st.stream[player.i];
  current = { ...s, paused: player.paused, hours_per_tick: player.hours, bus: "recorded run" };
  player.subs.forEach((f) => f(current!));
}
function schedule(st: State) {
  window.clearTimeout(player.timer);
  if (player.paused || !player.subs.size) return;
  // the recording advanced 2 sim-hours per frame; faster speeds play frames faster
  player.timer = window.setTimeout(() => {
    player.i = (player.i + 1) % st.stream.length;
    emit(st);
    schedule(st);
  }, 2000 / player.hours);
}

/** Replays the recorded WebSocket stream. Returns an unsubscribe function. */
export function subscribe(onSnap: (s: Snapshot) => void, onReady: (ok: boolean) => void): () => void {
  let alive = true;
  player.subs.add(onSnap);
  load().then((st) => {
    if (!alive) return;
    onReady(true);
    emit(st);
    schedule(st);
  }).catch(() => alive && onReady(false));
  return () => {
    alive = false;
    player.subs.delete(onSnap);
    if (!player.subs.size) window.clearTimeout(player.timer);
  };
}

const simIso = () => current?.sim_time ?? "";
function audit(st: State, actor: string, action: string, well: string | null, detail: Json) {
  st.audit.unshift({ time: new Date().toISOString().slice(0, 19), sim_time: simIso(), actor: actor || "engineer", action, well_id: well, detail: JSON.stringify(detail) });
}
function upsertRecs(st: State, recs: Recommendation[]) {
  for (const r of recs) {
    const i = st.recs.findIndex((x) => x.id === r.id);
    if (i >= 0) st.recs[i] = structuredClone(r);
    else st.recs.unshift(structuredClone(r));
  }
}
const wait = (ms: number) => new Promise((r) => window.setTimeout(r, ms));
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const clone = <T,>(v: T): T => structuredClone(v);

/* ------------------------------------------------------------------ request handling */

export async function demoGet(path: string): Promise<unknown> {
  const st = await load();
  const [p, q = ""] = path.split("?");
  const qs = new URLSearchParams(q);
  if (p === "/api/recommendations") return clone(st.recs.filter((r) => !qs.get("status") || r.status === qs.get("status")));
  if (p === "/api/alerts") return clone(st.alerts.filter((a) => !qs.get("status") || a.status === qs.get("status")));
  if (p === "/api/audit") return clone(st.audit.slice(0, Number(qs.get("limit") ?? 200)));
  if (p === "/api/field/plan") return clone(st.plan);
  if (p === "/api/health") return { status: "ok", sim_time: simIso(), mode: "recorded demo" };
  const hit = st.api["GET " + path];
  if (hit === undefined) throw new DemoError(404, "This view is not part of the recorded demo.");
  return clone(hit);
}

export async function demoPost(path: string, body: Json = {}): Promise<unknown> {
  const st = /calibrate|optimize|field\/plan|simulate|data\/replay/.test(path) ? await withActions() : await load();
  const by = String(body.by ?? "engineer");
  let m: RegExpMatchArray | null;

  if (path === "/api/sim/control") {
    if ("paused" in body) player.paused = Boolean(body.paused);
    if ("hours_per_tick" in body) player.hours = Math.min(12, Math.max(0.25, Number(body.hours_per_tick)));
    emit(st);
    schedule(st);
    return { paused: player.paused, hours_per_tick: player.hours };
  }
  if ((m = path.match(/^\/api\/recommendations\/([^/]+)\/(approve|reject)$/))) {
    const r = st.recs.find((x) => x.id === m![1]);
    if (!r) throw new DemoError(404, "unknown recommendation");
    if (r.status !== "pending") throw new DemoError(409, `recommendation is ${r.status}`);
    Object.assign(r, { status: m[2] === "approve" ? "approved" : "rejected", decided_by: by, decided_at: simIso(), note: String(body.note ?? "") });
    audit(st, by, m[2], r.well_id, { recommendation: r.id, type: r.type, title: r.title, note: body.note ?? "" });
    return clone(r);
  }
  if ((m = path.match(/^\/api\/alerts\/([^/]+)\/ack$/))) {
    const a = st.alerts.find((x) => x.id === m![1]);
    if (!a) throw new DemoError(404, "unknown alert");
    if (a.status === "active") {
      Object.assign(a, { status: "acknowledged", ack_by: by });
      audit(st, by, "acknowledge_alert", a.well_id, { alert: a.id, title: a.title });
    }
    return clone(a);
  }
  if ((m = path.match(/^\/api\/maintenance\/([^/]+)\/schedule$/))) {
    audit(st, by, "schedule_workover", m[1], { do_in_days: body.do_in_days, reasons: body.reasons });
    return { ok: true };
  }
  if ((m = path.match(/^\/api\/wells\/([^/]+)\/vfd$/))) {
    const d = st.api[`GET /api/wells/${m[1]}`] as { vfd: { enabled: boolean; spm: number | null }; summary: { phase: string } } | undefined;
    if (!d) throw new DemoError(404, `unknown well ${m[1]}`);
    if (body.enabled && d.summary.phase !== "production") throw new DemoError(400, "closed-loop VFD can only be enabled on a producing well");
    d.vfd.enabled = Boolean(body.enabled);
    audit(st, by, "vfd_closed_loop_" + (body.enabled ? "enabled" : "disabled"), m[1], {});
    return { enabled: d.vfd.enabled, spm: d.vfd.spm };
  }
  if ((m = path.match(/^\/api\/wells\/([^/]+)\/fault$/))) throw new DemoError(501, `Fault injection ${LIVE_ONLY}`);
  if ((m = path.match(/^\/api\/wells\/([^/]+)\/calibrate$/))) {
    const rec = st.actions[`calibrate ${m[1]}`] as Recorded | undefined;
    if (!rec) throw new DemoError(404, `unknown well ${m[1]}`);
    await wait(900);
    if (rec.status !== 200) throw new DemoError(rec.status, String(rec.body.detail ?? "calibration failed"));
    audit(st, "engineer", "calibrate", m[1], { params: (rec.body as { params?: unknown }).params });
    return clone(rec.body);
  }
  if ((m = path.match(/^\/api\/wells\/([^/]+)\/optimize$/))) {
    const method = String(body.method ?? "nsga2");
    const { method: _m, ...rest } = body;
    void _m;
    if (!same(rest, OPT_DEFAULTS)) throw new DemoError(501, `The recorded demo has optimiser runs for the default limits and weights only. Reset them to try it; custom settings ${LIVE_ONLY}`);
    const rec = st.actions[`optimize ${m[1]} ${method}`] as Recorded | undefined;
    if (!rec) throw new DemoError(404, `unknown well ${m[1]}`);
    await wait(1500);
    if (rec.status !== 200) throw new DemoError(rec.status, String(rec.body.detail ?? "optimiser failed"));
    st.lastOpt[m[1]] = method;
    return clone(rec.body);
  }
  if ((m = path.match(/^\/api\/wells\/([^/]+)\/optimize\/submit$/))) {
    const rec = st.actions[`optimize ${m[1]} ${st.lastOpt[m[1]]}`] as Recorded | undefined;
    if (!rec?.submit) throw new DemoError(400, "run the optimiser first");
    upsertRecs(st, rec.submit.recs);
    audit(st, by, "submit_design", m[1], { source: "optimizer" });
    return clone(rec.submit.body);
  }
  if (path === "/api/field/plan") {
    if (st.plan.status === "running") throw new DemoError(409, "a field plan is already running");
    const pct = Math.round(Number(body.budget_pct ?? 100));
    const done = (st.actions["fieldplan realloc"] as Record<string, Json>)[String(Math.min(120, Math.max(60, pct)))];
    st.plan = { status: "running", progress: 0, results: [] };
    audit(st, "engineer", "field_plan_started", null, { budget_pct: pct });
    (async () => {
      for (let k = 1; k <= 10; k++) { await wait(500); st.plan = { status: "running", progress: k / 10, results: [] }; }
      st.plan = clone(done);
    })();
    return { status: "running" };
  }
  if (path === "/api/field/plan/reallocate") {
    if (st.plan.status !== "done") throw new DemoError(400, "run the field plan first");
    const pct = Math.min(120, Math.max(60, Math.round(Number(body.budget_pct ?? 100))));
    st.plan = clone((st.actions["fieldplan realloc"] as Record<string, Json>)[String(pct)]);
    return clone(st.plan);
  }
  if (path === "/api/field/plan/submit") {
    if (st.plan.status !== "done") throw new DemoError(400, "run the field plan first");
    const rec = st.actions["fieldplan submit"] as { body: Json; recs: Recommendation[] };
    upsertRecs(st, rec.recs);
    audit(st, by, "submit_field_plan", null, {});
    return clone(rec.body);
  }
  if (path === "/api/simulate") {
    const wid = String(body.well_id);
    const ctx = st.actions["ctx " + wid];
    if (!ctx) throw new DemoError(404, `unknown well ${wid}`);
    const { simulateInBrowser } = await import("./pyodide");
    const out = (await simulateInBrowser(ctx, body)) as Json;
    return { ...out, sim_time: simIso(), scenario_id: null };
  }
  if (path === "/api/data/replay") {
    const v = st.actions["validate sample"] as { token: string };
    const rep = (st.actions["replay sample"] as Record<string, unknown>)[String(body.well_id)];
    if (body.token !== v.token || !rep) throw new DemoError(400, "unknown upload token");
    await wait(600);
    return clone(rep);
  }
  throw new DemoError(501, `This action ${LIVE_ONLY}`);
}

export async function demoPostForm(path: string, form: FormData): Promise<unknown> {
  const st = await withActions();
  const f = form.get("file");
  if (path === "/api/data/validate") {
    if (f instanceof File && f.name === "BGW-001_synthetic_sample.csv") { await wait(500); return clone(st.actions["validate sample"]); }
    throw new DemoError(501, `The recorded demo can validate the built-in synthetic sample ("Use a synthetic sample"). Checking your own files ${LIVE_ONLY}`);
  }
  throw new DemoError(501, `Uploading your own data ${LIVE_ONLY}`);
}

/** Static URL of a recorded file download (report, CSVs). Mirrors raw_name() in record_demo.py. */
export function demoRawUrl(path: string): string {
  const name = path.replace(/^\/+|\/+$/g, "").replace(/[^A-Za-z0-9._-]+/g, "_");
  const ext = path.startsWith("/api/report") ? ".html" : path.startsWith("/api/sample-csv") ? ".csv" : "";
  return `${ROOT}raw/${name}${ext}`;
}
