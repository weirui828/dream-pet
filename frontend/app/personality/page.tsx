"use client";

import { useRef, useState } from "react";
import { Avatar, Card, ErrorNote, PageHeader, Pill, fmtTime } from "@/components/ui";
import { API_BASE, api, getToken, mediaUrl } from "@/lib/api";
import { useApi, useDebounced } from "@/lib/hooks";

const TRAITS: { key: string; label: string; lo: string; hi: string }[] = [
  { key: "restlessness", label: "Restlessness", lo: "calm", hi: "restless" },
  { key: "openness", label: "Openness", lo: "focused", hi: "wanders" },
  { key: "depth", label: "Depth ↔ breadth", lo: "breadth", hi: "depth" },
  { key: "sociability", label: "Sociability", lo: "private", hi: "chatty" },
  { key: "stamina", label: "Stamina", lo: "tires fast", hi: "tireless" },
  { key: "dreaminess", label: "Dreaminess", lo: "literal", hi: "surreal" },
];
const TEMPERAMENTS = ["playful", "earnest", "dry", "melancholic"];

function Lock({ field, locks, toggle }: { field: string; locks: string[]; toggle: (f: string) => void }) {
  const on = locks.includes(field);
  return (
    <button onClick={() => toggle(field)} title={on ? "Locked: drift can't change this" : "Unlocked: nightly drift may nudge this"} className={`text-xs ${on ? "text-accent" : "text-muted/60 hover:text-muted"}`}>
      <svg viewBox="0 0 24 24" className="inline h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
        <rect x="5" y="11" width="14" height="10" rx="2" />
        <path d={on ? "M8 11V7a4 4 0 0 1 8 0v4" : "M8 11V7a4 4 0 0 1 7.5-2"} />
      </svg>
    </button>
  );
}

