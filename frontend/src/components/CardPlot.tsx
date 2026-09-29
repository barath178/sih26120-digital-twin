import { useState, type MouseEvent } from "react";
import { C, fmt } from "./ui";

/** Closed-loop load-vs-position plot for dynamometer cards, with nearest-point hover. */
export default function CardPlot({ pos, load, color, refLines = [], title, xMax, yRange }: {
  pos: number[]; load: number[]; color: string; title: string;
  refLines?: { y: number; label: string }[]; xMax?: number; yRange?: [number, number];
}) {
  const [hi, setHi] = useState<number | null>(null);
  const W = 360;
  const H = 230;
  const m = { l: 44, r: 12, t: 10, b: 30 };
  const pw = W - m.l - m.r;
  const ph = H - m.t - m.b;
  const xm = xMax ?? Math.max(...pos, 0.1);
  const lo = yRange ? yRange[0] : Math.min(0, ...load, ...refLines.map((r) => r.y));
  const hiY = yRange ? yRange[1] : Math.max(...load, ...refLines.map((r) => r.y));
  const pad = (hiY - lo) * 0.08 || 1;
  const y0 = lo - pad;
  const y1 = hiY + pad;
  const sx = (x: number) => m.l + (x / xm) * pw;
  const sy = (y: number) => m.t + (1 - (y - y0) / (y1 - y0)) * ph;
  const d = pos.map((x, i) => `${i ? "L" : "M"}${sx(x).toFixed(1)},${sy(load[i]).toFixed(1)}`).join(" ") + " Z";
  const yTicks = Array.from({ length: 5 }, (_, i) => y0 + ((y1 - y0) * i) / 4);
  const xTicks = Array.from({ length: 5 }, (_, i) => (xm * i) / 4);

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const py = ((e.clientY - r.top) / r.height) * H;
    let best = 0;
    let bd = Infinity;
    pos.forEach((x, i) => {
      const dd = (sx(x) - px) ** 2 + (sy(load[i]) - py) ** 2;
      if (dd < bd) { bd = dd; best = i; }
    });
    setHi(bd < 900 ? best : null);
  };

  return (
    <div>
      <div className="mb-1 text-xs font-medium text-ink-2">{title}</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseMove={onMove} onMouseLeave={() => setHi(null)} role="img" aria-label={title}>
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line x1={m.l} x2={W - m.r} y1={sy(t)} y2={sy(t)} stroke={C.line} />
            <text x={m.l - 6} y={sy(t) + 4} fill={C.ink3} fontSize={10} textAnchor="end">{fmt(t, 0)}</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={`x${t}`} x={sx(t)} y={H - 12} fill={C.ink3} fontSize={10} textAnchor="middle">{fmt(t, 1)}</text>
        ))}
        <text x={m.l + pw / 2} y={H - 1} fill={C.ink3} fontSize={10} textAnchor="middle">Position (m)</text>
        <text x={10} y={m.t + ph / 2} fill={C.ink3} fontSize={10} textAnchor="middle" transform={`rotate(-90 10 ${m.t + ph / 2})`}>Load (kN)</text>
        {refLines.map((r) => (
          <g key={r.label}>
            <line x1={m.l} x2={W - m.r} y1={sy(r.y)} y2={sy(r.y)} stroke={C.ink3} strokeDasharray="4 3" />
            <text x={W - m.r - 2} y={sy(r.y) - 4} fill={C.ink3} fontSize={10} textAnchor="end">{r.label}</text>
          </g>
        ))}
        <path d={d} fill={color} fillOpacity={0.12} stroke={color} strokeWidth={2} strokeLinejoin="round" />
        {hi !== null && (
          <g>
            <circle cx={sx(pos[hi])} cy={sy(load[hi])} r={4.5} fill={color} stroke={C.surface} strokeWidth={2} />
            <rect x={Math.min(sx(pos[hi]) + 8, W - 120)} y={Math.max(sy(load[hi]) - 30, 2)} width={112} height={24} rx={4} fill="#10100f" stroke={C.line} />
            <text x={Math.min(sx(pos[hi]) + 14, W - 114)} y={Math.max(sy(load[hi]) - 14, 18)} fill={C.ink} fontSize={10}>
              {fmt(pos[hi], 2)} m · {fmt(load[hi], 1)} kN
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
