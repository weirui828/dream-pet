"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { useApi } from "@/lib/hooks";
import { applyTheme } from "@/lib/theme";

const LINKS = [
  { href: "/", label: "Home", icon: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" },
  { href: "/chat", label: "Chat", icon: "M4 5h16v11H8l-4 4z" },
  { href: "/journal", label: "Journal", icon: "M6 3h11a2 2 0 0 1 2 2v16l-4-2-4 2-4-2-3 2V5a2 2 0 0 1 2-2z" },
  { href: "/dreams", label: "Dreams", icon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" },
  { href: "/mindmap", label: "Mind map", icon: "M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M5 5l4.5 4.5M19 5l-4.5 4.5M5 19l4.5-4.5M19 19l-4.5-4.5" },
  { href: "/personality", label: "Personality", icon: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4" },
  { href: "/settings", label: "Settings", icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-1-3-2 .3-1.4-1.4.3-2-3-1-1 2h-2l-1-2-3 1 .3 2L5.3 7.3 3.3 7l-1 3 2 1v2l-2 1 1 3 2-.3 1.4 1.4-.3 2 3 1 1-2h2l1 2 3-1-.3-2 1.4-1.4 2 .3 1-3-2-1z" },
  { href: "/simulation", label: "Simulation", icon: "M4 20V10M10 20V4M16 20v-7M22 20H2" },
];

export default function Nav() {
  const path = usePathname();
  const { data: st } = useApi<any>("/pets/me/status", 15000);
  const theme = st?.pet?.theme_color;
  useEffect(() => applyTheme(theme), [theme]);
  return (
    <nav className="sticky top-0 z-20 flex shrink-0 flex-col gap-1 border-b border-line bg-bg/90 px-3 py-3 backdrop-blur md:h-screen md:w-56 md:border-r md:border-b-0 md:py-6">
      <Link href="/" className="mb-2 flex items-center gap-2 px-2">
        <span className="grid h-8 w-8 place-items-center rounded-full bg-accent text-sm font-bold text-accent-ink">
          {(st?.pet?.name || "D").slice(0, 1)}
        </span>
        <span className="font-semibold">{st?.pet?.name || "Dream Pet"}</span>
      </Link>
      <div className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible">
        {LINKS.map((l) => {
          const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
          return (
            <Link
              key={l.href}
              href={l.href}
              className={`flex shrink-0 items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors ${
                active ? "bg-accent-soft font-semibold text-accent" : "text-muted hover:bg-panel-2 hover:text-ink"
              }`}
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
                <path d={l.icon} />
              </svg>
              {l.label}
              {l.href === "/chat" && st?.unread > 0 && (
                <span className="ml-auto rounded-full bg-curiosity px-1.5 text-[11px] font-bold text-white">{st.unread}</span>
              )}
            </Link>
          );
        })}
      </div>
      {st && (
        <div className="mt-auto hidden px-2 text-xs text-muted md:block">
          <div className="capitalize">{st.state}</div>
          <div>
            ${st.spent_today.toFixed(2)} / ${st.daily_cap.toFixed(2)} today
          </div>
        </div>
      )}
    </nav>
  );
}
