import { useId } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { C, ChartTip, Legend, axisProps, fmt } from "./ui";

export interface SeriesDef {
  key: string;
  name: string;
  color: string;
  dashed?: boolean;
}

/** Single-axis time chart (sim days on x). One unit per chart, never a dual axis.
 *  A lone series gets an area fill; every series ends on an emphasised point. */
export default function TimeChart({ data, series, unit, height = 170, dateOf, digits = 1, yDomain, refY, showLegend = true }: {
  data: Record<string, number | null>[]; series: SeriesDef[]; unit: string; height?: number; dateOf: (t: number) => string;
  digits?: number; yDomain?: [number | "auto" | "dataMin" | "dataMax", number | "auto" | "dataMin" | "dataMax"];
  refY?: { y: number; label: string }; showLegend?: boolean;
}) {
  const gid = useId().replace(/:/g, "");
  const lastIdx = (key: string) => {
    for (let i = data.length - 1; i >= 0; i--) if (data[i][key] !== null && data[i][key] !== undefined) return i;
    return -1;
  };
  const single = series.length === 1;
  return (
    <div>
      {showLegend && series.length > 1 && <div className="mb-1.5"><Legend items={series.map((s) => ({ label: s.name, color: s.color, dashed: s.dashed }))} /></div>}
      <div style={{ height }}>
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
            <defs>
              {series.map((s) => (
                <linearGradient key={s.key} id={`${gid}-${s.key}`} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor={s.color} stopOpacity={0.26} /><stop offset="100%" stopColor={s.color} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid vertical={false} strokeDasharray="2 4" />
            <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={dateOf} {...axisProps} minTickGap={44} />
            <YAxis {...axisProps} width={48} domain={yDomain ?? ["auto", "auto"]} tickFormatter={(v) => fmt(Number(v), Math.min(digits, 2))} />
            <Tooltip cursor={{ stroke: C.ink3, strokeDasharray: "3 3" }} content={<ChartTip labelFmt={(l) => dateOf(Number(l))} valueFmt={(v) => `${fmt(v, digits)} ${unit}`} />} />
            {refY && <ReferenceLine y={refY.y} stroke={C.ink3} strokeDasharray="4 3" label={{ value: refY.label, fill: C.ink3, fontSize: 10, position: "insideTopRight" }} />}
            {single && <Area dataKey={series[0].key} name={series[0].name} stroke="none" fill={`url(#${gid}-${series[0].key})`} isAnimationActive={false} activeDot={false} tooltipType="none" />}
            {series.map((s) => {
              const li = lastIdx(s.key);
              return (
                <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} isAnimationActive={false} connectNulls={false}
                  strokeDasharray={s.dashed ? "5 4" : undefined} activeDot={{ r: 4, strokeWidth: 2, stroke: C.surface }}
                  dot={(p: { cx?: number; cy?: number; index?: number }) => (p.index === li && p.cx !== undefined && p.cy !== undefined
                    ? <circle key={`d-${s.key}`} cx={p.cx} cy={p.cy} r={3.6} fill={s.color} stroke={C.surface} strokeWidth={2} />
                    : <g key={`n-${s.key}-${p.index}`} />)} />
              );
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
