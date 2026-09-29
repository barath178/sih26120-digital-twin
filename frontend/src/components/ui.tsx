import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, PowerOff, X } from "lucide-react";
import type { Phase, Severity } from "../types";

/* ------------------------------------------------------------------ palette (roles; series colours are the validated categorical order) */
export const C = {
  s1: "#3987e5",
  s2: "#d95926",
  s3: "#199e70",
  s4: "#c98500",
  s5: "#d55181",
  s6: "#008300",
  s7: "#9085e9",
  s8: "#e66767",
  ink: "#f4efe6",
  ink2: "#cbc4b8",
  ink3: "#948d80",
  line: "#36322c",
  surface: "#1c1a17",
  surface2: "#24211d",
  accent: "#f08a3c",
  good: "#2fb344",
  warn: "#f5b82e",
  serious: "#ec835a",
  crit: "#e5484d",
};

export const PHASE_COLOR: Record<Phase, string> = { production: C.s1, injection: C.s2, soak: C.s3 };
export const PHASE_LABEL: Record<Phase, string> = { production: "Production", injection: "Steam injection", soak: "Soak" };

/* ------------------------------------------------------------------ formatting */
export function fmt(v: number | null | undefined, nd = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return v.toLocaleString("en-IN", { minimumFractionDigits: nd, maximumFractionDigits: nd });
}
export function inr(v: number | null | undefined, compact = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const sign = v < 0 ? "−" : "";
  const a = Math.abs(v);
  if (compact && a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (compact && a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  return `${sign}₹${a.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}
export function pct(v: number | null | undefined, nd = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return `${(v * 100).toFixed(nd)}%`;
}
export function simDate(iso: string | undefined) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}
export function titleCase(s: string) {
  return s.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}

/* ------------------------------------------------------------------ layout */
export function PageHeader({ title, subtitle, actions, eyebrow }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <header className="rise flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
        <h1 className="text-[22px] font-semibold leading-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-[13px] leading-relaxed text-ink-3">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Panel({ title, subtitle, right, children, className = "", bodyClass = "" }: {
  title?: ReactNode; subtitle?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string;
}) {
  const flush = /(^|\s)p-0(\s|$)/.test(bodyClass);
  const hasHead = Boolean(title || right);
  const pad = flush ? "" : hasHead ? "px-5 pb-5 pt-1" : "p-5";
  return (
    <section className={`card min-w-0 ${className}`}>
      {hasHead && (
        <header className="flex items-start justify-between gap-3 px-5 pb-2 pt-4">
          <div className="min-w-0">
            {title && <h3 className="text-[14px] font-semibold leading-snug text-ink">{title}</h3>}
            {subtitle && <p className="mt-0.5 text-xs leading-relaxed text-ink-3">{subtitle}</p>}
          </div>
          {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={`${pad} ${bodyClass}`}>{children}</div>
    </section>
  );
}

/** Line + area sparkline with an emphasised end point. */
export function Sparkline({ data, color = C.s1, width = 120, height = 30 }: { data: (number | null | undefined)[]; color?: string; width?: number; height?: number }) {
  const gid = useId().replace(/:/g, "");
  const pts = data.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (pts.length < 2) return <div style={{ height }} />;
  const lo = Math.min(...pts);
  const hi = Math.max(...pts);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (pts.length - 1)) * (width - 6) + 3;
  const y = (v: number) => height - 4 - ((v - lo) / span) * (height - 8);
  const line = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1).toFixed(1)},${height} L${x(0).toFixed(1)},${height} Z`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" aria-hidden className="overflow-visible">
      <defs>
        <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.28" /><stop offset="100%" stopColor={color} stopOpacity="0" /></linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1])} r="2.6" fill={color} stroke={C.surface} strokeWidth="1.5" />
    </svg>
  );
}

/** Gauge-style readout: caps label, mono value, unit, optional trend line. */
export function Stat({ label, value, unit, sub, accent, spark, sparkColor, className = "" }: {
  label: ReactNode; value: ReactNode; unit?: string; sub?: ReactNode; accent?: string; spark?: (number | null | undefined)[]; sparkColor?: string; className?: string;
}) {
  return (
    <div className={`card flex min-w-0 flex-col gap-1 px-4 py-3.5 ${className}`}>
      <div className="eyebrow flex items-center gap-1.5 whitespace-nowrap">
        {accent && <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: accent }} />}
        <span className="truncate">{label}</span>
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="mono text-[26px] font-medium leading-none tracking-tight text-ink">{value}</span>
        {unit && <span className="text-xs text-ink-3">{unit}</span>}
      </div>
      {spark && <div className="mt-1"><Sparkline data={spark} color={sparkColor ?? accent ?? C.s1} height={28} /></div>}
      {sub && <div className="truncate text-xs text-ink-3">{sub}</div>}
    </div>
  );
}

