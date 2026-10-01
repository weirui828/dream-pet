// Per-pet theme: the persona's theme_color becomes the UI accent.

export const DEFAULT_THEME = "#2f6fd6";
const KEY = "dreampet_theme";

function rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function luminance([r, g, b]: [number, number, number]): number {
  const f = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** White or near-black, whichever contrasts better with the given color. */
function inkFor(c: [number, number, number]): string {
  const L = luminance(c);
  return (1.05) / (L + 0.05) >= (L + 0.05) / 0.05 ? "#ffffff" : "#12111a";
}

export function isHex(c: unknown): c is string {
  return typeof c === "string" && /^#[0-9a-fA-F]{6}$/.test(c);
}

export function applyTheme(color?: string | null) {
  if (typeof document === "undefined" || !isHex(color)) return;
  const base = rgb(color);
  const darkModeAccent = base.map((v) => Math.round(v * 0.72 + 255 * 0.28)) as [number, number, number];
  const root = document.documentElement.style;
  root.setProperty("--accent-base", color);
  root.setProperty("--accent-ink-light", inkFor(base));
  root.setProperty("--accent-ink-dark", inkFor(darkModeAccent));
  try {
    window.localStorage.setItem(KEY, color);
  } catch {}
}

/** Inline script for <head>: apply the last-used theme before first paint (no flash). */
export const THEME_BOOT_SCRIPT = `(function(){try{var c=localStorage.getItem("${KEY}");if(c&&/^#[0-9a-fA-F]{6}$/.test(c)){document.documentElement.style.setProperty("--accent-base",c);}}catch(e){}})();`;

// ---- small colour helpers (hex in, hex out) ----

function toHex([r, g, b]: number[]): string {
  return "#" + [r, g, b].map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");
}

/** Mix two hex colours; t = 0 gives a, t = 1 gives b. */
export function mixHex(a: string, b: string, t: number): string {
  const A = rgb(a);
  const B = rgb(b);
  return toHex(A.map((v, i) => v + (B[i] - v) * t));
}

/** Readable text colour (white or near-black) on top of `hex`. */
export function inkOn(hex: string): string {
  return inkFor(rgb(hex));
}
