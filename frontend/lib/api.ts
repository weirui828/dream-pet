"use client";

// Thin client for the Dream Pet API (/api/v1). The token lives in localStorage (set it on the
// Settings screen) or comes from NEXT_PUBLIC_ADMIN_TOKEN.

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
export const PET = "me";

export function getToken(): string {
  if (typeof window === "undefined") return process.env.NEXT_PUBLIC_ADMIN_TOKEN || "";
  try {
    return window.localStorage.getItem("dreampet_token") || process.env.NEXT_PUBLIC_ADMIN_TOKEN || "";
  } catch {
    return process.env.NEXT_PUBLIC_ADMIN_TOKEN || "";
  }
}

export function setToken(t: string) {
  try {
    window.localStorage.setItem("dreampet_token", t);
  } catch {
    /* private mode: token lasts for this page only */
  }
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const t = getToken();
  return { ...(t ? { Authorization: `Bearer ${t}` } : {}), ...(extra || {}) };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init || {};
  const res = await fetch(`${API_BASE}/api/v1${path}`, {
    ...rest,
    headers: headers(json !== undefined ? { "Content-Type": "application/json" } : undefined),
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const b = await res.json();
      msg = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail ?? b);
    } catch {}
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}

/** Media (videos, posters, avatars) is served by the API; <video>/<img> can't send headers. */
export function mediaUrl(path?: string | null): string | undefined {
  if (!path) return undefined;
  if (path.startsWith("http")) return path;
  const t = getToken();
  return `${API_BASE}${path}${t ? `${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(t)}` : ""}`;
}

export function streamUrl(path: string): string {
  const t = getToken();
  return `${API_BASE}/api/v1${path}${t ? `?token=${encodeURIComponent(t)}` : ""}`;
}

/** POST that answers with Server-Sent Events (chat). */
export async function postStream(
  path: string,
  body: unknown,
  onEvent: (event: string, data: any) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1${path}`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, res.statusText);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
      let event = "message";
      const data: string[] = [];
      for (const line of chunk.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (data.length) {
        try {
          onEvent(event, JSON.parse(data.join("\n")));
        } catch {
          onEvent(event, data.join("\n"));
        }
      }
    }
  }
}

export type Drives = { boredom: number; energy: number; curiosity: number; lifecycle: string };
export type Sample = { t: string; boredom: number; energy: number; curiosity: number; state: string };
export type Memory = {
  id: string;
  kind: string;
  title?: string | null;
  content: string;
  source_url?: string | null;
  importance: number;
  surprise: number;
  salience: number;
  strength: number;
  created_at: string;
  cluster_id?: string | null;
  cluster?: string | null;
  archived: boolean;
  meta: Record<string, any>;
};
export type DreamElement = {
  text: string;
  memory_ids: string[];
  image_hint?: string;
  memories: { id: string; title?: string; kind?: string; source_url?: string; content?: string }[];
};
export type Dream = {
  id: string;
  night: string;
  title: string;
  narrative: string;
  mood: string;
  weak: boolean;
  score: number;
  elements: DreamElement[];
  video: null | {
    job_id: string;
    status: string;
    url?: string;
    poster_url?: string;
    est_cost_usd: number;
    cost_usd: number;
    shot_map: { shot_id: number; kind: string; element_ref: number; memory_ids: string[]; start: number; end: number }[];
    error?: string | null;
  };
};
