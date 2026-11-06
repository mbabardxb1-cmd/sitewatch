"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ResponseBucket } from "@/lib/api";

// Single-series line chart: hourly average response time.
// Follows the data-viz spec: 2px line, recessive grid, one y-axis, crosshair tooltip,
// series named by the title (no legend box for one series), table view available.
const SERIES = "#2a78d6"; // categorical slot 1 (reference palette, validated)
const H = 220;
const PAD = { top: 12, right: 16, bottom: 28, left: 68 };

function niceMax(v: number): number {
  if (v <= 0) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

export function ResponseChart({ data, thresholdMs, hours }: { data: ResponseBucket[]; thresholdMs?: number; hours: number }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver((e) => setWidth(Math.max(280, e[0].contentRect.width)));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const points = useMemo(() => data.filter((d) => d.avg_ms !== null), [data]);
  const end = Date.now();
  const start = end - hours * 3600_000;
  // Scale to the data (not the threshold) so small response times stay readable.
  const dataMax = Math.max(0, ...points.map((p) => p.avg_ms ?? 0));
  const yMax = niceMax(dataMax * 1.15);
  const thresholdInRange = thresholdMs !== undefined && thresholdMs <= yMax;
  const iw = width - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const x = (t: number) => PAD.left + ((t - start) / (end - start)) * iw;
  const y = (v: number) => PAD.top + ih - (v / yMax) * ih;

  // Break the line where an hour has no successful checks (gap = unknown, not zero).
  const segments: string[] = [];
  let cur = "";
  let prevT: number | null = null;
  for (const p of points) {
    const t = new Date(p.bucket).getTime() + 1800_000; // centre of the hour
    if (prevT !== null && t - prevT > 3600_000 * 1.5) {
      segments.push(cur);
      cur = "";
    }
    cur += `${cur ? "L" : "M"}${x(t).toFixed(1)},${y(p.avg_ms!).toFixed(1)}`;
    prevT = t;
  }
  if (cur) segments.push(cur);

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * yMax);
  const xTicks = Array.from({ length: 5 }, (_, i) => start + ((end - start) * i) / 4);
  const fmtX = (t: number) =>
    new Date(t).toLocaleString(undefined, hours <= 48 ? { hour: "2-digit", minute: "2-digit" } : { month: "short", day: "numeric" });

  function onMove(e: React.MouseEvent<SVGRectElement>) {
    if (!points.length) return;
    const rect = (e.target as SVGRectElement).getBoundingClientRect();
    const t = start + ((e.clientX - rect.left) / rect.width) * (end - start);
    let best = 0;
    points.forEach((p, i) => {
      if (Math.abs(new Date(p.bucket).getTime() - t) < Math.abs(new Date(points[best].bucket).getTime() - t)) best = i;
    });
    setHover(best);
  }

  const hp = hover !== null ? points[hover] : null;
  const hx = hp ? x(new Date(hp.bucket).getTime() + 1800_000) : 0;

  return (
    <div ref={wrap} className="space-y-2">
      <div className="flex justify-end">
        <button className="text-xs text-slate-600 underline-offset-2 hover:underline" onClick={() => setShowTable(!showTable)}>
          {showTable ? "Show chart" : "Show table"}
        </button>
      </div>
      {points.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-500">No successful checks in this period — no response times to show.</p>
      ) : showTable ? (
        <div className="max-h-64 overflow-auto">
          <table className="sw-table tabular-nums">
            <thead className="sticky top-0 bg-white text-xs uppercase text-slate-500">
              <tr><th className="py-1 pr-4">Hour</th><th className="pr-4">Avg</th><th className="pr-4">p95</th><th className="pr-4">Max</th><th>Checks OK</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {[...data].reverse().map((d) => (
                <tr key={d.bucket}>
                  <td className="py-1 pr-4">{new Date(d.bucket).toLocaleString()}</td>
                  <td className="pr-4">{d.avg_ms ?? "—"} ms</td><td className="pr-4">{d.p95_ms ?? "—"} ms</td>
                  <td className="pr-4">{d.max_ms ?? "—"} ms</td><td>{d.successes}/{d.checks}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <svg width={width} height={H} role="img" aria-label="Average response time per hour">
            {ticks.map((v) => (
              <g key={v}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(v)} y2={y(v)} stroke="#e2e8f0" strokeWidth={1} />
                <text x={PAD.left - 8} y={y(v)} dy="0.32em" textAnchor="end" fontSize={11} fill="#64748b">{Math.round(v)} ms</text>
              </g>
            ))}
            {xTicks.map((t) => (
              <text key={t} x={x(t)} y={H - 8} textAnchor="middle" fontSize={11} fill="#64748b">{fmtX(t)}</text>
            ))}
            {thresholdInRange && thresholdMs !== undefined && (
              <g>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(thresholdMs)} y2={y(thresholdMs)} stroke="#94a3b8"
                      strokeWidth={1} strokeDasharray="4 4" />
                <text x={width - PAD.right} y={y(thresholdMs) - 4} textAnchor="end" fontSize={11} fill="#475569">
                  Degraded threshold {thresholdMs} ms
                </text>
              </g>
            )}
            {segments.map((d, i) => (
              <path key={i} d={d} fill="none" stroke={SERIES} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            ))}
            {points.length < 3 && points.map((p) => (
              <circle key={p.bucket} cx={x(new Date(p.bucket).getTime() + 1800_000)} cy={y(p.avg_ms!)} r={4} fill={SERIES}
                      stroke="#fff" strokeWidth={2} />
            ))}
            {hp && (
              <g pointerEvents="none">
                <line x1={hx} x2={hx} y1={PAD.top} y2={PAD.top + ih} stroke="#94a3b8" strokeWidth={1} />
                <circle cx={hx} cy={y(hp.avg_ms!)} r={4} fill={SERIES} stroke="#fff" strokeWidth={2} />
              </g>
            )}
            <rect x={PAD.left} y={PAD.top} width={iw} height={ih} fill="transparent" onMouseMove={onMove}
                  onMouseLeave={() => setHover(null)} />
          </svg>
          {thresholdMs !== undefined && !thresholdInRange && (
            <p className="text-xs text-slate-500">All hourly averages are below the {thresholdMs} ms degraded threshold.</p>
          )}
          {hp && (
            <div className="pointer-events-none absolute rounded-md border border-slate-200 bg-white px-2 py-1 text-xs shadow-sm"
                 style={{ left: hx + 8 > width - 190 ? hx - 190 : hx + 8, top: 8 }}>
              <div className="font-medium text-slate-900">{new Date(hp.bucket).toLocaleString()}</div>
              <div className="text-slate-700 tabular-nums">Avg {hp.avg_ms} ms · p95 {hp.p95_ms ?? "—"} ms</div>
              <div className="text-slate-500 tabular-nums">{hp.successes}/{hp.checks} checks succeeded</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
