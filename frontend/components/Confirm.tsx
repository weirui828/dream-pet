"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

type Options = {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean; // destructive actions get a red confirm button
};

type Pending = Options & { resolve: (ok: boolean) => void };

const ConfirmContext = createContext<(o: Options) => Promise<boolean>>(async () => false);

/** `const confirm = useConfirm(); if (!(await confirm({ title: "…" }))) return;` */
export function useConfirm() {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const confirm = useCallback((o: Options) => new Promise<boolean>((resolve) => setPending({ ...o, resolve })), []);
  const close = (ok: boolean) => {
    pending?.resolve(ok);
    setPending(null);
  };
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && <Dialog opts={pending} onClose={close} />}
    </ConfirmContext.Provider>
  );
}

function Dialog({ opts, onClose }: { opts: Options; onClose: (ok: boolean) => void }) {
  const okRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    okRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose(false);
    };
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden"; // no background scroll while open
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      prev?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="confirm-backdrop fixed inset-0 z-50 grid place-items-center bg-black/40 p-4 backdrop-blur-[2px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose(false);
      }}
    >
      <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" className="confirm-panel card w-full max-w-sm p-5 shadow-2xl">
        <div className="flex gap-3">
          <span className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full ${opts.danger ? "bg-bad/15 text-bad" : "bg-accent-soft text-accent"}`}>
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              {opts.danger ? (
                <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6" />
              ) : (
                <path d="M12 8v5M12 16.5v.5M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" />
              )}
            </svg>
          </span>
          <div className="min-w-0">
            <h2 id="confirm-title" className="font-semibold">{opts.title}</h2>
            {opts.body && <div className="mt-1 text-sm text-muted">{opts.body}</div>}
          </div>
        </div>
        <form
          className="mt-5 flex justify-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            onClose(true);
          }}
        >
          <button type="button" className="btn" onClick={() => onClose(false)}>
            {opts.cancelLabel || "Cancel"}
          </button>
          <button
            ref={okRef}
            type="submit"
            className="btn btn-primary"
            style={opts.danger ? { background: "var(--bad)", borderColor: "var(--bad)", color: "#fff" } : undefined}
          >
            {opts.confirmLabel || "Confirm"}
          </button>
        </form>
      </div>
    </div>
  );
}
