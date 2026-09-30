"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, streamUrl } from "./api";

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
