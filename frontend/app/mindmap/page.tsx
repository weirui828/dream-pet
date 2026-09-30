"use client";

import { useMemo, useState } from "react";
import { Card, Empty, ErrorNote, PageHeader, Pill } from "@/components/ui";
import type { Memory } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Cluster = { id: string; label: string; size: number; lp: number; visits: number; seed: boolean; error_history: number[] };
type Bubble = Cluster & { x: number; y: number; r: number };

function layout(cs: Cluster[], W: number, H: number): Bubble[] {
  const maxSize = Math.max(1, ...cs.map((c) => c.size));
  const sorted = [...cs].sort((a, b) => b.size - a.size || a.id.localeCompare(b.id));
  const bs: Bubble[] = sorted.map((c, i) => {
    const r = 18 + 52 * Math.sqrt(c.size / maxSize);
    const a = i * 2.39996; // golden-angle spiral start
    const d = 14 * Math.sqrt(i) * 3;
    return { ...c, r, x: W / 2 + d * Math.cos(a), y: H / 2 + d * Math.sin(a) };
  });
  for (let it = 0; it < 300; it++) {
    for (let i = 0; i < bs.length; i++) {
      const a = bs[i];
      a.x += (W / 2 - a.x) * 0.01;
      a.y += (H / 2 - a.y) * 0.01;
      for (let j = i + 1; j < bs.length; j++) {
        const b = bs[j];
        const dx = b.x - a.x || 0.01,
          dy = b.y - a.y || 0.01;
        const dist = Math.hypot(dx, dy);
        const min = a.r + b.r + 6;
        if (dist < min) {
          const push = (min - dist) / 2;
          const ux = dx / dist,
            uy = dy / dist;
          a.x -= ux * push;
          a.y -= uy * push;
          b.x += ux * push;
          b.y += uy * push;
        }
      }
      a.x = Math.max(a.r, Math.min(W - a.r, a.x));
      a.y = Math.max(a.r, Math.min(H - a.r, a.y));
    }
  }
  return bs;
}

function lpColor(lp: number): string {
  if (lp > 0.005) return `color-mix(in oklab, var(--curiosity) ${Math.round(25 + Math.min(1, lp / 0.15) * 65)}%, var(--panel-2))`;
  if (lp < -0.005) return `color-mix(in oklab, var(--accent) ${Math.round(20 + Math.min(1, -lp / 0.15) * 50)}%, var(--panel-2))`;
  return "var(--panel-2)";
}

function Spark({ xs }: { xs: number[] }) {
  if (xs.length < 2) return <span className="text-xs text-muted">—</span>;
  const w = 120,
    h = 28;
  const d = xs.map((v, i) => `${i ? "L" : "M"}${(i / (xs.length - 1)) * w},${h - Math.min(1, v) * h}`).join("");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-7 w-32">
      <path d={d} fill="none" stroke="var(--curiosity)" strokeWidth={1.5} />
    </svg>
  );
}

export default function MindMap() {
  const { data, error } = useApi<Cluster[]>("/pets/me/clusters", 60000);
  const [sel, setSel] = useState<string | null>(null);
  const { data: mems } = useApi<Memory[]>(sel ? `/pets/me/memories?cluster_id=${sel}&limit=30` : null);
  const W = 900,
    H = 560;
  const bubbles = useMemo(() => layout((data || []).filter((c) => c.size > 0 || c.seed), W, H), [data]);
  const cl = data?.find((c) => c.id === sel);

  return (
    <div>
      <PageHeader title="Mind map" subtitle="Topics it has read about. Size = memories; colour = learning progress (pink: getting better at predicting; violet: getting worse; grey: mastered or noise)." />
      <ErrorNote error={error} />
      {data && data.length === 0 && <Empty>No topics yet.</Empty>}
      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <Card className="overflow-hidden !p-2">
          <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
            {bubbles.map((b) => (
              <g key={b.id} onClick={() => setSel(b.id)} className="cursor-pointer">
                <circle cx={b.x} cy={b.y} r={b.r} fill={lpColor(b.lp)} stroke={sel === b.id ? "var(--accent)" : b.seed ? "var(--muted)" : "var(--line)"} strokeWidth={sel === b.id ? 3 : 1} strokeDasharray={b.seed && b.size === 0 ? "4 4" : undefined} />
                <foreignObject x={b.x - b.r * 0.85} y={b.y - b.r * 0.6} width={b.r * 1.7} height={b.r * 1.2}>
                  <div className="flex h-full w-full flex-col items-center justify-center text-center leading-tight" style={{ fontSize: Math.max(10, Math.min(14, b.r / 4)) }}>
                    <span className="line-clamp-2 font-medium">{b.label}</span>
                    <span className="text-[10px] text-muted">{b.size}</span>
                  </div>
                </foreignObject>
              </g>
            ))}
          </svg>
        </Card>
        <Card title={cl ? cl.label : "Pick a topic"}>
          {cl ? (
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap gap-2">
                <Pill tone="accent">LP {cl.lp.toFixed(3)}</Pill>
                <Pill>{cl.size} memories</Pill>
                <Pill>{cl.visits} reads</Pill>
                {cl.seed && <Pill>interest</Pill>}
              </div>
              <div>
                <div className="label mb-1">Prediction error over time</div>
                <Spark xs={cl.error_history} />
              </div>
              <ul className="space-y-2">
                {(mems || []).map((m) => (
                  <li key={m.id} className="border-t border-line pt-2">
                    <div className="font-medium">{m.title}</div>
                    <div className="line-clamp-3 text-xs text-muted">{m.content}</div>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-muted">Click a bubble to see what the pet knows about it and how its predictions improved.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
