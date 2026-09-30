"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import DriveChart from "@/components/DriveChart";
import { Avatar, Card, ErrorNote, Gauge, PageHeader, Pill, StateBadge, timeAgo } from "@/components/ui";
import { api, mediaUrl, type Sample } from "@/lib/api";
import { useApi, useLive } from "@/lib/hooks";

const RANGES = [
  { label: "6h", hours: 6, title: "Last 6 hours" },
  { label: "24h", hours: 24, title: "Last 24 hours" },
  { label: "3d", hours: 72, title: "Last 3 days" },
  { label: "7d", hours: 168, title: "Last 7 days" },
  { label: "30d", hours: 720, title: "Last 30 days" },
];

const EVENT_LABEL: Record<string, (p: any) => string | null> = {
  read: (p) => `read “${p.title}” (${p.cluster}) · satisfaction ${p.satisfaction}`,
  topic_picked: (p) => `wondered about ${p.label}${p.wildcard ? " (wildcard)" : ""}`,
  chat: () => "chatted with you",
  dream_saved: (p) => `dreamed “${p.title}” (${p.mood})`,
  state_change: (p) => `${p.from} → ${p.to} · ${p.reason}`,
  persona_drift: (p) => `drift: ${p.field} ${p.old} → ${p.new}`,
  video_ready: () => "dream video is ready",
  proactive: (p) => `messaged you (${p.trigger})`,
  budget_refused: (p) => `budget guard refused a ${p.role} call`,
  explore_end: (p) => `stopped exploring after ${p.reads} read(s): ${p.reason}`,
};

