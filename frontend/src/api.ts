/** Backend origin. Empty (the default) means same origin, which is how the bundled app runs.
 *  Set VITE_BACKEND_URL at build time when the UI is hosted separately from the API (for example on Vercel). */
import { DEMO, DemoError, demoGet, demoPost, demoPostForm, demoRawUrl } from "./demo/mock";

const BASE = String(import.meta.env.VITE_BACKEND_URL ?? "").replace(/\/+$/, "");
export const apiUrl = (path: string) => (DEMO ? demoRawUrl(path) : `${BASE}${path}`);
/** WebSocket URL of the live stream, derived from the same setting. */
export function wsUrl(): string {
  if (BASE) return `${BASE.replace(/^http/, "ws")}/ws`;
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
      else if (Array.isArray(body.detail)) msg = body.detail.map((d: { msg: string; loc?: string[] }) => `${(d.loc ?? []).slice(-1)[0] ?? ""}: ${d.msg}`).join("; ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

/** In the static demo, requests are answered by the recorded run (src/demo/mock.ts). */
function demo<T>(p: Promise<unknown>): Promise<T> {
  return p.then((v) => v as T, (e: Error & { status?: number }) => {
    throw new ApiError(e instanceof DemoError || e.status ? (e.status ?? 500) : 500, e.message);
  });
}

export function get<T>(path: string): Promise<T> {
  if (DEMO) return demo<T>(demoGet(path));
  return fetch(apiUrl(path)).then((r) => handle<T>(r));
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  if (DEMO) return demo<T>(demoPost(path, (body ?? {}) as Record<string, unknown>));
  return fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => handle<T>(r));
}

export function postForm<T>(path: string, form: FormData): Promise<T> {
  if (DEMO) return demo<T>(demoPostForm(path, form));
  return fetch(apiUrl(path), { method: "POST", body: form }).then((r) => handle<T>(r));
}

export const ENGINEER_KEY = "twin.engineer";
export function engineerName(): string {
  try {
    return localStorage.getItem(ENGINEER_KEY) || "Field Engineer";
  } catch {
    return "Field Engineer";
  }
}
export function setEngineerName(name: string) {
  try {
    localStorage.setItem(ENGINEER_KEY, name);
  } catch {
    /* storage unavailable */
  }
}
