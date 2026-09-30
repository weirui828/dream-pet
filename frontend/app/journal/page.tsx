"use client";

import { useState } from "react";
import { Card, Empty, ErrorNote, PageHeader, Pill, fmtTime } from "@/components/ui";
import { api, type Memory } from "@/lib/api";
import { useApi, useDebounced } from "@/lib/hooks";

const KINDS = [
  { id: "episodic", label: "Learned" },
  { id: "insight", label: "Insights" },
  { id: "chat", label: "Chats" },
  { id: "dream", label: "Dreams" },
];

function Meter({ label, v, color }: { label: string; v?: number; color: string }) {
  if (v == null) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-muted" title={`${label} ${v.toFixed(3)}`}>
      {label}
      <span className="inline-block h-1.5 w-12 rounded-full bg-panel-2">
        <span className="block h-full rounded-full" style={{ width: `${Math.min(1, v) * 100}%`, background: color }} />
      </span>
    </span>
  );
}

export default function Journal() {
  const [kind, setKind] = useState("episodic");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(50);
  const setDebounced = useDebounced<string>(setQuery, 350);
  const { data: status } = useApi<any>("/pets/me/status");
  const { data, error, reload } = useApi<Memory[]>(`/pets/me/memories?kind=${kind}&limit=${limit}${query ? `&q=${encodeURIComponent(query)}` : ""}`);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const days: Record<string, Memory[]> = {};
  for (const m of data || []) {
    const d = new Date(m.created_at).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", timeZone: status?.timezone });
    (days[d] ||= []).push(m);
  }

  return (
    <div>
      <PageHeader title="Journal" subtitle="What it learned, where it read it, and how surprising it was." />
      <ErrorNote error={error} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {KINDS.map((k) => (
          <button key={k.id} onClick={() => setKind(k.id)} className={`btn ${kind === k.id ? "btn-primary" : ""}`}>
            {k.label}
          </button>
        ))}
        <input
          className="field ml-auto max-w-xs"
          placeholder="Search memories…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setDebounced(e.target.value);
          }}
        />
      </div>
      {data && data.length === 0 && <Empty>Nothing here yet. The pet explores when it gets bored.</Empty>}
      <div className="space-y-6">
        {Object.entries(days).map(([day, ms]) => (
          <div key={day}>
            <h2 className="label mb-2">{day}</h2>
            <div className="space-y-2">
              {ms.map((m) => (
                <Card key={m.id} className="!p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{m.title || m.kind}</span>
                        {m.cluster && <Pill tone="accent">{m.cluster}</Pill>}
                        {m.meta?.topic && m.meta.topic !== m.cluster && <Pill>via {m.meta.topic}</Pill>}
                      </div>
                      {m.source_url && (
                        <a href={m.source_url} target="_blank" rel="noreferrer" className="block truncate text-xs text-accent">
                          {m.source_url}
                        </a>
                      )}
                    </div>
                    <span className="text-xs text-muted">{fmtTime(m.created_at, status?.timezone)}</span>
                  </div>
                  {editing === m.id ? (
                    <div className="mt-2 space-y-2">
                      <textarea className="field min-h-24" value={draft} onChange={(e) => setDraft(e.target.value)} />
                      <div className="flex gap-2">
                        <button className="btn btn-primary" onClick={async () => { await api(`/memories/${m.id}`, { method: "PATCH", json: { content: draft } }); setEditing(null); reload(); }}>Save</button>
                        <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <p className="mt-2 text-sm leading-relaxed whitespace-pre-wrap">{m.content}</p>
                  )}
                  {m.meta?.prediction && (
                    <p className="mt-2 text-xs text-muted">
                      <span className="font-semibold">It guessed:</span> {m.meta.prediction}
                    </p>
                  )}
                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
                    {m.kind === "episodic" && (
                      <>
                        <Meter label="surprise" v={m.surprise} color="var(--curiosity)" />
                        <Meter label="novelty" v={m.meta?.novelty} color="var(--accent)" />
                        <Meter label="learnability" v={m.meta?.learnability} color="var(--energy)" />
                        <Meter label="satisfaction" v={m.meta?.satisfaction != null ? m.meta.satisfaction * 3 : undefined} color="var(--boredom)" />
                      </>
                    )}
                    <span className="ml-auto flex gap-1">
                      <button className="rounded px-1.5 text-xs text-muted hover:text-ink" onClick={() => { setEditing(m.id); setDraft(m.content); }}>edit</button>
                      <button
                        className="rounded px-1.5 text-xs text-muted hover:text-bad"
                        onClick={async () => {
                          if (!window.confirm("Forget this memory for good?")) return;
                          await api(`/memories/${m.id}`, { method: "DELETE" });
                          reload();
                        }}
                      >
                        forget
                      </button>
                    </span>
                  </div>
                </Card>
              ))}
            </div>
          </div>
        ))}
      </div>
      {data && data.length >= limit && (
        <div className="mt-6 text-center">
          <button className="btn" onClick={() => setLimit((l) => l + 50)}>Show more</button>
        </div>
      )}
    </div>
  );
}