export default function Home() {
  const { data: st, error, reload } = useApi<any>("/pets/me/status", 30000);
  const [range, setRange] = useState(RANGES[1]);
  const { data: samples, setData: setSamples } = useApi<Sample[]>(`/pets/me/samples?hours=${range.hours}`, 120000);
  const { data: events, reload: reloadEvents } = useApi<any[]>("/pets/me/events?hours=48&limit=60&types=read,topic_picked,chat,dream_saved,state_change,persona_drift,video_ready,proactive,budget_refused,explore_end", 60000);
  const { data: dreams } = useApi<any[]>("/pets/me/dreams?limit=1");
  const [live, setLive] = useState<{ drives?: any; activity?: string }>({});

  const connected = useLive(
    useCallback(
      (e) => {
        if (e.kind === "drives") {
          setLive((l) => ({ ...l, drives: e.data }));
          // only append points that move forward in time; anything else would draw a line backwards
          setSamples((s) => (s && (!s.length || e.data.t > s[s.length - 1].t) ? [...s, e.data as Sample] : s));
        } else if (e.kind === "event") {
          if (e.data.type === "activity") setLive((l) => ({ ...l, activity: e.data.payload.line }));
          if (EVENT_LABEL[e.data.type]) reloadEvents();
        } else if (e.kind === "state") reload();
      },
      [reload, reloadEvents, setSamples],
    ),
  );

  const d = live.drives || st?.drives;
  const ex = st?.explain;
  const dream = dreams?.[0];

  return (
    <div>
      <ErrorNote error={error} />
      {st && (
        <>
          <div className="mb-6 flex flex-wrap items-center gap-5">
            <Avatar url={mediaUrl(st.pet.avatar_url)} name={st.pet.name} state={d?.state || st.state} size={104} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-3xl font-semibold tracking-tight">{st.pet.name}</h1>
                <StateBadge state={d?.state || d?.lifecycle || st.state} />
                <span className={`h-2 w-2 rounded-full ${connected ? "bg-good" : "bg-muted"}`} title={connected ? "live" : "reconnecting"} />
              </div>
              <p className="mt-1 text-lg text-muted">{live.activity || st.activity?.line || "…"}</p>
              <p className="mt-1 text-xs text-muted">
                bedtime {st.schedule.bedtime} · wakes {st.schedule.wake_time} · {st.timezone} · {st.counts.memories} memories · {st.counts.dreams} dreams
              </p>
            </div>
          </div>

          {st.pending_video_approvals?.length > 0 && (
            <Card className="mb-4 border-warn/50" title="Video waiting for approval">
              {st.pending_video_approvals.map((j: any) => (
                <div key={j.job_id} className="flex flex-wrap items-center gap-3 text-sm">
                  Rendering last night’s dream would cost about <b>${j.est_usd.toFixed(2)}</b>.
                  <button className="btn btn-primary" onClick={async () => { await api(`/video_jobs/${j.job_id}/approve`, { method: "POST", json: { approved: true } }); reload(); }}>Render video</button>
                  <button className="btn" onClick={async () => { await api(`/video_jobs/${j.job_id}/approve`, { method: "POST", json: { approved: false } }); reload(); }}>Stills only (free)</button>
                </div>
              ))}
            </Card>
          )}

          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Drives" className="lg:col-span-1">
              <div className="space-y-5">
                <Gauge label="Boredom" value={d?.boredom ?? 0} color="var(--boredom)" marks={ex && [{ at: ex.band.theta_low, label: "stop" }, { at: ex.band.theta_high, label: "explore" }]} />
                <Gauge label="Energy" value={d?.energy ?? 0} max={ex?.energy_max || 100} color="var(--energy)" />
                <Gauge label="Curiosity (learning progress)" value={Math.max(0, Math.min(1, (d?.curiosity ?? 0) * 5))} color="var(--curiosity)" />
                {ex?.hours_until_bored != null && ex.hours_until_bored > 0 && (
                  <p className="text-xs text-muted">Bored enough to explore in ~{ex.hours_until_bored.toFixed(1)} h.</p>
                )}
                <p className="text-xs text-muted">
                  Spent today ${st.spent_today.toFixed(3)} of ${st.daily_cap.toFixed(2)} hard cap
                </p>
              </div>
            </Card>
            <Card
              title={range.title}
              className="lg:col-span-2"
              actions={
                <div className="flex gap-1">
                  {RANGES.map((r) => (
                    <button
                      key={r.label}
                      onClick={() => setRange(r)}
                      className={`rounded-md px-2 py-0.5 text-xs ${r === range ? "bg-accent-soft font-semibold text-accent" : "text-muted hover:text-ink"}`}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              }
            >
              <DriveChart samples={samples || []} energyMax={ex?.energy_max} band={ex?.band} tz={st.timezone} />
            </Card>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card title="Last night’s dream" className="lg:col-span-1" actions={<Link href="/dreams" className="text-xs text-accent">all dreams →</Link>}>
              {dream ? (
                <Link href="/dreams" className="block">
                  {dream.video?.poster_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={mediaUrl(dream.video.poster_url)} alt="" className="mb-3 aspect-video w-full rounded-xl object-cover" />
                  )}
                  <div className="font-medium">{dream.title}</div>
                  <div className="mt-1 line-clamp-4 text-sm text-muted">{dream.narrative}</div>
                  <div className="mt-2 flex gap-2">
                    <Pill tone="accent">{dream.mood}</Pill>
                    <Pill>{dream.elements.length} scenes</Pill>
                    {dream.weak && <Pill tone="warn">weak</Pill>}
                  </div>
                </Link>
              ) : (
                <p className="text-sm text-muted">No dreams yet — they come after the first night.</p>
              )}
            </Card>
            <Card title="What’s been happening" className="lg:col-span-2">
              <ul className="max-h-80 space-y-1.5 overflow-y-auto pr-1 text-sm">
                {(events || [])
                  .map((e) => ({ e, text: EVENT_LABEL[e.type]?.(e.payload) }))
                  .filter((x) => x.text)
                  .map(({ e, text }) => (
                    <li key={e.id} className="flex gap-3">
                      <span className="w-16 shrink-0 text-right text-xs text-muted tabular-nums">{timeAgo(e.t, st.now ? new Date(st.now) : undefined)}</span>
                      <span className={e.type === "budget_refused" ? "text-bad" : ""}>{text}</span>
                    </li>
                  ))}
                {events && events.length === 0 && <li className="text-muted">Quiet so far.</li>}
              </ul>
            </Card>
          </div>
        </>
      )}
      {!st && !error && <PageHeader title="Loading…" />}
    </div>
  );
}