export default function Personality() {
  const { data, error, reload, setData } = useApi<any>("/pets/me/persona");
  const { data: presets } = useApi<any[]>("/presets");
  const { data: drives, reload: reloadDrives } = useApi<any>("/pets/me/drives");
  const { data: changes } = useApi<any[]>("/pets/me/persona/changes");
  const [p, setP] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [avatars, setAvatars] = useState<any[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const [seen, setSeen] = useState<any>(null);
  if (data && data !== seen) {
    setSeen(data);
    setP(data.persona);
  }

  const patch = async (body: any) => {
    setSaving(true);
    try {
      const out = await api("/pets/me/persona", { method: "PATCH", json: body });
      setData(out);
      reloadDrives();
      setMsg(null);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setSaving(false);
    }
  };
  const patchSoon = useDebounced<any>(patch, 500);

  const update = (body: any, soon = true) => {
    setP((x: any) => ({ ...x, ...body, traits: { ...x.traits, ...(body.traits || {}) } }));
    (soon ? patchSoon : patch)(body);
  };

  const toggleLock = (f: string) => {
    const locks = p.locks.includes(f) ? p.locks.filter((x: string) => x !== f) : [...p.locks, f];
    update({ locks }, false);
  };

  if (!p) return <div><PageHeader title="Personality" /><ErrorNote error={error} /></div>;

  return (
    <div>
      <PageHeader
        title="Personality"
        subtitle="Traits are sliders that compile into drive parameters. The pet drifts a little each night (±3 max), citing why — lock anything you want fixed."
        actions={
          <>
            <a className="btn" href={`${API_BASE}/api/v1/pets/me/persona/export${getToken() ? `?token=${getToken()}` : ""}`} download="persona.yaml">Export YAML</a>
            <button className="btn" onClick={() => fileRef.current?.click()}>Import YAML</button>
            <input ref={fileRef} type="file" accept=".yaml,.yml" className="hidden" onChange={async (e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              try {
                setData(await api("/pets/me/persona/import", { method: "POST", body: await f.text() }));
                reloadDrives();
              } catch (err: any) { setMsg(err.message); }
            }} />
          </>
        }
      />
      <ErrorNote error={error} />
      {msg && <div className="mb-4 rounded-xl bg-bad/10 p-3 text-sm text-bad">{msg}</div>}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="label mr-1">Presets</span>
        {(presets || []).map((pr) => (
          <button key={pr.id} className="btn" title={pr.interests.join(", ")} onClick={async () => {
            if (!window.confirm(`Replace the persona with the ${pr.name} preset? Memories stay.`)) return;
            setData(await api("/pets/me/persona/preset", { method: "POST", json: { name: pr.id } }));
            reloadDrives();
          }}>
            {pr.id.replace("_", " ")}
          </button>
        ))}
        {saving && <span className="text-xs text-muted">saving…</span>}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Identity">
          <div className="flex gap-4">
            <div className="flex flex-col items-center gap-2">
              <Avatar url={mediaUrl(data.avatar_url)} name={p.name} size={88} />
              <button className="text-xs text-accent" onClick={async () => setAvatars(await api("/pets/me/avatar/generate", { method: "POST" }))}>
                {data.avatar_url ? "regenerate" : "generate avatar"}
              </button>
            </div>
            <div className="grid flex-1 grid-cols-2 gap-3">
              <label className="col-span-2">
                <span className="label">Name</span>
                <input className="field" value={p.name} onChange={(e) => update({ name: e.target.value })} />
              </label>
              <label>
                <span className="label">Language (BCP 47)</span>
                <input className="field" value={p.language} onChange={(e) => update({ language: e.target.value })} />
              </label>
              <label>
                <span className="label">Timezone</span>
                <input className="field" value={p.timezone} onChange={(e) => update({ timezone: e.target.value })} />
              </label>
              <label>
                <span className="label">Bedtime</span>
                <input className="field" type="time" value={p.bedtime} onChange={(e) => update({ bedtime: e.target.value })} />
              </label>
              <label>
                <span className="label">Wakes at</span>
                <input className="field" type="time" value={p.wake_time} onChange={(e) => update({ wake_time: e.target.value })} />
              </label>
              <label>
                <span className="label">Temperament</span>
                <select className="field" value={p.temperament} onChange={(e) => update({ temperament: e.target.value }, false)}>
                  {[...new Set([...TEMPERAMENTS, p.temperament])].map((t) => <option key={t}>{t}</option>)}
                </select>
              </label>
              <label>
                <span className="label">Chat while asleep</span>
                <select className="field" value={p.sleep_chat} onChange={(e) => update({ sleep_chat: e.target.value }, false)}>
                  <option value="sleeptalk">sleep-talks</option>
                  <option value="wake">wakes up (costs energy)</option>
                </select>
              </label>
            </div>
          </div>
          {avatars && (
            <div className="mt-3">
              <div className="label mb-2">Pick an avatar</div>
              <div className="flex flex-wrap gap-2">
                {avatars.map((a) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={a.uri} src={mediaUrl(a.url)} alt="" className="h-20 w-20 cursor-pointer rounded-full border-2 border-transparent object-cover hover:border-accent" onClick={async () => { await api("/pets/me/avatar/select", { method: "POST", json: { uri: a.uri } }); setAvatars(null); reload(); }} />
                ))}
              </div>
            </div>
          )}
          <label className="mt-3 block">
            <span className="label">Self-description (the pet may revise this)</span>
            <textarea className="field min-h-16" value={p.self_description} onChange={(e) => update({ self_description: e.target.value })} />
          </label>
          <label className="mt-3 block">
            <span className="label">Visual style for dreams</span>
            <input className="field" value={p.visual_style} onChange={(e) => update({ visual_style: e.target.value })} />
          </label>
        </Card>

        <Card title="Traits">
          <div className="space-y-4">
            {TRAITS.map((t) => (
              <div key={t.key}>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 font-medium">{t.label} <Lock field={`traits.${t.key}`} locks={p.locks} toggle={toggleLock} /></span>
                  <span className="font-mono text-xs text-muted">{p.traits[t.key]}</span>
                </div>
                <input type="range" min={0} max={100} value={p.traits[t.key]} className="w-full" onChange={(e) => update({ traits: { [t.key]: Number(e.target.value) } })} />
                <div className="flex justify-between text-[10px] text-muted"><span>{t.lo}</span><span>{t.hi}</span></div>
              </div>
            ))}
          </div>
          {data.compiled && (
            <p className="mt-3 text-xs text-muted">
              Compiles to: dream walk {data.compiled.walk_hops} hop(s), dreamer temperature {data.compiled.dreamer_temperature}, up to {data.compiled.proactive_per_day} proactive messages/day, checks in after {data.compiled.silence_hours} h of silence.
            </p>
          )}
        </Card>

        <Card title="Interests and aversions">
          <ul className="space-y-2">
            {p.interests.map((it: any, i: number) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <span className="w-40 truncate">{it.topic}</span>
                <input type="range" min={0} max={100} value={it.weight} className="flex-1" onChange={(e) => {
                  const interests = p.interests.map((x: any, j: number) => (j === i ? { ...x, weight: Number(e.target.value) } : x));
                  update({ interests });
                }} />
                <span className="w-8 text-right font-mono text-xs text-muted">{Math.round(it.weight)}</span>
                <Lock field={`interests.${it.topic}`} locks={p.locks} toggle={toggleLock} />
                <button className="text-xs text-muted hover:text-bad" onClick={() => update({ interests: p.interests.filter((_: any, j: number) => j !== i) }, false)}>✕</button>
              </li>
            ))}
          </ul>
          <form className="mt-3 flex gap-2" onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            const topic = String(f.get("topic") || "").trim();
            if (!topic) return;
            update({ interests: [...p.interests, { topic, weight: 50 }] }, false);
            e.currentTarget.reset();
          }}>
            <input name="topic" className="field" placeholder="Add an interest, e.g. volcanoes" />
            <button className="btn">Add</button>
          </form>
          <label className="mt-4 block">
            <span className="label">Aversions (comma-separated; never explored)</span>
            <input className="field" defaultValue={p.aversions.join(", ")} onBlur={(e) => update({ aversions: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) }, false)} />
          </label>
        </Card>

        <Card title="Drift changelog">
          {changes && changes.length === 0 && <p className="text-sm text-muted">No drift yet. After each night the pet may nudge an interest or trait, citing the memories that motivated it.</p>}
          <ul className="max-h-80 space-y-2 overflow-y-auto text-sm">
            {(changes || []).map((c) => (
              <li key={c.id} className="border-t border-line pt-2 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Pill tone="accent">{c.field}</Pill>
                  <span className="font-mono text-xs">{typeof c.old === "number" ? c.old.toFixed(0) : c.old} → {typeof c.new === "number" ? c.new.toFixed(0) : c.new}</span>
                  <span className="ml-auto text-xs text-muted">{fmtTime(c.t, p.timezone, true)}</span>
                </div>
                <p className="mt-1 text-muted">“{c.reason}”</p>
                <p className="text-[11px] text-muted">cites {c.memory_ids.length} memor{c.memory_ids.length === 1 ? "y" : "ies"}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      {drives && <AdvancedDrives drives={drives} onChange={reloadDrives} />}
    </div>
  );
}

function AdvancedDrives({ drives, onChange }: { drives: any; onChange: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const set = async (key: string, value: number | null) => {
    try {
      await api("/pets/me/drives", { method: "PATCH", json: { [key]: value } });
      setErr(null);
      onChange();
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Card title="Advanced: drive parameters" className="mt-4">
      <p className="mb-3 text-sm text-muted">
        Base values come from <code>drives.yaml</code> (hot-reloaded), traits modulate them, and anything you set here overrides both. Bounds are enforced.
      </p>
      {err && <div className="mb-3 rounded-lg bg-bad/10 p-2 text-sm text-bad">{err}</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="py-1.5 pr-3 font-medium">parameter</th>
              <th className="pr-3 font-medium">base</th>
              <th className="pr-3 font-medium">effective</th>
              <th className="pr-3 font-medium">set by</th>
              <th className="pr-3 font-medium">bounds</th>
              <th className="font-medium">override</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(drives.flat as Record<string, number>).map(([k, v]) => {
              const b = drives.bounds[k];
              const over = drives.overrides[k];
              return (
                <tr key={k} className="border-t border-line">
                  <td className="py-1.5 pr-3 font-mono text-xs">{k}</td>
                  <td className="pr-3 font-mono text-xs text-muted">{drives.base[k]}</td>
                  <td className="pr-3 font-mono text-xs">{typeof v === "number" ? +v.toFixed(4) : v}</td>
                  <td className="pr-3 text-xs">{drives.trace[k] ? <Pill tone={drives.trace[k] === "override" ? "warn" : "default"}>{drives.trace[k]}</Pill> : ""}</td>
                  <td className="pr-3 text-xs text-muted">{b ? `${b.min ?? "–"} – ${b.max ?? "–"}` : ""}</td>
                  <td className="py-1">
                    <span className="flex items-center gap-1">
                      <input
                        key={`${k}-${over}`}
                        className="field !w-24 !py-1 font-mono text-xs"
                        type="number"
                        step="any"
                        defaultValue={over ?? ""}
                        placeholder="—"
                        onBlur={(e) => {
                          const val = e.target.value.trim();
                          if (val === "" && over === undefined) return;
                          set(k, val === "" ? null : Number(val));
                        }}
                      />
                      {over !== undefined && <button className="text-xs text-muted hover:text-ink" onClick={() => set(k, null)}>reset</button>}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {drives.drives_file_error && <p className="mt-2 text-xs text-bad">drives.yaml error (last good values in use): {drives.drives_file_error}</p>}
    </Card>
  );
}
