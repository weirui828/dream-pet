"use client";

import { useState } from "react";
import DreamView from "@/components/DreamView";
import { Empty, ErrorNote, PageHeader, Pill } from "@/components/ui";
import { api, type Dream } from "@/lib/api";
import { useApi } from "@/lib/hooks";

export default function Dreams() {
  const { data, error, reload } = useApi<Dream[]>("/pets/me/dreams?limit=90", 60000);
  const [picked, setSel] = useState<string | null>(null);
  const sel = picked ?? data?.[0]?.id ?? null;
  const dream = data?.find((d) => d.id === sel);

  return (
    <div>
      <PageHeader title="Dreams" subtitle="Every night the pet recombines real memories into a dream. Every scene cites where it came from." />
      <ErrorNote error={error} />
      {data && data.length === 0 && <Empty>No dreams yet. The first one arrives after the pet’s first night.</Empty>}
      {data && data.length > 0 && (
        <div className="grid gap-6 lg:grid-cols-[240px_1fr]">
          <aside className="flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible">
            {data.map((d) => (
              <button
                key={d.id}
                onClick={() => setSel(d.id)}
                className={`shrink-0 rounded-xl border px-3 py-2 text-left transition-colors ${d.id === sel ? "border-accent bg-accent-soft/50" : "border-line bg-panel hover:border-accent/50"}`}
              >
                <div className="text-xs text-muted">{d.night}</div>
                <div className="line-clamp-1 text-sm font-medium">{d.title}</div>
                <div className="mt-1 flex gap-1">
                  <Pill>{d.mood}</Pill>
                  {d.video?.status === "done" && <Pill tone="good">video</Pill>}
                </div>
              </button>
            ))}
          </aside>
          <div className="min-w-0">
            {dream && (
              <DreamView
                dream={dream}
                onRender={async () => {
                  await api(`/dreams/${dream.id}/render`, { method: "POST" });
                  setTimeout(reload, 1500);
                }}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
