"use client";

import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Card({ title, children, className = "", actions }: { title?: ReactNode; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={`card p-4 md:p-5 ${className}`}>
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="label">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

const STATE_STYLE: Record<string, string> = {
  idle: "bg-panel-2 text-ink",
  exploring: "bg-boredom/15 text-boredom",
  napping: "bg-energy/15 text-energy",
  asleep: "bg-accent-soft text-accent",
  dreaming: "bg-curiosity/15 text-curiosity",
};

export function StateBadge({ state }: { state?: string }) {
  if (!state) return null;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ${STATE_STYLE[state] || "bg-panel-2"}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {state}
    </span>
  );
}

export function Gauge({ label, value, max = 1, color, marks, suffix }: {
  label: string;
  value: number;
  max?: number;
  color: string;
  marks?: { at: number; label: string }[];
  suffix?: string;
}) {
  const pct = Math.max(0, Math.min(1, value / max)) * 100;
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="label">{label}</span>
        <span className="font-mono text-sm tabular-nums">
          {max === 1 ? value.toFixed(2) : Math.round(value)}
          {suffix}
        </span>
      </div>
      <div className="relative h-2.5 rounded-full bg-panel-2">
        <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: color }} />
        {marks?.map((m) => (
          <div key={m.label} className="absolute -top-1 h-4.5 w-px bg-muted" style={{ left: `${(m.at / max) * 100}%` }} title={m.label} />
        ))}
      </div>
      {marks && (
        <div className="relative mt-1 h-3 text-[10px] text-muted">
          {marks.map((m) => (
            <span key={m.label} className="absolute -translate-x-1/2" style={{ left: `${(m.at / max) * 100}%` }}>
              {m.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-muted">{children}</div>;
}

export function ErrorNote({ error }: { error?: string }) {
  if (!error) return null;
  const auth = /401|token/i.test(error);
  return (
    <div className="mb-4 rounded-xl border border-bad/40 bg-bad/10 p-3 text-sm text-bad">
      {auth ? "The API needs your admin token — add it on the Settings screen." : `Can't reach the pet: ${error}. Is \`dreampet up\` running?`}
    </div>
  );
}

export function timeAgo(iso?: string, now?: Date): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const n = (now || new Date()).getTime();
  const s = Math.round((n - t) / 1000);
  if (Math.abs(s) < 60) return "just now";
  const m = Math.round(s / 60);
  if (Math.abs(m) < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (Math.abs(h) < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function fmtTime(iso: string, tz?: string, withDate = false) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    ...(withDate ? { month: "short", day: "numeric" } : {}),
  });
}

export function Pill({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "good" | "bad" | "warn" | "accent" }) {
  const cls = {
    default: "bg-panel-2 text-muted",
    good: "bg-good/15 text-good",
    bad: "bg-bad/15 text-bad",
    warn: "bg-warn/15 text-warn",
    accent: "bg-accent-soft text-accent",
  }[tone];
  return <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>{children}</span>;
}

export function Avatar({ url, name, state, size = 96 }: { url?: string; name?: string; state?: string; size?: number }) {
  const hue = [...(name || "D")].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  const asleep = state === "asleep" || state === "dreaming" || state === "napping";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt={name} className={`h-full w-full rounded-full object-cover ${asleep ? "" : "breathe"}`} />
      ) : (
        <svg viewBox="0 0 100 100" className={`h-full w-full ${asleep ? "" : "breathe"}`} aria-label={name}>
          <defs>
            <radialGradient id={`g${hue}`} cx="35%" cy="30%">
              <stop offset="0%" stopColor={`hsl(${hue} 80% 80%)`} />
              <stop offset="100%" stopColor={`hsl(${(hue + 40) % 360} 55% 55%)`} />
            </radialGradient>
          </defs>
          <path d="M50 8c22 0 40 16 40 40 0 26-18 44-40 44S10 74 10 48C10 24 28 8 50 8z" fill={`url(#g${hue})`} />
          {asleep ? (
            <>
              <path d="M32 50q6 5 12 0M56 50q6 5 12 0" stroke="#1f1d2b" strokeWidth={3} fill="none" strokeLinecap="round" />
              <text x="72" y="28" fontSize="14" fill="currentColor" className="text-muted">z</text>
              <text x="80" y="18" fontSize="10" fill="currentColor" className="text-muted">z</text>
            </>
          ) : (
            <>
              <circle cx="38" cy="48" r="5" fill="#1f1d2b" />
              <circle cx="62" cy="48" r="5" fill="#1f1d2b" />
              <circle cx="40" cy="46" r="1.6" fill="#fff" />
              <circle cx="64" cy="46" r="1.6" fill="#fff" />
            </>
          )}
          <path d={state === "exploring" ? "M42 64q8 7 16 0" : "M44 64q6 4 12 0"} stroke="#1f1d2b" strokeWidth={2.5} fill="none" strokeLinecap="round" />
        </svg>
      )}
    </div>
  );
}
