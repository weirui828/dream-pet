"use client";

import { useMemo, useRef, useState } from "react";
import type { Sample } from "@/lib/api";

const STATE_FILL: Record<string, string> = {
  exploring: "var(--boredom)",
  napping: "var(--energy)",
  asleep: "var(--accent)",
  dreaming: "var(--curiosity)",
};

type Overlay = { samples: Sample[]; label: string; dashed?: boolean };

const byTime = (a: Sample, b: Sample) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0);

export default function DriveChart({
  samples: rawSamples,
  energyMax = 100,
  band,
  height = 220,
  cursor,
  onCursor,
  markers = [],
  overlay,
  tz,
}: {
  samples: Sample[];
  energyMax?: number;
  band?: { theta_low: number; theta_high: number };
  height?: number;
  cursor?: number; // index into samples
  onCursor?: (i: number) => void;
  markers?: { t: string; label: string; color?: string }[];
  overlay?: Overlay;
  tz?: string;
}) {
  const W = 1000;
  const H = height;
  const pad = { l: 34, r: 34, t: 10, b: 24 };
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  // lines connect points in array order, so make sure that order is time order
  const samples = useMemo(() => [...rawSamples].sort(byTime), [rawSamples]);

  const { t0, t1, times } = useMemo(() => {
    const times = samples.map((s) => new Date(s.t).getTime());
    const all = overlay ? [...times, ...overlay.samples.map((s) => new Date(s.t).getTime())] : times;
    return { t0: Math.min(...all), t1: Math.max(...all), times };
  }, [samples, overlay]);

  if (samples.length < 2) {
    return <div className="grid h-40 place-items-center text-sm text-muted">Not enough data yet.</div>;
  }

  const x = (t: number) => pad.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - pad.l - pad.r);
  const yB = (v: number) => pad.t + (1 - v) * (H - pad.t - pad.b);
  const yE = (v: number) => pad.t + (1 - Math.min(1, v / energyMax)) * (H - pad.t - pad.b);

  const path = (ss: Sample[], f: (s: Sample) => number) =>
    ss.map((s, i) => `${i ? "L" : "M"}${x(new Date(s.t).getTime()).toFixed(1)},${f(s).toFixed(1)}`).join("");

  // state bands
  const segs: { a: number; b: number; state: string }[] = [];
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i].state;
    const last = segs[segs.length - 1];
    if (last && last.state === s) last.b = times[i];
    else segs.push({ a: times[i], b: times[i], state: s });
  }
  for (let i = 0; i < segs.length - 1; i++) segs[i].b = segs[i + 1].a;

  // day ticks
  const ticks: { t: number; label: string }[] = [];
  const span = t1 - t0;
  const DAY = 86400e3;
  const step = span > 14 * DAY ? 5 * DAY : span > 3 * DAY ? DAY : span > DAY ? 6 * 3600e3 : span > 8 * 3600e3 ? 3 * 3600e3 : 3600e3;
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
    const d = new Date(t);
    ticks.push({
      t,
      label:
        step >= 86400e3
          ? d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: tz })
          : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: tz }),
    });
  }

  const idxAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    const t = t0 + ((px - pad.l) / (W - pad.l - pad.r)) * (t1 - t0);
    let lo = 0,
      hi = times.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (times[mid] < t) lo = mid + 1;
      else hi = mid;
    }
    return Math.max(0, Math.min(times.length - 1, lo));
  };

  const active = hover ?? cursor ?? null;
  const as = active != null ? samples[active] : null;

  return (
    <div className="relative">
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-auto w-full touch-none select-none"
        style={{ height }}
        onPointerMove={(e) => setHover(idxAt(e.clientX))}
        onPointerLeave={() => setHover(null)}
        onPointerDown={(e) => onCursor?.(idxAt(e.clientX))}
      >
        {segs.map((s, i) =>
          STATE_FILL[s.state] ? (
            <rect key={i} x={x(s.a)} y={pad.t} width={Math.max(0.5, x(s.b) - x(s.a))} height={H - pad.t - pad.b} fill={STATE_FILL[s.state]} opacity={0.09} />
          ) : null,
        )}
        {ticks.map((tk) => (
          <g key={tk.t}>
            <line x1={x(tk.t)} x2={x(tk.t)} y1={pad.t} y2={H - pad.b} stroke="var(--line)" />
            <text x={x(tk.t) + 3} y={H - 7} fontSize={11} fill="var(--muted)">
              {tk.label}
            </text>
          </g>
        ))}
        {band && (
          <>
            <line x1={pad.l} x2={W - pad.r} y1={yB(band.theta_high)} y2={yB(band.theta_high)} stroke="var(--boredom)" strokeDasharray="5 5" opacity={0.6} />
            <line x1={pad.l} x2={W - pad.r} y1={yB(band.theta_low)} y2={yB(band.theta_low)} stroke="var(--boredom)" strokeDasharray="2 5" opacity={0.6} />
            <text x={4} y={yB(band.theta_high) + 4} fontSize={10} fill="var(--boredom)">θ↑</text>
            <text x={4} y={yB(band.theta_low) + 4} fontSize={10} fill="var(--boredom)">θ↓</text>
          </>
        )}
        {overlay && (
          <>
            <path d={path([...overlay.samples].sort(byTime), (s) => yE(s.energy))} fill="none" stroke="var(--energy)" strokeWidth={1.5} strokeDasharray="4 4" opacity={0.6} vectorEffect="non-scaling-stroke" />
            <path d={path([...overlay.samples].sort(byTime), (s) => yB(s.boredom))} fill="none" stroke="var(--boredom)" strokeWidth={1.5} strokeDasharray="4 4" opacity={0.6} vectorEffect="non-scaling-stroke" />
          </>
        )}
        <path d={path(samples, (s) => yE(s.energy))} fill="none" stroke="var(--energy)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        <path d={path(samples, (s) => yB(s.boredom))} fill="none" stroke="var(--boredom)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        {markers.map((m, i) => {
          const t = new Date(m.t).getTime();
          if (t < t0 || t > t1) return null;
          return <circle key={i} cx={x(t)} cy={H - pad.b - 4} r={3.5} fill={m.color || "var(--curiosity)"}><title>{m.label}</title></circle>;
        })}
        {as && (
          <line x1={x(new Date(as.t).getTime())} x2={x(new Date(as.t).getTime())} y1={pad.t} y2={H - pad.b} stroke="var(--ink)" opacity={0.35} />
        )}
        <text x={W - pad.r + 4} y={pad.t + 10} fontSize={10} fill="var(--energy)">{Math.round(energyMax)}</text>
        <text x={W - pad.r + 4} y={H - pad.b} fontSize={10} fill="var(--energy)">0</text>
        <text x={pad.l - 20} y={pad.t + 10} fontSize={10} fill="var(--boredom)">1</text>
        <text x={pad.l - 20} y={H - pad.b} fontSize={10} fill="var(--boredom)">0</text>
      </svg>
      {as && (
        <div className="pointer-events-none absolute top-2 left-10 rounded-lg border border-line bg-panel/95 px-2.5 py-1.5 text-xs shadow-sm">
          <div className="font-medium">{new Date(as.t).toLocaleString(undefined, { timeZone: tz, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</div>
          <div className="text-muted capitalize">{as.state}</div>
          <div>
            <span className="text-boredom">boredom {as.boredom.toFixed(2)}</span> · <span className="text-energy">energy {Math.round(as.energy)}</span>
          </div>
        </div>
      )}
      <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-muted">
        <span><span className="text-boredom">━</span> boredom</span>
        <span><span className="text-energy">━</span> energy</span>
        {overlay && <span>┅ {overlay.label}</span>}
        {Object.entries(STATE_FILL).map(([k, c]) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: c, opacity: 0.35 }} />
            {k}
          </span>
        ))}
      </div>
    </div>
  );
}
