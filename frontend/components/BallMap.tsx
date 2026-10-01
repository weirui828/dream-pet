"use client";

// Real 3D balls, all lying on one plane (z = 0), seen from the front. Drag to tilt the plane a
// little; it springs back. Layout comes from the same flat circle packing as before.

import { Environment, Lightformer } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import * as THREE from "three";
import { packBubbles, type Bubble, type Cluster } from "@/lib/bubbleLayout";
import { usePrefersDark, useThemeAccent } from "@/lib/hooks";
import { mixHex } from "@/lib/theme";

export type { Cluster };

const PX = 1 / 60; // layout pixels -> world units
const LABEL_MIN_R = 38; // in layout px: smaller balls show their name on hover
const MAX_TILT = 0.38; // radians

type Ball = Bubble & { wx: number; wy: number; wr: number };

// Dreamy pastels. Meaning stays the same: rose = learning (prediction error falling),
// periwinkle = getting harder, pearl = mastered or noise.
const ROSE = ["#ffc9e0", "#ff9fcb"];
const PERIWINKLE = ["#cdd4ff", "#9aa6ff"];
const PEARL = { light: "#f7f4ff", dark: "#d9d4f2" };
const TINTS = ["#ffd2b0", "#b9e6ff", "#e2ccff", "#c4f3e2"]; // peach, sky, lilac, mint

function dreamyColor(b: Bubble, dark: boolean): string {
  const t = Math.min(1, Math.abs(b.lp) / 0.1);
  let c: string;
  if (b.lp > 0.005) c = mixHex(ROSE[0], ROSE[1], t);
  else if (b.lp < -0.005) c = mixHex(PERIWINKLE[0], PERIWINKLE[1], t);
  else c = dark ? PEARL.dark : PEARL.light;
  return mixHex(c, TINTS[b.i % TINTS.length], 0.18); // a little variety so the field isn't uniform
}

