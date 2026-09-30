"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorNote, PageHeader, Pill, StateBadge, fmtTime } from "@/components/ui";
import { api, postStream } from "@/lib/api";
import { useApi, useLive } from "@/lib/hooks";

type Msg = {
  id: string;
  sender: "owner" | "pet";
  content: string;
  t: string;
  recalled?: { id: string; title?: string }[];
  proactive?: string | null;
  link?: { kind: string; id: string } | null;
  pending?: boolean;
};

export default function ChatPage() {
  const { data: st } = useApi<any>("/pets/me/status", 30000);
  const { data: initial, error } = useApi<Msg[]>("/pets/me/messages?conversation_id=main&limit=200");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [mem, setMem] = useState<any>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (initial) setMsgs(initial);
    api("/pets/me/messages/read", { method: "POST" }).catch(() => {});
  }, [initial]);

  useEffect(() => {
    // braces matter: newer browsers return a Promise from scrollIntoView, which React would treat as a cleanup
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  useLive(
    useCallback((e) => {
      if (e.kind === "message" && e.data.proactive) {
        setMsgs((m) => (m.some((x) => x.id === e.data.id) ? m : [...m, e.data as Msg]));
        api("/pets/me/messages/read", { method: "POST" }).catch(() => {});
      }
    }, []),
  );

  const send = async () => {
    const message = text.trim();
    if (!message || busy) return;
    setText("");
    setBusy(true);
    const now = new Date().toISOString();
    const replyId = `pending-${now}`;
    setMsgs((m) => [...m, { id: `o-${now}`, sender: "owner", content: message, t: now }, { id: replyId, sender: "pet", content: "", t: now, pending: true }]);
    try {
      await postStream("/pets/me/chat", { message, conversation_id: "main", stream: true }, (event, data) => {
        if (event === "token") setMsgs((m) => m.map((x) => (x.id === replyId ? { ...x, content: x.content + data.text } : x)));
        if (event === "done")
          setMsgs((m) => m.map((x) => (x.id === replyId ? { ...x, id: data.reply_id || replyId, content: data.reply, recalled: data.recalled, pending: false } : x)));
      });
    } catch (e: any) {
      setMsgs((m) => m.map((x) => (x.id === replyId ? { ...x, content: `(couldn't reach the pet: ${e.message})`, pending: false } : x)));
    } finally {
      setBusy(false);
    }
  };

  const showMemory = async (id: string) => {
    if (open === id) return setOpen(null);
    setOpen(id);
    setMem(null);
    try {
      setMem(await api(`/memories/${id}`));
    } catch {
      setMem({ content: "(forgotten)" });
    }
  };

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col md:h-[calc(100vh-4rem)]">
      <PageHeader title={`Chat${st ? ` with ${st.pet.name}` : ""}`} subtitle={st && <span className="inline-flex items-center gap-2"><StateBadge state={st.state} /> {st.state === "asleep" ? "It’s asleep — it may mumble or wake up grumpy." : st.drives.energy < 15 ? "Low on energy: replies will be short and sleepy." : ""}</span>} />
      <ErrorNote error={error} />
      <div className="card flex-1 space-y-3 overflow-y-auto p-4">
        {msgs.length === 0 && <p className="text-center text-sm text-muted">Say hi. The pet remembers what it reads and what you tell it.</p>}
        {msgs.map((m) => (
          <div key={m.id} className={`flex ${m.sender === "owner" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] md:max-w-[70%]`}>
              {m.proactive && <div className="mb-1 text-[11px] text-muted">spoke first · {m.proactive.replace("_", " ")}</div>}
              <div
                className={`rounded-2xl px-3.5 py-2 text-[15px] leading-relaxed whitespace-pre-wrap ${
                  m.sender === "owner" ? "rounded-br-sm bg-accent text-white" : "rounded-bl-sm bg-panel-2"
                }`}
              >
                {m.content || (m.pending ? <span className="animate-pulse text-muted">thinking…</span> : "")}
              </div>
              {m.link?.kind === "dream" && (
                <Link href="/dreams" className="mt-1 inline-block text-xs text-accent">see the dream →</Link>
              )}
              {m.recalled && m.recalled.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {m.recalled.map((r) => (
                    <button key={r.id} onClick={() => showMemory(r.id)} className={`rounded-full border px-2 py-0.5 text-[11px] ${open === r.id ? "border-accent text-accent" : "border-line text-muted hover:text-ink"}`}>
                      ↺ {r.title || "memory"}
                    </button>
                  ))}
                </div>
              )}
              {m.recalled?.some((r) => r.id === open) && (
                <div className="mt-2 rounded-xl border border-line bg-panel p-3 text-sm">
                  {mem ? (
                    <>
                      <div className="mb-1 flex items-center gap-2">
                        <b>{mem.title}</b>
                        {mem.kind && <Pill>{mem.kind}</Pill>}
                      </div>
                      <p className="text-muted">{mem.content}</p>
                      {mem.source_url && (
                        <a href={mem.source_url} target="_blank" rel="noreferrer" className="mt-1 block truncate text-xs text-accent">{mem.source_url}</a>
                      )}
                    </>
                  ) : (
                    <span className="text-muted">…</span>
                  )}
                </div>
              )}
              <div className={`mt-0.5 text-[10px] text-muted ${m.sender === "owner" ? "text-right" : ""}`}>{fmtTime(m.t, st?.timezone)}</div>
            </div>
          </div>
        ))}
        <div ref={bottom} />
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input className="field flex-1" placeholder="Tell it something, or ask what it learned…" value={text} onChange={(e) => setText(e.target.value)} disabled={busy} autoFocus />
        <button className="btn btn-primary" disabled={busy || !text.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
