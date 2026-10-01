"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import type { Cluster } from "@/components/BallMap";
import { Card, Empty, ErrorNote, PageHeader, Pill } from "@/components/ui";
import type { Memory } from "@/lib/api";
import { useApi } from "@/lib/hooks";

// three.js needs the browser (WebGL), so skip server rendering
const BallMap = dynamic(() => import("@/components/BallMap"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center text-sm text-muted">Loading the mind map…</div>,
});


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
  const shown = useMemo(() => (data || []).filter((c) => c.size > 0 || c.seed), [data]);
  const cl = data?.find((c) => c.id === sel);

  return (
    <div>
      <PageHeader title="Mind map" subtitle="Topics it has read about. Each bubble is a topic: size is how many memories it holds, colour is learning progress. Click one to look inside." />
      <ErrorNote error={error} />
      {data && data.length === 0 && <Empty>No topics yet.</Empty>}
      <div className="grid items-start gap-4 xl:grid-cols-[1fr_320px]">
        <Card className="relative overflow-hidden !p-0">
          <div className="h-[460px] md:h-[580px]">{shown.length > 0 && <BallMap clusters={shown} selected={sel} onSelect={setSel} />}</div>
          <div className="pointer-events-none absolute bottom-3 left-4 flex flex-wrap gap-3 text-[11px] text-muted">
            <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: "#f7a8c9" }} /> learning (error falling)</span>
            <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: "#a9a6f5" }} /> getting harder</span>
            <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full ring-1 ring-line" style={{ background: "#f3f0fb" }} /> mastered or noise</span>
            <span>· drag to tilt</span>
          </div>
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
              <ul className="max-h-[400px] space-y-2 overflow-y-auto pr-1">
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
