import { useState } from "react";
import type { Generator, WellSummary } from "../types";
import { C, PHASE_COLOR, fmt, pct, phaseName } from "./ui";
import { go } from "../router";

// Schematic lease layout (illustrative, not surveyed). Coordinates are compressed vertically so the map has no dead space.
const W = 100;
const H = 60;
const ty = (y: number) => 8 + (y - 14) * 0.74;
const GGS = { x: 58, y: ty(55) };

function statusGlyph(status: WellSummary["status"], x: number, y: number) {
  if (status === "ok") return null;
  if (status === "warning") return <path d={`M ${x + 2.4} ${y - 4.6} l 2 3.4 h -4 z`} fill={C.warn} stroke={C.surface} strokeWidth={0.35} />;
  return <rect x={x + 1} y={y - 4.8} width={3.2} height={3.2} rx={0.6} fill={C.crit} stroke={C.surface} strokeWidth={0.35} />;
}

export default function FieldMap({ wells, generators }: { wells: WellSummary[]; generators: Generator[] }) {
  const [hover, setHover] = useState<WellSummary | null>(null);
  const byId = Object.fromEntries(wells.map((w) => [w.id, w]));
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Schematic map of Baghewala CSS wells">
        <defs>
          <radialGradient id="heat"><stop offset="0%" stopColor={C.s2} stopOpacity="0.6" /><stop offset="100%" stopColor={C.s2} stopOpacity="0" /></radialGradient>
        </defs>
        {wells.map((w) => <line key={`fl-${w.id}`} x1={w.x} y1={ty(w.y)} x2={GGS.x} y2={GGS.y} stroke="#34342f" strokeWidth="0.45" />)}
        {generators.map((g) => {
          const w = g.busy_with ? byId[g.busy_with] : null;
          return w ? (
            <line key={`st-${g.id}`} x1={g.x} y1={ty(g.y)} x2={w.x} y2={ty(w.y)} stroke={C.s2} strokeWidth="0.8" strokeDasharray="1.6 1.2">
              <animate attributeName="stroke-dashoffset" from="5.6" to="0" dur="1s" repeatCount="indefinite" />
            </line>
          ) : null;
        })}
        <rect x={GGS.x - 2.5} y={GGS.y - 1.8} width="5" height="3.6" rx="0.6" fill={C.surface2} stroke={C.ink3} strokeWidth="0.3" />
        <text x={GGS.x} y={GGS.y + 5} fill={C.ink3} fontSize="2" textAnchor="middle">GGS</text>
        {generators.map((g) => (
          <g key={g.id}>
            <rect x={g.x - 2.4} y={ty(g.y) - 2.4} width="4.8" height="4.8" rx="0.8" fill={C.surface2} stroke={g.busy_with ? C.s2 : C.ink3} strokeWidth="0.4" />
            <text x={g.x} y={ty(g.y) + 0.8} fill={C.ink} fontSize="1.9" textAnchor="middle" fontWeight="600">SG</text>
            <text x={g.x} y={ty(g.y) + 5.2} fill={C.ink2} fontSize="1.9" textAnchor="middle">{g.id} {g.busy_with ? `→ ${g.busy_with}` : "idle"}</text>
          </g>
        ))}
        {wells.map((w) => {
          const heat = Math.max(0, Math.min(1, (w.t_avg - 47) / 250));
          const y = ty(w.y);
          return (
            <g key={w.id} className="cursor-pointer" onMouseEnter={() => setHover(w)} onMouseLeave={() => setHover(null)} onClick={() => go({ page: "well", id: w.id })}>
              <circle cx={w.x} cy={y} r={3 + 5 * heat} fill="url(#heat)" opacity={0.25 + 0.75 * heat} />
              <circle cx={w.x} cy={y} r="5" fill="transparent" />
              <circle cx={w.x} cy={y} r="1.9" fill={PHASE_COLOR[w.phase]} stroke={C.surface} strokeWidth="0.5" />
              {w.vfd && <circle cx={w.x} cy={y} r="2.8" fill="none" stroke={C.s7} strokeWidth="0.35" />}
              {statusGlyph(w.status, w.x, y)}
              <text x={w.x} y={y + 4.8} fill={C.ink} fontSize="2.1" textAnchor="middle" fontWeight="600">{w.id}</text>
              <text x={w.x} y={y + 7.1} fill={C.ink3} fontSize="1.8" textAnchor="middle">
                {w.phase === "production" ? `${fmt(w.oil, 1)} m³/d` : w.phase === "injection" ? `${fmt(w.steam_rate, 0)} t/d steam` : "soaking"}
              </text>
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="pointer-events-none absolute z-10 w-56 rounded-lg border border-line-2 bg-[#10100f]/95 p-3 text-xs shadow-xl"
          style={{ left: `min(calc(${(hover.x / W) * 100}% + 12px), calc(100% - 14rem))`, top: `${(ty(hover.y) / H) * 100}%` }}>
          <div className="mb-1 flex items-center justify-between"><span className="text-sm font-semibold">{hover.id}</span><span className="text-ink-3">{hover.pad}</span></div>
          <div className="text-ink-2">{phaseName(hover.phase, hover.t_avg)} · day {fmt(hover.t_phase_d, 0)}</div>
          <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 text-ink-2">
            <span>Oil</span><span className="num text-right text-ink">{fmt(hover.oil, 2)} m³/d</span>
            <span>Heated zone</span><span className="num text-right text-ink">{fmt(hover.t_avg, 0)} °C</span>
            <span>Viscosity</span><span className="num text-right text-ink">{fmt(hover.mu_cp, 0)} cP</span>
            <span>Fillage</span><span className="num text-right text-ink">{hover.phase === "production" ? pct(hover.fillage) : "–"}</span>
          </div>
        </div>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-2">
        {(["production", "injection", "soak"] as const).map((p) => <span key={p} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: PHASE_COLOR[p] }} />{p === "injection" ? "Steam injection" : p[0].toUpperCase() + p.slice(1)}</span>)}
        <span className="inline-flex items-center gap-1.5"><svg width="10" height="10"><path d="M5 1 L9 8 H1 Z" fill={C.warn} /></svg>Warning</span>
        <span className="inline-flex items-center gap-1.5"><svg width="10" height="10"><rect x="1" y="1" width="8" height="8" rx="1.5" fill={C.crit} /></svg>Critical / down</span>
        <span className="inline-flex items-center gap-1.5"><svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="none" stroke={C.s7} strokeWidth="1.2" /></svg>Closed-loop VFD</span>
        <span className="text-ink-3">Glow = heated-zone temperature</span>
      </div>
    </div>
  );
}