function useReducedMotion() {
  const [still, setStill] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setStill(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return still;
}

/** Keep the whole plane in view whatever the canvas size. */
function FitCamera({ w, h, margin }: { w: number; h: number; margin: number }) {
  const { camera, size } = useThree();
  useEffect(() => {
    const cam = camera as THREE.PerspectiveCamera;
    const half = THREE.MathUtils.degToRad(cam.fov / 2);
    const aspect = size.width / Math.max(1, size.height);
    const z = Math.max(h / 2 / Math.tan(half), w / 2 / (Math.tan(half) * aspect)) * 1.08 + margin;
    cam.position.set(0, 0, z);
    cam.lookAt(0, 0, 0);
    cam.updateProjectionMatrix();
  }, [camera, size, w, h, margin]);
  return null;
}

/** The plane: tilts toward the drag, springs back, sways a touch when idle. */
function Plane({ tilt, still, children }: { tilt: RefObject<{ x: number; y: number }>; still: boolean; children: React.ReactNode }) {
  const g = useRef<THREE.Group>(null);
  useFrame((state, dt) => {
    if (!g.current) return;
    const sway = still ? 0 : Math.sin(state.clock.elapsedTime * 0.35) * 0.05;
    g.current.rotation.x = THREE.MathUtils.damp(g.current.rotation.x, tilt.current.x, 6, dt);
    g.current.rotation.y = THREE.MathUtils.damp(g.current.rotation.y, tilt.current.y + sway, 6, dt);
  });
  return <group ref={g}>{children}</group>;
}

function BallMesh({ b, color, accent, selected, hovered, still, registry, onSelect, onHover, dragged }: {
  b: Ball;
  color: string;
  accent: string;
  selected: boolean;
  hovered: boolean;
  still: boolean;
  registry: RefObject<Map<string, THREE.Object3D>>;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
  dragged: RefObject<boolean>;
}) {
  const mesh = useRef<THREE.Mesh | null>(null);
  const mat = useRef<THREE.MeshPhysicalMaterial>(null);
  const born = useRef<number | null>(null);
  const phase = useMemo(() => (b.i * 2.399) % (Math.PI * 2), [b.i]);
  useFrame((state, dt) => {
    const m = mesh.current;
    if (!m) return;
    const t = state.clock.elapsedTime;
    if (born.current === null) born.current = t + b.i * 0.045; // staggered entrance
    const grown = still ? 1 : THREE.MathUtils.smoothstep(t - born.current, 0, 0.6);
    const target = b.wr * grown * (hovered ? 1.07 : 1) * (selected ? 1.04 : 1);
    m.scale.setScalar(THREE.MathUtils.damp(m.scale.x, target, 10, dt));
    // gentle bob, and hovered balls rise toward the viewer
    m.position.y = b.wy + (still ? 0 : Math.sin(t * 0.9 + phase) * 0.03);
    m.position.z = THREE.MathUtils.damp(m.position.z, hovered || selected ? b.wr * 0.35 : 0, 8, dt);
    if (mat.current) {
      // every ball glows softly from within; hover and selection glow brighter
      const glow = selected ? 0.45 + (still ? 0 : 0.12 * Math.sin(t * 2.4)) : hovered ? 0.32 : 0.2;
      mat.current.emissiveIntensity = THREE.MathUtils.damp(mat.current.emissiveIntensity, glow, 8, dt);
    }
  });
  return (
    <mesh
      ref={(m) => {
        mesh.current = m;
        if (m) registry.current.set(b.id, m);
        else registry.current.delete(b.id);
      }}
      position={[b.wx, b.wy, 0]}
      scale={still ? b.wr : 0.0001}
      onClick={(e) => {
        e.stopPropagation();
        if (!dragged.current) onSelect(b.id);
      }}
      onPointerOver={(e) => {
        e.stopPropagation();
        onHover(b.id);
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        onHover(null);
        document.body.style.cursor = "";
      }}
    >
      <sphereGeometry args={[1, 64, 64]} />
      <meshPhysicalMaterial
        ref={mat}
        color={color}
        emissive={selected ? mixHex(color, accent, 0.5) : color}
        emissiveIntensity={0.2}
        roughness={0.14}
        metalness={0}
        clearcoat={1}
        clearcoatRoughness={0.06}
        iridescence={0.55}
        iridescenceIOR={1.35}
        iridescenceThicknessRange={[120, 520]}
        sheen={0.5}
        sheenRoughness={0.4}
        sheenColor="#fff0fb"
        envMapIntensity={1.2}
        transparent
        opacity={b.size === 0 ? 0.35 : 1}
      />
    </mesh>
  );
}

/** Move each HTML label over its ball (imperative, every frame, no React renders). */
function projectLabels(camera: THREE.Camera, width: number, height: number, meshes: Map<string, THREE.Object3D>,
                       labels: Map<string, HTMLDivElement>, v: THREE.Vector3) {
  for (const [id, el] of labels) {
    const obj = meshes.get(id);
    if (!obj) continue;
    obj.getWorldPosition(v);
    v.project(camera);
    el.style.transform = `translate(${(((v.x + 1) / 2) * width).toFixed(1)}px, ${(((1 - v.y) / 2) * height).toFixed(1)}px) translate(-50%, -50%)`;
    el.style.visibility = "visible";
  }
}

function LabelProjector({ registry, labels }: { registry: RefObject<Map<string, THREE.Object3D>>; labels: RefObject<Map<string, HTMLDivElement>> }) {
  const v = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ camera, size }) => projectLabels(camera, size.width, size.height, registry.current, labels.current, v));
  return null;
}

