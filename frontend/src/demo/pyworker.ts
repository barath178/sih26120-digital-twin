/// <reference lib="webworker" />
/** Web Worker that hosts Pyodide, so loading NumPy/SciPy and running the physics never blocks the page. */
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/";

interface Py {
  FS: { mkdirTree(p: string): void; writeFile(p: string, d: string): void };
  loadPackage(names: string[]): Promise<void>;
  runPython(code: string): unknown;
  globals: { get(name: string): (a: string, b: string) => string };
}

let ready: Promise<Py> | null = null;

function boot(pyRoot: string): Promise<Py> {
  ready ??= (async () => {
    const mod = (await import(/* @vite-ignore */ `${PYODIDE}pyodide.mjs`)) as { loadPyodide(o: { indexURL: string }): Promise<Py> };
    const py = await mod.loadPyodide({ indexURL: PYODIDE });
    const files = (await fetch(pyRoot + "files.json").then((r) => r.json())) as string[];
    const [, sources] = await Promise.all([
      py.loadPackage(["numpy", "scipy"]),
      Promise.all(files.map((f) => fetch(pyRoot + f).then((r) => r.text()))),
    ]);
    const home = "/home/pyodide/";
    files.forEach((f, i) => {
      py.FS.mkdirTree(home + f.split("/").slice(0, -1).join("/"));
      py.FS.writeFile(home + f, sources[i]);
    });
    py.runPython(`
import json, sys
sys.path.insert(0, "/home/pyodide")
from app import whatif
def run_whatif(ctx, body):
    return json.dumps(whatif.simulate_json(json.loads(ctx), json.loads(body)))
`);
    return py;
  })();
  ready.catch(() => { ready = null; });
  return ready;
}

self.onmessage = async (e: MessageEvent<{ id: number; kind: "boot" | "simulate"; pyRoot: string; ctx?: string; body?: string }>) => {
  const { id, kind, pyRoot, ctx, body } = e.data;
  try {
    const py = await boot(pyRoot);
    const out = kind === "simulate" ? py.globals.get("run_whatif")(ctx!, body!) : null;
    self.postMessage({ id, ok: true, out });
  } catch (err) {
    self.postMessage({ id, ok: false, error: String((err as Error).message ?? err) });
  }
};
