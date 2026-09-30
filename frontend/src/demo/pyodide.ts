/** Runs the backend's what-if physics (backend/app/whatif.py + app/physics) in the browser with Pyodide,
 *  inside a Web Worker, so the static demo's what-if sliders give real simulation results, not recordings. */
const PY_ROOT = new URL(`${import.meta.env.BASE_URL}demo/py/`, location.href).href;

type Reply = { id: number; ok: boolean; out?: string | null; error?: string };

let worker: Worker | null = null;
let seq = 0;
let booted = false;
let booting: Promise<void> | null = null;
const pending = new Map<number, { resolve: (v: string | null) => void; reject: (e: Error) => void }>();

function call(kind: "boot" | "simulate", ctx?: string, body?: string): Promise<string | null> {
  if (!worker) {
    worker = new Worker(new URL("./pyworker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<Reply>) => {
      const p = pending.get(e.data.id);
      if (!p) return;
      pending.delete(e.data.id);
      if (e.data.ok) p.resolve(e.data.out ?? null);
      else p.reject(new Error(e.data.error));
    };
  }
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker!.postMessage({ id, kind, pyRoot: PY_ROOT, ctx, body });
  });
}

/** True once the in-browser physics is loaded (the first load downloads ~30 MB and takes 10-30 s). */
export const physicsReady = () => booted;

/** Start loading in the background (called when the Optimise section opens). */
export function preloadPhysics(): Promise<void> {
  booting ??= call("boot").then(() => { booted = true; }, (e) => { booting = null; throw e; });
  return booting;
}

export async function simulateInBrowser(ctx: unknown, body: unknown): Promise<unknown> {
  try {
    await preloadPhysics();
  } catch {
    const err = new Error("Could not load the in-browser physics engine (needs internet access to cdn.jsdelivr.net).") as Error & { status: number };
    err.status = 503;
    throw err;
  }
  try {
    return JSON.parse((await call("simulate", JSON.stringify(ctx), JSON.stringify(body)))!);
  } catch (e) {
    const msg = String((e as Error).message ?? e);
    const valueError = msg.match(/ValueError: (.*)/);
    const err = new Error(valueError ? valueError[1] : "The in-browser simulation failed.") as Error & { status: number };
    err.status = valueError ? 400 : 500;
    throw err;
  }
}
