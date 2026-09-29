import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { Snapshot } from "./types";
import { wsUrl } from "./api";

interface LiveState {
  snap: Snapshot | null;
  connected: boolean;
}

const LiveCtx = createContext<LiveState>({ snap: null, connected: false });

/** One WebSocket for the whole app; reconnects with back-off. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<LiveState>({ snap: null, connected: false });
  const retry = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      ws = new WebSocket(wsUrl());
      ws.onopen = () => {
        retry.current = 0;
        setState((s) => ({ ...s, connected: true }));
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "tick") setState({ snap: msg as Snapshot, connected: true });
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (closed) return;
        const delay = Math.min(1000 * 2 ** retry.current, 10000);
        retry.current += 1;
        timer = window.setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return <LiveCtx.Provider value={state}>{children}</LiveCtx.Provider>;
}

export function useLive() {
  return useContext(LiveCtx);
}

/** Re-run a fetch whenever `deps` change and optionally on an interval. */
export function useFetch<T>(fn: () => Promise<T>, deps: unknown[], intervalMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    const run = () =>
      fn()
        .then((d) => {
          if (alive) {
            setData(d);
            setError(null);
          }
        })
        .catch((e) => alive && setError(String(e.message ?? e)))
        .finally(() => alive && setLoading(false));
    setLoading(true);
    run();
    const id = intervalMs ? window.setInterval(run, intervalMs) : undefined;
    return () => {
      alive = false;
      if (id) window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);
  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