export default function BallMap({ clusters, selected, onSelect }: { clusters: Cluster[]; selected: string | null; onSelect: (id: string) => void }) {
  const still = useReducedMotion();
  const dark = usePrefersDark();
  const accent = useThemeAccent();
  const [hover, setHover] = useState<string | null>(null);
  const registry = useRef(new Map<string, THREE.Object3D>());
  const labels = useRef(new Map<string, HTMLDivElement>());
  const tilt = useRef({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const dragged = useRef(false);

  const { balls, w, h, maxR } = useMemo(() => {
    const { bubbles, box } = packBubbles(clusters, 32);
    const cx = box.x + box.w / 2;
    const cy = box.y + box.h / 2;
    const balls: Ball[] = bubbles.map((b) => ({ ...b, wx: (b.x - cx) * PX, wy: -(b.y - cy) * PX, wr: b.r * PX }));
    return { balls, w: box.w * PX, h: box.h * PX, maxR: Math.max(...balls.map((b) => b.wr), 0.5) };
  }, [clusters]);

  return (
    <div
      className="dreamy-backdrop relative h-full w-full touch-none"
      onPointerDown={(e) => {
        drag.current = { x: e.clientX, y: e.clientY };
        dragged.current = false;
      }}
      onPointerMove={(e) => {
        if (!drag.current) return;
        const dx = e.clientX - drag.current.x;
        const dy = e.clientY - drag.current.y;
        if (Math.hypot(dx, dy) > 5) dragged.current = true;
        tilt.current = {
          x: THREE.MathUtils.clamp(dy / 250, -MAX_TILT, MAX_TILT),
          y: THREE.MathUtils.clamp(dx / 250, -MAX_TILT, MAX_TILT),
        };
      }}
      onPointerUp={() => {
        drag.current = null;
        tilt.current = { x: 0, y: 0 }; // spring back to face-on
        setTimeout(() => (dragged.current = false), 0);
      }}
      onPointerLeave={() => {
        drag.current = null;
        tilt.current = { x: 0, y: 0 };
      }}
    >
      <Canvas
        camera={{ fov: 35, position: [0, 0, 20] }}
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true, toneMapping: THREE.NeutralToneMapping, toneMappingExposure: 1.05 }}
      >
        <FitCamera w={w} h={h} margin={maxR} />
        <ambientLight intensity={dark ? 0.7 : 1.1} color="#fff6fd" />
        <directionalLight position={[4, 7, 8]} intensity={1.0} color="#fff3f8" />
        <pointLight position={[-6, -4, 3]} intensity={0.6} color="#bcd8ff" />
        <pointLight position={[6, 5, 2]} intensity={0.4} color="#ffc8e4" />
        {/* local studio lighting for the glossy reflections (no HDR download) */}
        <Environment resolution={256}>
          <Lightformer form="rect" intensity={2.2} color="#fff4fb" position={[0, 5, 6]} scale={[10, 4, 1]} />
          <Lightformer form="rect" intensity={1.4} color="#c9e4ff" position={[-6, 1, 2]} rotation-y={Math.PI / 2} scale={[8, 3, 1]} />
          <Lightformer form="ring" intensity={1.4} color="#ffcde6" position={[6, -2, 3]} rotation-y={-Math.PI / 2} scale={4} />
        </Environment>
        <Plane tilt={tilt} still={still}>
          {balls.map((b) => (
            <BallMesh
              key={b.id}
              b={b}
              color={dreamyColor(b, dark)}
              accent={accent}
              selected={b.id === selected}
              hovered={b.id === hover}
              still={still}
              registry={registry}
              onSelect={onSelect}
              onHover={setHover}
              dragged={dragged}
            />
          ))}
        </Plane>
        <LabelProjector registry={registry} labels={labels} />
      </Canvas>
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        {balls.map((b) => {
          const inside = b.r >= LABEL_MIN_R;
          const show = inside || b.id === hover || b.id === selected;
          return (
            <div
              key={b.id}
              ref={(el) => {
                if (el) labels.current.set(b.id, el);
                else labels.current.delete(b.id);
              }}
              className={`absolute top-0 left-0 text-center leading-tight transition-opacity duration-200 ${
                inside
                  ? "font-semibold text-[#1f1d2b] [text-shadow:0_1px_2px_rgba(255,255,255,0.6)]"
                  : `whitespace-nowrap rounded-full px-2 py-0.5 text-[12px] shadow-sm ${b.id === selected ? "bg-accent text-accent-ink" : "bg-panel/90 text-ink"}`
              }`}
              style={{
                opacity: show ? 1 : 0,
                visibility: "hidden",
                ...(inside ? { width: b.r * 1.5, fontSize: Math.max(10, Math.min(15, b.r / 4.4)), hyphens: "auto", overflowWrap: "anywhere" } : { marginTop: b.r + 14 }),
              }}
              lang="en"
            >
              <span className={inside ? "line-clamp-2" : ""}>{b.label}</span>
              <span className={inside ? "block text-[0.8em] opacity-70" : "ml-1 opacity-60"}>{b.size}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
