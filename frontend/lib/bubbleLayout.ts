// Flat circle packing for the mind map: every bubble on one plane, biggest topic in the middle.

export type Cluster = { id: string; label: string; size: number; lp: number; visits: number; seed: boolean; error_history: number[] };
export type Bubble = Cluster & { x: number; y: number; r: number; i: number };

export function packBubbles(cs: Cluster[], gap = 10): { bubbles: Bubble[]; box: { x: number; y: number; w: number; h: number } } {
  const max = Math.max(1, ...cs.map((c) => c.size));
  const sorted = [...cs].sort((a, b) => b.size - a.size || a.id.localeCompare(b.id));
  const golden = Math.PI * (3 - Math.sqrt(5));
  const bs: Bubble[] = sorted.map((c, i) => {
    const r = c.size > 0 ? 26 + 84 * Math.sqrt(c.size / max) : 18;
    const d = i === 0 ? 0 : (70 + gap) * Math.sqrt(i);
    return { ...c, r, i, x: d * Math.cos(i * golden), y: d * Math.sin(i * golden) };
  });
  for (let it = 0; it < 400; it++) {
    for (let a = 0; a < bs.length; a++) {
      const A = bs[a];
      if (a > 0) {
        A.x *= 0.99; // gentle pull to the centre keeps the cluster round and tight
        A.y *= 0.99;
      }
      for (let b = a + 1; b < bs.length; b++) {
        const B = bs[b];
        const dx = B.x - A.x || 0.01;
        const dy = B.y - A.y || 0.01;
        const dist = Math.hypot(dx, dy);
        const min = A.r + B.r + gap;
        if (dist < min) {
          const push = (min - dist) / dist;
          const shareA = a === 0 ? 0 : 0.5; // the biggest topic stays pinned
          A.x -= dx * push * shareA;
          A.y -= dy * push * shareA;
          B.x += dx * push * (1 - shareA);
          B.y += dy * push * (1 - shareA);
        }
      }
    }
  }
  const pad = 24;
  const minX = Math.min(...bs.map((b) => b.x - b.r)) - pad;
  const maxX = Math.max(...bs.map((b) => b.x + b.r)) + pad;
  const minY = Math.min(...bs.map((b) => b.y - b.r)) - pad;
  const maxY = Math.max(...bs.map((b) => b.y + b.r)) + pad;
  return { bubbles: bs, box: { x: minX, y: minY, w: maxX - minX, h: maxY - minY } };
}
