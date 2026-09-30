"use client";

import { useState } from "react";
import { Card, ErrorNote, PageHeader, Pill } from "@/components/ui";
import { API_BASE, api, getToken, setToken } from "@/lib/api";
import { useApi, useMounted } from "@/lib/hooks";

const ROLE_COLORS: Record<string, string> = {
  explorer_llm: "var(--boredom)",
  chat_llm: "var(--accent)",
  dreamer_llm: "var(--curiosity)",
  screenwriter_llm: "#8b6cc7",
  embeddings: "var(--energy)",
  search: "#5b8def",
  fetch: "#9aa0a6",
  video: "#e05d5d",
  image: "#d9a441",
};

export default function Settings() {
  const mounted = useMounted();
  const { data: s, error, reload } = useApi<any>("/settings");
  const { data: usage } = useApi<any[]>("/usage?days=14");

  const days: Record<string, Record<string, { usd: number; est: number; calls: number }>> = {};
  for (const u of usage || []) {
    (days[u.day] ||= {})[u.role] = { usd: u.usd, est: u.est_usd, calls: u.calls };
  }
  const dayKeys = Object.keys(days).sort().slice(-14);
  const maxDay = Math.max(0.01, ...dayKeys.map((d) => Object.values(days[d]).reduce((a, x) => a + Math.max(x.usd, x.est), 0)));

  return (
    <div>
      <PageHeader title="Settings" subtitle="Provider roles, budgets and usage. Keys live in env vars or the .secrets file, never in the database — this screen only shows whether a role is configured." />
      <Card title="Connection" className="mb-4">
        {mounted && <TokenForm onSaved={reload} />}
      </Card>
      <ClockCard />
      <ErrorNote error={error} />
      {s && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card title="Provider roles">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted">
                  <th className="py-1 font-medium">role</th>
                  <th className="font-medium">provider</th>
                  <th className="font-medium">model</th>
                  <th className="font-medium">mode</th>
                  <th className="font-medium">key</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(s.roles as Record<string, any>).map(([k, r]) => (
                  <tr key={k} className="border-t border-line">
                    <td className="py-1.5 font-mono text-xs">{k}</td>
                    <td>{r.provider}</td>
                    <td className="max-w-40 truncate text-xs text-muted">{s.active[k]?.model}</td>
                    <td><Pill tone={r.mode === "live" ? "good" : r.mode === "fake" ? "default" : "accent"}>{r.mode}</Pill></td>
                    <td>{r.provider === "fake" ? <span className="text-xs text-muted">n/a</span> : r.has_key ? <Pill tone="good">set</Pill> : <Pill tone="warn">none</Pill>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-muted">
              Edit roles in <code>dreampet.yaml</code> and restart. Storage: {s.database}. Tracing: {s.tracing}. Embeddings: {s.embeddings.active}
              {s.embeddings.stale > 0 && <span className="text-warn"> · re-embedding {s.embeddings.stale} memories</span>}
            </p>
          </Card>

          <Card title="Budgets">
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span>Daily hard cap (never overridable)</span><b>${s.budgets.daily_usd_hard_cap.toFixed(2)}</b></div>
              {Object.entries(s.budgets.per_role_usd as Record<string, number>).map(([k, v]) => (
                <div key={k} className="flex justify-between text-muted"><span className="font-mono text-xs">{k}</span><span>${v.toFixed(2)}/day</span></div>
              ))}
              <div className="border-t border-line pt-2 text-xs text-muted">
                Video: {s.video.frequency}, {s.video.shots} shots / {s.video.total_seconds}s, asks before spending more than ${s.video.require_approval_over_usd.toFixed(2)}.
              </div>
            </div>
          </Card>

          <Card title="Spend by day (last 14)" className="xl:col-span-2">
            {dayKeys.length === 0 && <p className="text-sm text-muted">No usage yet.</p>}
            <div className="space-y-1.5">
              {dayKeys.map((d) => {
                const roles = days[d];
                const total = Object.values(roles).reduce((a, x) => a + x.usd, 0);
                const est = Object.values(roles).reduce((a, x) => a + x.est, 0);
                return (
                  <div key={d} className="flex items-center gap-3 text-xs">
                    <span className="w-20 shrink-0 font-mono text-muted">{d.slice(5)}</span>
                    <div className="flex h-4 flex-1 overflow-hidden rounded bg-panel-2">
                      {Object.entries(roles).map(([r, x]) => (
                        <div key={r} title={`${r}: $${x.usd.toFixed(4)} (est $${x.est.toFixed(4)}), ${x.calls} calls`} style={{ width: `${(Math.max(x.usd, x.est) / maxDay) * 100}%`, background: ROLE_COLORS[r] || "var(--muted)", opacity: x.usd > 0 ? 0.9 : 0.35 }} />
                      ))}
                    </div>
                    <span className="w-28 shrink-0 text-right tabular-nums">${total.toFixed(3)} <span className="text-muted">/ est ${est.toFixed(2)}</span></span>
                  </div>
                );
              })}
            </div>
            <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-muted">
              {Object.entries(ROLE_COLORS).map(([r, c]) => (
                <span key={r} className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: c }} />{r}</span>
              ))}
              <span>faded = estimated only (fake/replay providers cost nothing)</span>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function TokenForm({ onSaved }: { onSaved: () => void }) {
  const [tok, setTok] = useState(getToken);
  const [saved, setSaved] = useState(false);
  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="min-w-64 flex-1">
        <span className="label">API</span>
        <input className="field" value={API_BASE} readOnly />
      </label>
      <label className="min-w-64 flex-1">
        <span className="label">Admin token (from .secrets → DREAMPET_ADMIN_TOKEN)</span>
        <input className="field font-mono" type="password" value={tok} onChange={(e) => { setTok(e.target.value); setSaved(false); }} />
      </label>
      <button className="btn btn-primary" onClick={() => { setToken(tok.trim()); setSaved(true); onSaved(); }}>Save</button>
      {saved && <span className="text-sm text-good">saved in this browser</span>}
    </div>
  );
}

const SPEEDS = [
  { speed: 1, label: "Real time" },
  { speed: 6, label: "6×" },
  { speed: 30, label: "30×" },
  { speed: 60, label: "60×" },
  { speed: 120, label: "120×" },
  { speed: 600, label: "600×" },
  { speed: 1200, label: "1200×" },
  { speed: 3600, label: "3600×" },
];

function dayTakes(speed: number): string {
  const s = 86400 / speed;
  if (s >= 3600) return `${+(s / 3600).toFixed(1)} h`;
  if (s >= 60) return `${+(s / 60).toFixed(1)} min`;
  return `${Math.round(s)} s`;
}

function ClockCard() {
  const { data: clk, error, setData } = useApi<any>("/clock", 5000);
  const [custom, setCustom] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const setSpeed = async (speed: number) => {
    try {
      setData(await api("/clock", { method: "PATCH", json: { speed } }));
      setErr(null);
      setCustom("");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  if (error || !clk) return null;
  return (
    <Card title="Simulation speed" className="mb-4">
      {clk.adjustable ? (
        <div className="space-y-3">
          <p className="text-sm text-muted">
            This pet lives on a simulated clock. At <b className="text-ink">{+clk.speed.toFixed(2)}×</b> a pet day takes{" "}
            <b className="text-ink">{dayTakes(clk.speed)}</b>. Pet time now:{" "}
            {new Date(clk.now).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC.
            Changes apply immediately and are kept when the demo restarts.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {SPEEDS.map((o) => (
              <button key={o.speed} className={`btn ${clk.speed === o.speed ? "btn-primary" : ""}`} onClick={() => setSpeed(o.speed)} title={`a day takes ${dayTakes(o.speed)}`}>
                {o.label}
              </button>
            ))}
            <form
              className="flex items-center gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                const v = Number(custom);
                if (v) setSpeed(v);
              }}
            >
              <input className="field !w-28" type="number" min={clk.min} max={clk.max} step="any" placeholder="custom ×" value={custom} onChange={(e) => setCustom(e.target.value)} />
              <button className="btn" disabled={!custom}>Set</button>
            </form>
          </div>
          {err && <p className="text-sm text-bad">{err}</p>}
        </div>
      ) : (
        <p className="text-sm text-muted">
          This pet runs on the real clock, in real time. For an accelerated pet run <code>dreampet demo</code>; scenario runs set their own
          speed in the scenario YAML (<code>speed: max</code>).
        </p>
      )}
    </Card>
  );
}
