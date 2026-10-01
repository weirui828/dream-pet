"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, streamUrl } from "./api";

const INVALIDATE = "dreampet:invalidate";

/** Tell every useApi() reading a path that starts with `prefix` to refetch now
 *  (e.g. after editing the persona, so the name in the sidebar updates immediately). */
export function invalidate(prefix: string) {
  window.dispatchEvent(new CustomEvent(INVALIDATE, { detail: prefix }));
}

/** Fetch JSON, refetch on demand or on an interval. Data from a previous path is never shown. */
export function useApi<T = any>(path: string | null, intervalMs?: number) {
  const [state, setState] = useState<{ path: string | null; data?: T; error?: string }>({ path: null });
  const [loading, setLoading] = useState(false);
  const pathRef = useRef(path);
  useLayoutEffect(() => {
    pathRef.current = path;
  });

  const reload = useCallback(async () => {
    const p = pathRef.current;
    if (!p) return;
    setLoading(true);
    try {
      const d = await api<T>(p);
      if (pathRef.current === p) setState({ path: p, data: d });
    } catch (e: any) {
      if (pathRef.current === p) setState((s) => ({ ...s, path: p, error: e?.message || String(e) }));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const onInvalidate = (e: Event) => {
      const prefix = (e as CustomEvent<string>).detail;
      if (pathRef.current?.startsWith(prefix)) reload();
    };
    window.addEventListener(INVALIDATE, onInvalidate);
    return () => window.removeEventListener(INVALIDATE, onInvalidate);
  }, [reload]);

  useEffect(() => {
    if (!path) return;
    const first = setTimeout(reload, 0);
    const id = intervalMs ? setInterval(reload, intervalMs) : undefined;
    return () => {
      clearTimeout(first);
      if (id) clearInterval(id);
    };
  }, [path, intervalMs, reload]);

  const current = state.path === path;
  const setData = useCallback((f: T | undefined | ((d: T | undefined) => T | undefined)) => {
    setState((s) => ({ ...s, data: typeof f === "function" ? (f as (d: T | undefined) => T | undefined)(s.data) : f }));
  }, []);
  return { data: current ? state.data : undefined, error: current ? state.error : undefined, loading, reload, setData };
}

/** True once running in the browser (for values only the client knows, like localStorage). */
export function useMounted(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

export type LiveEvent = { kind: string; data: any };

/** Subscribe to the pet's live SSE stream. */
export function useLive(onEvent: (e: LiveEvent) => void) {
  const cb = useRef(onEvent);
  useLayoutEffect(() => {
    cb.current = onEvent;
  });
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const open = () => {
      es = new EventSource(streamUrl("/pets/me/stream"));
      es.onopen = () => setConnected(true);
      es.onerror = () => {
        setConnected(false);
        es?.close();
        retry = setTimeout(open, 3000);
      };
      for (const kind of ["hello", "drives", "state", "event", "message"]) {
        es.addEventListener(kind, (m: MessageEvent) => {
          try {
            cb.current({ kind, data: JSON.parse(m.data) });
          } catch {}
        });
      }
    };
    open();
    return () => {
      es?.close();
      if (retry) clearTimeout(retry);
    };
  }, []);
  return connected;
}

export function useDebounced<T>(fn: (v: T) => void, ms = 400) {
  const t = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const f = useRef(fn);
  useLayoutEffect(() => {
    f.current = fn;
  });
  return useCallback(
    (v: T) => {
      if (t.current) clearTimeout(t.current);
      t.current = setTimeout(() => f.current(v), ms);
    },
    [ms],
  );
}

/** The pet's theme colour, kept live: applyTheme() writes it to <html style>, so watch that. */
export function useThemeAccent(): string {
  return useSyncExternalStore(
    (cb) => {
      const mo = new MutationObserver(cb);
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
      return () => mo.disconnect();
    },
    () => {
      const v = getComputedStyle(document.documentElement).getPropertyValue("--accent-base").trim();
      return /^#[0-9a-f]{6}$/i.test(v) ? v : "#2f6fd6";
    },
    () => "#2f6fd6",
  );
}

/** Follows the OS dark-mode setting. */
export function usePrefersDark(): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia("(prefers-color-scheme: dark)");
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia("(prefers-color-scheme: dark)").matches,
    () => false,
  );
}