export function KV({ k, v, unit }: { k: ReactNode; v: ReactNode; unit?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/40 py-1.5 text-[13px] last:border-b-0">
      <span className="text-ink-3">{k}</span>
      <span className="num text-right text-ink">
        {v}
        {unit && <span className="ml-1 text-xs text-ink-3">{unit}</span>}
      </span>
    </div>
  );
}

export function Button({ children, onClick, variant = "primary", disabled, type = "button", title, small }: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "danger" | "good"; disabled?: boolean;
  type?: "button" | "submit"; title?: string; small?: boolean;
}) {
  const base = `inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-[8px] font-medium transition duration-150 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40 ${small ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-[13px]"}`;
  const styles = {
    primary: "bg-accent text-accent-ink shadow-[0_1px_0_rgba(255,255,255,0.25)_inset] hover:bg-accent-2",
    ghost: "bg-surface-2 text-ink-2 ring-1 ring-inset ring-line hover:bg-surface-3 hover:text-ink",
    danger: "bg-crit text-white hover:brightness-110",
    good: "bg-[#23803a] text-white hover:bg-[#2a9444]",
  }[variant];
  return (
    <button type={type} className={`${base} ${styles}`} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

/** Underline tabs; the hint line makes each destination self-explanatory. */
export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { key: T; label: string; hint?: string; count?: number }[]; value: T; onChange: (k: T) => void; label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-x-1 border-b border-line/70">
      {tabs.map((t) => {
        const on = t.key === value;
        return (
          <button key={t.key} role="tab" aria-selected={on} onClick={() => onChange(t.key)}
            className={`-mb-px border-b-2 px-3.5 pb-2 pt-1.5 text-left transition-colors ${on ? "border-accent text-ink" : "border-transparent text-ink-3 hover:text-ink-2"}`}>
            <span className="flex items-center gap-1.5 text-[13px] font-medium">{t.label}{t.count !== undefined && t.count > 0 && <span className="num rounded bg-surface-3 px-1.5 text-[11px] text-ink-2">{t.count}</span>}</span>
            {t.hint && <span className="block text-[11px] font-normal text-ink-3">{t.hint}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function Segmented<T extends string | number>({ options, value, onChange, label }: {
  options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg bg-surface-2 p-0.5 ring-1 ring-inset ring-line">
      {options.map((o) => (
        <button key={String(o.value)} role="radio" aria-checked={o.value === value} onClick={() => onChange(o.value)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${o.value === value ? "bg-surface-3 text-ink shadow-sm" : "text-ink-3 hover:text-ink-2"}`}>{o.label}</button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ status (icon + label, never colour alone) */
const SEV = {
  critical: { color: C.crit, Icon: AlertOctagon, label: "Critical" },
  warning: { color: C.warn, Icon: AlertTriangle, label: "Warning" },
  info: { color: C.ink2, Icon: Info, label: "Info" },
} as const;

export function SeverityBadge({ severity }: { severity: Severity }) {
  const s = SEV[severity];
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-surface-3 px-1.5 py-0.5 text-xs font-medium text-ink">
      <s.Icon size={13} color={s.color} aria-hidden />
      {s.label}
    </span>
  );
}

export function WellStatus({ status }: { status: "ok" | "warning" | "critical" | "down" }) {
  const m = {
    ok: { color: C.good, Icon: CheckCircle2, label: "Normal" },
    warning: { color: C.warn, Icon: AlertTriangle, label: "Warning" },
    critical: { color: C.crit, Icon: AlertOctagon, label: "Critical" },
    down: { color: C.crit, Icon: PowerOff, label: "Down" },
  }[status];
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-ink">
      <m.Icon size={14} color={m.color} aria-hidden />
      {m.label}
    </span>
  );
}

/** Build Bible phases: production below 120 °C in the heated zone is shown as COOLDOWN. */
export const COOLDOWN_T_C = 120;
export function phaseName(phase: Phase, tAvg?: number): string {
  return phase === "production" && tAvg !== undefined && tAvg < COOLDOWN_T_C ? "Cooldown (production)" : PHASE_LABEL[phase];
}
export function PhaseBadge({ phase, tAvg }: { phase: Phase; tAvg?: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-surface-3 px-2 py-0.5 text-xs font-medium text-ink">
      <span className="h-2 w-2 rounded-full" style={{ background: PHASE_COLOR[phase] }} />
      {phaseName(phase, tAvg)}
    </span>
  );
}

/** Data-status label (MEASURED / MODELLED / SYNTHETIC / USER-ENTERED). */
export function DataLabel({ kind }: { kind: "MEASURED" | "MODELLED" | "SYNTHETIC" | "USER-ENTERED" }) {
  const tone = { MEASURED: "#3987e5", MODELLED: "#9085e9", SYNTHETIC: "#f5b82e", "USER-ENTERED": "#199e70" }[kind];
  return (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-px font-[family-name:var(--font-cond)] text-[10px] font-semibold uppercase tracking-wider text-ink-2 ring-1 ring-inset" style={{ ["--tw-ring-color" as string]: tone }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: tone }} />{kind}
    </span>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "blue" | "accent" }) {
  const cls = tone === "blue" ? "bg-[#3987e5]/15 text-[#a9cdf7]" : tone === "accent" ? "bg-accent/15 text-accent-2" : "bg-surface-3 text-ink-2";
  return <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${cls}`}>{children}</span>;
}

/* ------------------------------------------------------------------ loading / empty / error */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skel ${className}`} aria-hidden />;
}
export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-3">
      <Skeleton className="h-6 w-48" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      <Skeleton className="h-64" />
    </div>
  );
}
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line-2 px-6 py-10 text-center">
      <div className="text-sm font-medium text-ink-2">{title}</div>
      {children && <div className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-ink-3">{children}</div>}
    </div>
  );
}
export function ErrorBox({ error }: { error: string }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg bg-crit/10 px-3 py-2.5 text-sm text-ink ring-1 ring-inset ring-crit/40">
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-crit" aria-hidden />
      <span>{error}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ overlays: confirm dialog + toasts (no browser popups) */
interface ConfirmOpts { title: string; body?: ReactNode; confirmLabel?: string; tone?: "primary" | "danger" }
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => false);
export const useConfirm = () => useContext(ConfirmCtx);

interface ToastItem { id: number; title: string; body?: string; tone: "good" | "info" | "error" }
const ToastCtx = createContext<(t: { title: string; body?: string; tone?: ToastItem["tone"] }) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function OverlayProvider({ children }: { children: ReactNode }) {
  const [dlg, setDlg] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const confirm = useCallback((o: ConfirmOpts) => new Promise<boolean>((resolve) => setDlg({ ...o, resolve })), []);
  const toast = useCallback((t: { title: string; body?: string; tone?: ToastItem["tone"] }) => {
    const id = ++seq.current;
    setToasts((x) => [...x.slice(-3), { id, title: t.title, body: t.body, tone: t.tone ?? "good" }]);
    window.setTimeout(() => setToasts((x) => x.filter((y) => y.id !== id)), 4200);
  }, []);
  const close = (v: boolean) => { dlg?.resolve(v); setDlg(null); };
  useEffect(() => {
    if (!dlg) return;
    const on = (e: KeyboardEvent) => { if (e.key === "Escape") close(false); };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });
  return (
    <ConfirmCtx.Provider value={confirm}>
      <ToastCtx.Provider value={toast}>
        {children}
        {dlg && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-[2px]" onMouseDown={(e) => e.target === e.currentTarget && close(false)}>
            <div role="dialog" aria-modal="true" aria-labelledby="dlg-title" className="pop card w-full max-w-md p-5">
              <h2 id="dlg-title" className="text-base font-semibold">{dlg.title}</h2>
              {dlg.body && <div className="mt-2 text-[13px] leading-relaxed text-ink-2">{dlg.body}</div>}
              <div className="mt-5 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => close(false)}>Cancel</Button>
                <span ref={(el) => el?.querySelector("button")?.focus()}><Button variant={dlg.tone === "danger" ? "danger" : "primary"} onClick={() => close(true)}>{dlg.confirmLabel ?? "Confirm"}</Button></span>
              </div>
            </div>
          </div>
        )}
        <div aria-live="polite" className="pointer-events-none fixed bottom-20 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2 lg:bottom-4">
          {toasts.map((t) => (
            <div key={t.id} className="pop pointer-events-auto card flex items-start gap-2.5 px-3.5 py-3">
              {t.tone === "good" ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-good" /> : t.tone === "error" ? <AlertTriangle size={16} className="mt-0.5 shrink-0 text-crit" /> : <Info size={16} className="mt-0.5 shrink-0 text-s1" />}
              <div className="min-w-0 flex-1"><div className="text-[13px] font-medium">{t.title}</div>{t.body && <div className="mt-0.5 text-xs text-ink-3">{t.body}</div>}</div>
              <button aria-label="Dismiss" className="text-ink-3 hover:text-ink" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}><X size={14} /></button>
            </div>
          ))}
        </div>
      </ToastCtx.Provider>
    </ConfirmCtx.Provider>
  );
}

/* ------------------------------------------------------------------ chart helpers */
export const axisProps = {
  stroke: C.line,
  tick: { fill: C.ink3, fontSize: 11 },
  tickLine: false,
  axisLine: { stroke: C.line },
} as const;

interface TipRow { name?: string | number; value?: unknown; color?: string; dataKey?: unknown; unit?: string; payload?: unknown }
export function ChartTip({ active, payload, label, labelFmt, valueFmt, hide = [] }: {
  active?: boolean; payload?: TipRow[]; label?: unknown; labelFmt?: (l: unknown) => string; valueFmt?: (v: number, name: string) => string;
  hide?: string[];
}) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="rounded-lg bg-[#100f0d]/95 px-3 py-2 text-xs shadow-2xl ring-1 ring-inset ring-line-2">
      <div className="mb-1 font-medium text-ink-2">{labelFmt ? labelFmt(label) : String(label)}</div>
      {payload.filter((p) => p.value !== null && p.value !== undefined && !hide.includes(String(p.name))).map((p, i) => (
        <div key={i} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5 text-ink-2">
            <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
            {String(p.name)}
          </span>
          <span className="num text-ink">{valueFmt ? valueFmt(Number(p.value), String(p.name)) : fmt(Number(p.value), 2)}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <svg width="18" height="8" aria-hidden>
            <line x1="1" y1="4" x2="17" y2="4" stroke={it.color} strokeWidth="2" strokeDasharray={it.dashed ? "4 3" : undefined} strokeLinecap="round" />
          </svg>
          {it.label}
        </span>
      ))}
    </div>
  );
}
