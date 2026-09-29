import { useState } from "react";
import { C, fmt } from "./ui";

export interface SteamEvent {
  well_id: string;
  generator: string;
  start_day: number;
  end_day: number;
  steam_t: number;
  rate_tpd: number;
  status: "injecting" | "scheduled";
  delay_days: number;
  due_day?: number;
  optimal_day?: number | null;
}

/** Gantt of steam-generator allocation. Bars: injections (identity = well, label on bar);
 *  tick = economic optimum re-steam day predicted by the twin. */
export default function SteamCalendar({ events, generators, horizon, simTime }: {
  events: SteamEvent[]; generators: { id: string; capacity_tpd: number }[]; horizon: number; simTime: string;
}) {
  const [hover, setHover] = useState<SteamEvent | null>(null);
  const rowH = 44;
  const left = 64;
  const width = 900;
  const plotW = width - left - 12;
  const height = generators.length * rowH + 30;
  const x = (d: number) => left + (Math.max(0, Math.min(d, horizon)) / horizon) * plotW;
  const t0 = new Date(simTime).getTime();
  const ticks = Array.from({ length: Math.floor(horizon / 30) + 1 }, (_, i) => i * 30);
  const dateOf = (d: number) => new Date(t0 + d * 86400000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Steam generator calendar">
        {ticks.map((d) => (
          <g key={d}>
            <line x1={x(d)} x2={x(d)} y1={0} y2={height - 22} stroke={C.line} strokeWidth={1} />
            <text x={x(d)} y={height - 8} fill={C.ink3} fontSize={11} textAnchor="middle">{d === 0 ? "Today" : `${dateOf(d)}`}</text>
          </g>
        ))}
        {generators.map((g, i) => (
          <g key={g.id}>
            <text x={4} y={i * rowH + rowH / 2 + 4} fill={C.ink2} fontSize={12} fontWeight={600}>{g.id}</text>
            <text x={4} y={i * rowH + rowH / 2 + 17} fill={C.ink3} fontSize={10}>{g.capacity_tpd} t/d</text>
            {events.filter((e) => e.generator === g.id && e.start_day < horizon).map((e) => {
              const x0 = x(e.start_day);
              const w = Math.max(x(e.end_day) - x0 - 2, 4);
              const y = i * rowH + 8;
              return (
                <g key={`${e.well_id}-${e.start_day}`} onMouseEnter={() => setHover(e)} onMouseLeave={() => setHover(null)} className="cursor-default">
                  <rect x={x0} y={y} width={w} height={rowH - 16} rx={4} fill={e.status === "injecting" ? C.s2 : "#5a2a14"}
                    stroke={e.status === "injecting" ? "none" : C.s2} strokeWidth={1} />
                  {w > 38 && <text x={x0 + 5} y={y + (rowH - 16) / 2 + 4} fill={C.ink} fontSize={11} fontWeight={600}>{e.well_id}</text>}
                </g>
              );
            })}
          </g>
        ))}
        {/* optimal re-steam markers */}
        {events.filter((e) => e.optimal_day !== undefined && e.optimal_day !== null && e.optimal_day < horizon).map((e) => {
          const gi = generators.findIndex((g) => g.id === e.generator);
          const xo = x(e.optimal_day as number);
          const y = gi * rowH + 4;
          return <path key={`opt-${e.well_id}`} d={`M ${xo} ${y} l -4 -4 h 8 z`} fill={C.s7}><title>{`${e.well_id}: economic optimum`}</title></path>;
        })}
      </svg>
      <div className="mt-1 flex flex-wrap gap-4 text-xs text-ink-2">
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm" style={{ background: C.s2 }} />Injecting now</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm border" style={{ background: "#5a2a14", borderColor: C.s2 }} />Scheduled (planned cycle end)</span>
        <span className="inline-flex items-center gap-1.5"><svg width="10" height="8"><path d="M1 1 H9 L5 7 Z" fill={C.s7} /></svg>Twin's economic re-steam day</span>
      </div>
      {hover && (
        <div className="pointer-events-none absolute right-2 top-2 z-10 rounded-lg border border-line-2 bg-[#10100f]/95 p-3 text-xs shadow-xl">
          <div className="mb-1 text-sm font-semibold">{hover.well_id} · {hover.generator}</div>
          <div className="text-ink-2">{hover.status === "injecting" ? "Injecting now" : "Scheduled"}: {dateOf(hover.start_day)} → {dateOf(hover.end_day)}</div>
          <div className="text-ink-2">{fmt(hover.steam_t, 0)} t at {fmt(hover.rate_tpd, 0)} t/d</div>
          {hover.delay_days > 0.1 && <div className="text-ink-2">Waiting {fmt(hover.delay_days, 1)} days for a free generator</div>}
          {hover.optimal_day !== undefined && hover.optimal_day !== null && <div className="text-ink-2">Economic optimum: {dateOf(hover.optimal_day)}</div>}
        </div>
      )}
    </div>
  );
}
