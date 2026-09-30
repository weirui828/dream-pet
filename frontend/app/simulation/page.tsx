"use client";

import { useMemo, useState } from "react";
import DreamView from "@/components/DreamView";
import DriveChart from "@/components/DriveChart";
import { Card, Empty, ErrorNote, PageHeader, Pill } from "@/components/ui";
import { api, type Dream, type Sample } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Run = { id: string; name: string; status: string; created_at: string; passed?: boolean; progress?: any; headline?: any };

const STATUS_TONE: Record<string, "good" | "bad" | "warn" | "accent" | "default"> = {
  passed: "good",
  failed_assertions: "warn",
  failed: "bad",
  running: "accent",
};

export default function Simulation() {
  const { data: scenarios } = useApi<any[]>("/scenarios");
  const { data: runs, error, reload } = useApi<Run[]>("/sim/runs", 3000);
  const [yamlEdit, setYaml] = useState<string | null>(null);
  const [fileSel, setFile] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [cmp, setCmp] = useState<string[]>([]);

  const file = fileSel ?? scenarios?.[0]?.file ?? "";
  const yaml = yamlEdit ?? scenarios?.find((x) => x.file === file)?.yaml ?? "";

  const start = async () => {
    setStarting(true);
    setMsg(null);
    try {
      const r = await api<{ run_id: string }>("/sim/runs", { method: "POST", json: { scenario_yaml: yaml } });
      setSel(r.run_id);
      reload();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setStarting(false);
    }
  };

  return (
    <div>
      <PageHeader title="Simulation" subtitle="The same graphs on a simulated clock with fake or replayed providers: a week of pet life in seconds, reproducibly, at zero cost." />
      <ErrorNote error={error} />
      <div className="grid gap-4 xl:grid-cols-[380px_1fr]">
        <div className="space-y-4">
          <Card title="Run a scenario">
            <select className="field mb-2" value={file} onChange={(e) => { setFile(e.target.value); setYaml(scenarios?.find((s) => s.file === e.target.value)?.yaml || ""); }}>
              {(scenarios || []).map((s) => <option key={s.file} value={s.file}>{s.name}</option>)}
            </select>
            <textarea className="field min-h-64 font-mono text-xs" value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} />
            {msg && <p className="mt-2 text-sm text-bad">{msg}</p>}
            <button className="btn btn-primary mt-2 w-full justify-center" onClick={start} disabled={starting || !yaml.trim()}>
              {starting ? "Starting…" : "Run"}
            </button>
          </Card>
          <Card title="Runs" actions={cmp.length === 2 && <button className="btn !py-1 text-xs" onClick={() => setSel(`cmp:${cmp[0]}:${cmp[1]}`)}>Compare 2</button>}>
            {runs && runs.length === 0 && <p className="text-sm text-muted">No runs yet.</p>}
            <ul className="max-h-[28rem] space-y-1.5 overflow-y-auto">
              {(runs || []).map((r) => (
                <li key={r.id} className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 text-sm ${sel === r.id ? "border-accent" : "border-line"}`}>
                  <input type="checkbox" checked={cmp.includes(r.id)} onChange={(e) => setCmp((c) => (e.target.checked ? [...c.filter((x) => x !== r.id), r.id].slice(-2) : c.filter((x) => x !== r.id)))} title="select to compare" />
                  <button className="min-w-0 flex-1 text-left" onClick={() => setSel(r.id)}>
                    <div className="truncate font-medium">{r.name}</div>
                    <div className="truncate font-mono text-[10px] text-muted">{r.id}</div>
                  </button>
                  {r.status === "running" && r.progress ? (
                    <span className="text-xs text-accent tabular-nums">{Math.round((r.progress.fraction || 0) * 100)}%</span>
                  ) : (
                    <Pill tone={STATUS_TONE[r.status] || "default"}>{r.status.replace("_", " ")}</Pill>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="min-w-0">
          {!sel && <Empty>Pick a run, or start one. Tick two runs to compare them.</Empty>}
          {sel?.startsWith("cmp:") && <Compare a={sel.split(":")[1]} b={sel.split(":")[2]} />}
          {sel && !sel.startsWith("cmp:") && <RunDetail id={sel} onPromoted={() => setMsg("Promoted to the live pet.")} />}
        </div>
      </div>
    </div>
  );
}

function RunDetail({ id, onPromoted }: { id: string; onPromoted: () => void }) {
  const { data: run } = useApi<any>(`/sim/runs/${id}`, 2500);
  const done = run && run.status !== "running";
  const { data: samples } = useApi<Sample[]>(done ? `/sim/runs/${id}/samples` : null);
  const { data: events } = useApi<any[]>(done ? `/sim/runs/${id}/events?types=read,dream_saved,chat,persona_drift,proactive,state_change,topic_picked&limit=5000` : null);
  const { data: dreams } = useApi<Dream[]>(done ? `/sim/runs/${id}/dreams` : null);
  const [cursorSel, setCursor] = useState<number | null>(null);
  const [dreamSel, setDreamSel] = useState<string | null>(null);
  const cursor = cursorSel ?? Math.max(0, (samples?.length ?? 1) - 1);

  const res = run?.summary;
  const s = res?.summary;
  const at = samples?.[cursor];
  const nearby = useMemo(() => {
    if (!at || !events) return [];
    const t = new Date(at.t).getTime();
    return events.filter((e) => Math.abs(new Date(e.t).getTime() - t) < 3 * 3600e3).slice(0, 14);
  }, [at, events]);

  if (!run) return null;
  if (run.status === "running") {
    const p = run.progress || {};
    return (
      <Card title={run.name}>
        <div className="mb-2 text-sm text-muted">Simulating{p.day ? ` ${p.day}` : ""}…</div>
        <div className="h-2 overflow-hidden rounded-full bg-panel-2">
          <div className="h-full bg-accent transition-all" style={{ width: `${Math.round((p.fraction || 0) * 100)}%` }} />
        </div>
      </Card>
    );
  }
  if (run.status === "failed") return <Card title={run.name}><p className="text-sm text-bad">{run.error}</p></Card>;
  if (!s) return null;
  const dream = dreams?.find((d) => d.id === dreamSel) || dreams?.[0];

  return (
    <div className="space-y-4">
      <Card
        title={`${s.scenario} · seed ${s.seed} · ${s.persona}`}
        actions={
          <button className="btn !py-1 text-xs" title="Apply this scenario's drive overrides and persona overrides to the live pet" onClick={async () => { await api(`/sim/runs/${id}/promote`, { method: "POST" }); onPromoted(); }}>
            Promote settings to live pet
          </button>
        }
      >
        <DriveChart
          samples={samples || []}
          energyMax={Math.max(10, ...(samples || []).map((x) => x.energy))}
          cursor={cursor}
          onCursor={setCursor}
          markers={(events || []).filter((e) => e.type === "dream_saved" || e.type === "chat").map((e) => ({ t: e.t, label: e.type === "chat" ? `chat: ${e.payload.message}` : `dream: ${e.payload.title}`, color: e.type === "chat" ? "var(--accent)" : "var(--curiosity)" }))}
          height={240}
        />
        {samples && samples.length > 1 && (
          <div className="mt-3">
            <div className="label mb-1">Time scrubber</div>
            <input type="range" className="w-full" min={0} max={samples.length - 1} value={cursor} onChange={(e) => setCursor(Number(e.target.value))} />
            {at && (
              <div className="mt-2 grid gap-3 md:grid-cols-[200px_1fr]">
                <div className="text-sm">
                  <div className="font-medium">{new Date(at.t).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC</div>
                  <div className="capitalize text-muted">{at.state}</div>
                  <div><span className="text-boredom">boredom {at.boredom.toFixed(2)}</span> · <span className="text-energy">energy {Math.round(at.energy)}</span></div>
                </div>
                <ul className="max-h-40 space-y-0.5 overflow-y-auto text-xs">
                  {nearby.map((e) => (
                    <li key={e.id} className={new Date(e.t) <= new Date(at.t) ? "" : "text-muted"}>
                      <span className="font-mono text-muted">{new Date(e.t).toISOString().slice(11, 16)}</span> {e.type.replace("_", " ")}{" "}
                      {e.payload.title || e.payload.label || e.payload.field || (e.payload.to ? `${e.payload.from}→${e.payload.to}` : "") || e.payload.message || ""}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Summary">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            {[
              ["explore sessions", s.explorations],
              ["articles read", s.reads],
              ["chat turns", s.chats],
              ["dreams", `${s.dreams}${s.weak_dreams ? ` (${s.weak_dreams} weak)` : ""}`],
              ["topic clusters", s.clusters],
              ["topic diversity", `${s.topic_entropy_bits} bits`],
              ["satisfaction / read", s.mean_satisfaction_per_read],
              ["prediction error", s.mean_prediction_error],
              ["persona changes", s.persona_changes],
              ["proactive messages", s.proactive_messages],
              ["estimated real cost", `$${s.est_usd}`],
              ["actual spend", `$${s.usd}`],
            ].map(([k, v]) => (
              <div key={k as string} className="contents">
                <dt className="text-muted">{k}</dt>
                <dd className="text-right font-mono tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-3 text-xs text-muted">Top topics: {s.top_topics.map((t: any) => `${t.label} (${t.size})`).join(", ")}</div>
        </Card>
        <Card title="Assertions">
          {res.assertions.length === 0 && <p className="text-sm text-muted">None.</p>}
          <ul className="space-y-1.5 text-sm">
            {res.assertions.map((a: any, i: number) => (
              <li key={i} className="flex items-center gap-2">
                <span className={a.ok ? "text-good" : "text-bad"}>{a.ok ? "✓" : "✗"}</span>
                <span className="font-mono text-xs">{a.assert}</span>
                <span className="ml-auto font-mono text-xs text-muted">want {JSON.stringify(a.want)} · got {JSON.stringify(a.got)}</span>
              </li>
            ))}
          </ul>
          {res.chats?.length > 0 && (
            <div className="mt-4 space-y-2 border-t border-line pt-3 text-sm">
              {res.chats.map((c: any, i: number) => (
                <div key={i}>
                  <div className="text-xs text-muted">day {c.day} · {c.mode}</div>
                  <div><b>you:</b> {c.message}</div>
                  <div><b>pet:</b> {c.reply}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {dreams && dreams.length > 0 && (
        <Card title="Dreams">
          <div className="mb-4 flex flex-wrap gap-1.5">
            {dreams.map((d) => (
              <button key={d.id} onClick={() => setDreamSel(d.id)} className={`rounded-lg border px-2 py-1 text-xs ${dream?.id === d.id ? "border-accent text-accent" : "border-line"}`}>
                {d.night}
              </button>
            ))}
          </div>
          {dream && <DreamView dream={dream} />}
        </Card>
      )}
    </div>
  );
}

function Compare({ a, b }: { a: string; b: string }) {
  const { data, error } = useApi<any>(`/sim/compare?a=${a}&b=${b}`);
  const { data: sa } = useApi<Sample[]>(`/sim/runs/${a}/samples?every=3`);
  const { data: sb } = useApi<Sample[]>(`/sim/runs/${b}/samples?every=3`);
  // align run B onto run A's start time so the curves overlay by elapsed time
  const shifted = useMemo(() => {
    if (!sa?.length || !sb?.length) return undefined;
    const off = new Date(sa[0].t).getTime() - new Date(sb[0].t).getTime();
    return sb.map((x) => ({ ...x, t: new Date(new Date(x.t).getTime() + off).toISOString() }));
  }, [sa, sb]);
  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      <Card title={`A: ${a}   vs   B: ${b} (dashed)`}>
        <DriveChart samples={sa || []} overlay={shifted ? { samples: shifted, label: "run B" } : undefined} energyMax={Math.max(10, ...(sa || []).map((x) => x.energy), ...(sb || []).map((x) => x.energy))} height={240} />
      </Card>
      {data && (
        <Card title="Metrics">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="py-1 font-medium">metric</th>
                <th className="text-right font-medium">A</th>
                <th className="text-right font-medium">B</th>
                <th className="text-right font-medium">Δ</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r: any) => (
                <tr key={r.metric} className="border-t border-line">
                  <td className="py-1.5">{r.label}</td>
                  <td className="text-right font-mono tabular-nums">{r.a}</td>
                  <td className="text-right font-mono tabular-nums">{r.b}</td>
                  <td className={`text-right font-mono tabular-nums ${r.delta > 0 ? "text-good" : r.delta < 0 ? "text-bad" : "text-muted"}`}>{r.delta > 0 ? "+" : ""}{r.delta}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
