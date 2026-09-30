"use client";

import { useRef, useState } from "react";
import { Pill } from "@/components/ui";
import { mediaUrl, type Dream } from "@/lib/api";

export default function DreamView({ dream, onRender }: { dream: Dream; onRender?: () => void }) {
  const video = dream.video;
  const vref = useRef<HTMLVideoElement>(null);
  const [activeEl, setActiveEl] = useState<number | null>(null);
  const [openEl, setOpenEl] = useState<number | null>(null);

  const onTime = () => {
    const t = vref.current?.currentTime ?? 0;
    const shot = video?.shot_map.find((s) => t >= s.start && t < s.end);
    setActiveEl(shot ? shot.element_ref : null);
  };

  const seekTo = (i: number) => {
    const shot = video?.shot_map.find((s) => s.element_ref === i);
    if (shot && vref.current) {
      vref.current.currentTime = shot.start + 0.05;
      vref.current.play().catch(() => {});
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-2xl font-semibold tracking-tight">{dream.title}</h2>
          <Pill tone="accent">{dream.mood}</Pill>
          {dream.weak && <Pill tone="warn">weak dream</Pill>}
          <Pill>score {dream.score.toFixed(2)}</Pill>
        </div>
        <p className="mt-1 text-sm text-muted">Night of {new Date(dream.night + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</p>
      </div>

      {video?.status === "done" && video.url ? (
        <div>
          <video
            ref={vref}
            src={mediaUrl(video.url)}
            poster={mediaUrl(video.poster_url)}
            controls
            playsInline
            onTimeUpdate={onTime}
            className="aspect-video w-full rounded-2xl bg-black"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
            {video.shot_map.map((s) => (
              <button key={s.shot_id} onClick={() => seekTo(s.element_ref)} className={`rounded-md border px-2 py-0.5 ${activeEl === s.element_ref ? "border-accent text-accent" : "border-line"}`}>
                shot {s.shot_id} · {s.kind} · {s.start.toFixed(1)}–{s.end.toFixed(1)}s
              </button>
            ))}
            <span className="ml-auto">
              cost ${video.cost_usd.toFixed(2)} (est. ${video.est_cost_usd.toFixed(2)})
            </span>
          </div>
        </div>
      ) : (
        <div className="grid aspect-[3/1] place-items-center rounded-2xl border border-dashed border-line text-sm text-muted">
          <div className="text-center">
            {video ? (
              <>
                Video: <b>{video.status.replace("_", " ")}</b>
                {video.error && <div className="mt-1 max-w-md text-xs">{video.error}</div>}
              </>
            ) : (
              "No video for this dream."
            )}
            {onRender && video?.status !== "rendering" && (
              <div className="mt-2">
                <button className="btn" onClick={onRender}>Render video</button>
              </div>
            )}
          </div>
        </div>
      )}

      <p className="max-w-3xl text-[15px] leading-relaxed whitespace-pre-wrap">{dream.narrative}</p>

      <div>
        <h3 className="label mb-2">Scenes and the memories they came from</h3>
        <ol className="space-y-2">
          {dream.elements.map((el, i) => (
            <li key={i} className={`rounded-xl border p-3 transition-colors ${activeEl === i ? "border-accent bg-accent-soft/40" : "border-line bg-panel"}`}>
              <div className="flex gap-3">
                <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-panel-2 text-xs font-semibold">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm">{el.text}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {el.memories.map((m) => (
                      <button key={m.id} onClick={() => setOpenEl(openEl === i ? null : i)} className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted hover:text-ink">
                        ← {m.title || m.kind}
                      </button>
                    ))}
                    {video?.shot_map.some((s) => s.element_ref === i) && (
                      <button className="rounded-full px-2 py-0.5 text-[11px] text-accent" onClick={() => seekTo(i)}>▶ in video</button>
                    )}
                  </div>
                  {openEl === i && (
                    <div className="mt-2 space-y-2">
                      {el.memories.map((m) => (
                        <div key={m.id} className="rounded-lg bg-panel-2 p-2.5 text-xs">
                          <div className="font-medium">{m.title}</div>
                          <div className="text-muted">{m.content}</div>
                          {m.source_url && (
                            <a href={m.source_url} target="_blank" rel="noreferrer" className="mt-1 block truncate text-accent">{m.source_url}</a>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
