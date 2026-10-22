"use client";

import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  Box,
  ChevronLeft,
  ChevronRight,
  Footprints,
  Image as ImageIcon,
  Info,
  LayoutGrid,
  Maximize,
  Minimize,
  Pause,
  Play,
  Ruler,
  X,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type * as THREE from "three";
import { parsePly } from "@/lib/ply";
import { classifyLayers, outputHeadline, type Layer } from "@/lib/quality";
import { CamerasSchema, type Artifact, type Cameras, type Tour } from "@/lib/schemas";
import { headingOnMap, minimapUV, toViewer, transformPositions, wrap, type Vec3 } from "@/lib/viewer-math";
import type { Pose, ViewMode } from "./Scene";

const Scene = dynamic(() => import("./Scene").then((m) => m.Scene), { ssr: false });

type JobResult = { output_type?: string; metrics?: Record<string, unknown>; limitations?: string[] } | null | undefined;

export type TourViewerProps = {
  title: string;
  artifacts: Artifact[];
  tour?: Tour | null;
  result?: JobResult;
  className?: string;
  compact?: boolean;
  autoPlay?: boolean;
};

type Loaded = { cameras: Cameras; positions: Float32Array; colors: Float32Array | null; floorY: number; poses: Pose[] };

function webglAvailable(): boolean {
  try {
    const c = document.createElement("canvas");
    return Boolean(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

export function TourViewer({ title, artifacts, tour, result, className, compact, autoPlay }: TourViewerProps) {
  const ply = artifacts.find((a) => a.kind === "POINT_CLOUD_WEB");
  const cams = artifacts.find((a) => a.kind === "VIEWPOINTS");
  const preview = artifacts.find((a) => a.kind === "PREVIEW");
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noGl, setNoGl] = useState(false);
  const [index, setIndex] = useState(0);
  const [mode, setMode] = useState<ViewMode>("orbit");
  const [playing, setPlaying] = useState(Boolean(autoPlay));
  const [grid, setGrid] = useState(false);
  const [info, setInfo] = useState(false);
  const [photo, setPhoto] = useState(false);
  const [arrived, setArrived] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const markerRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const layers: Layer[] = useMemo(() => classifyLayers(result, artifacts), [result, artifacts]);

  // Load the real artifacts: cameras.json + web PLY, both from signed URLs.
  useEffect(() => {
    if (!ply || !cams) return;
    if (!webglAvailable()) {
      setNoGl(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [cr, pr] = await Promise.all([fetch(cams.url, { credentials: "include" }), fetch(ply.url, { credentials: "include" })]);
        if (!cr.ok || !pr.ok) throw new Error(`Could not download the reconstruction (${cr.status}/${pr.status}).`);
        const cameras = CamerasSchema.parse(await cr.json());
        const pc = parsePly(await pr.arrayBuffer());
        const tr = cameras.viewer_transform as { origin: Vec3; rotation: number[][]; extent: number };
        transformPositions(pc.positions, tr);
        const ys: number[] = [];
        for (let i = 1; i < pc.positions.length; i += 3) ys.push(pc.positions[i]);
        ys.sort((a, b) => a - b);
        const floorY = ys.length ? ys[Math.floor(ys.length * 0.03)] : -2;
        const { viewpointPose } = await import("./Scene");
        const poses = cameras.viewpoints.map((vp) => viewpointPose(vp, tr));
        if (!cancelled) setData({ cameras, positions: pc.positions, colors: pc.colors, floorY, poses });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load the reconstruction.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ply, cams]);

  const n = data?.poses.length ?? 0;
  const thumbs = useMemo(() => {
    const byUpload = new Map((tour?.nodes ?? []).filter((x) => x.upload_id).map((x) => [x.upload_id as string, x]));
    const byRef = new Map((tour?.nodes ?? []).map((x) => [x.ref, x]));
    return (data?.cameras.viewpoints ?? []).map((vp) => {
      const node = (vp.upload_id && byUpload.get(vp.upload_id)) || byRef.get(vp.name);
      return { thumb: node?.thumb_url ?? null, image: node?.image_url ?? null, label: node?.label || vp.name.replace(/\.[a-z]+$/i, "") };
    });
  }, [data, tour]);

  const go = useCallback(
    (i: number) => {
      if (!n) return;
      setArrived(false);
      setIndex(wrap(i, n));
    },
    [n],
  );

  // Auto-tour: advance after arriving and dwelling at each viewpoint.
  useEffect(() => {
    if (!playing || !arrived || !n) return;
    const t = setTimeout(() => go(index + 1), mode === "walk" ? 2600 : 1800);
    return () => clearTimeout(t);
  }, [playing, arrived, index, n, go, mode]);

  useEffect(() => {
    const el = stripRef.current?.querySelector<HTMLElement>(`[data-i="${index}"]`);
    el?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }, [index]);

  const toggleFullscreen = useCallback(() => {
    const el = rootRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  }, []);
  useEffect(() => {
    const f = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", f);
    return () => document.removeEventListener("fullscreenchange", f);
  }, []);

  // Keyboard shortcuts (ignored while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "ArrowRight") go(index + 1);
      else if (e.key === "ArrowLeft") go(index - 1);
      else if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key.toLowerCase() === "f") toggleFullscreen();
      else if (e.key.toLowerCase() === "g") setGrid((g) => !g);
      else if (e.key.toLowerCase() === "v") setMode((m) => (m === "orbit" ? "walk" : "orbit"));
      else if (e.key === "Escape") {
        setGrid(false);
        setInfo(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, index, toggleFullscreen]);

  const onCamera = useCallback(
    (pos: THREE.Vector3, fwd: THREE.Vector3) => {
      const el = markerRef.current;
      if (!el || !data) return;
      const tr = data.cameras.viewer_transform as { origin: Vec3; rotation: number[][]; extent: number };
      const { u, v } = minimapUV([pos.x, pos.y, pos.z], tr);
      const h = headingOnMap([fwd.x, fwd.y, fwd.z]);
      el.style.left = `${Math.min(100, Math.max(0, u * 100))}%`;
      el.style.top = `${Math.min(100, Math.max(0, v * 100))}%`;
      el.style.transform = `translate(-50%, -50%) rotate(${h}rad)`;
    },
    [data],
  );

  if (!ply || !cams) {
    return (
      <div className={clsx("surface flex min-h-[320px] items-center justify-center rounded-3xl p-8 text-center", className)}>
        <div>
          <Box className="mx-auto mb-3 h-8 w-8 text-ink-400" aria-hidden />
          <p className="text-ivory-100">No 3D reconstruction for this project yet.</p>
          <p className="mt-1 text-sm text-ink-300">Run a reconstruction to generate a point cloud and camera viewpoints.</p>
        </div>
      </div>
    );
  }

  const current = data?.cameras.viewpoints[index];
  const tr = data?.cameras.viewer_transform as { origin: Vec3; rotation: number[][]; extent: number } | undefined;
  const showPhoto = photo && mode === "walk" && arrived && thumbs[index]?.image;

  return (
    <div ref={rootRef} className={clsx("relative overflow-hidden rounded-3xl bg-ink-950 ring-1 ring-white/8", fullscreen && "rounded-none", className)} data-testid="tour-viewer">
      <div className="absolute inset-0">
        {data && !noGl && (
          <Scene
            positions={data.positions}
            colors={data.colors}
            poses={data.poses}
            index={index}
            mode={mode}
            pointSize={mode === "walk" ? 0.035 : 0.06}
            showFrustums
            floorY={data.floorY}
            onSelect={go}
            onCamera={onCamera}
            onArrive={() => setArrived(true)}
          />
        )}
        {(noGl || error) && (
          <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
            {preview && <img src={preview.url} alt="Top-down render of the reconstructed points" className="max-h-[60%] rounded-2xl ring-1 ring-white/10" />}
            <p role="alert" className="max-w-md text-sm text-ink-300">
              {error ?? "Your browser has WebGL disabled, so the interactive 3D view is unavailable. Showing the top-down render of the real points instead."}
            </p>
          </div>
        )}
        {!data && !error && !noGl && (
          <div className="flex h-full items-center justify-center">
            <div className="w-48">
              <div className="indeterminate h-1 rounded-full bg-white/8" />
              <p className="mt-3 text-center text-sm text-ink-300" role="status">Loading point cloud…</p>
            </div>
          </div>
        )}
      </div>

      {/* Source photo overlay (first-person mode): the real capture from this exact viewpoint. */}
      <AnimatePresence>
        {showPhoto && (
          <motion.div
            key={index}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.6 }}
            className="pointer-events-none absolute inset-0 flex items-center justify-center bg-ink-950/40"
          >
            <img src={thumbs[index].image as string} alt={`Source photo ${thumbs[index].label}`} className="h-full w-auto object-contain" />
            <span className="glass absolute left-1/2 top-20 -translate-x-1/2 rounded-full px-3 py-1 text-xs text-ivory-100">Source photo · not a render</span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Top-left: title + honest output label */}
      <div className="pointer-events-none absolute left-3 top-3 right-3 flex items-start justify-between gap-3 sm:left-4 sm:top-4">
        <div className="glass pointer-events-auto max-w-[min(520px,60%)] rounded-2xl px-4 py-2.5">
          <p className="truncate font-display text-base text-ivory-50 sm:text-lg">{title}</p>
          <p className="truncate text-xs text-ink-300">{outputHeadline(result)}</p>
        </div>

        {/* Top-right: minimap from the real top-down preview with live position */}
        {!compact && preview && tr && (
          <div className="glass pointer-events-auto hidden w-44 overflow-hidden rounded-2xl p-1.5 sm:block md:w-52" aria-label="Minimap">
            <div className="relative aspect-square overflow-hidden rounded-xl">
              <img src={preview.url} alt="Top-down map of the reconstruction" className="absolute inset-0 h-full w-full object-cover opacity-90" />
              {data?.cameras.viewpoints.map((vp, i) => {
                const { u, v } = minimapUV(toViewer(vp.position, tr), tr);
                return (
                  <button
                    key={i}
                    aria-label={`Go to viewpoint ${i + 1}`}
                    onClick={() => go(i)}
                    className={clsx("absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-ink-950 transition", i === index ? "bg-ivory-50 scale-125" : "bg-teal-400 hover:scale-150")}
                    style={{ left: `${u * 100}%`, top: `${v * 100}%` }}
                  />
                );
              })}
              <div ref={markerRef} className="pointer-events-none absolute left-1/2 top-1/2 h-0 w-0" aria-hidden>
                <div className="absolute -left-2 -top-3 h-0 w-0 border-x-8 border-b-[14px] border-x-transparent border-b-amber-400/90" />
              </div>
            </div>
            <p className="px-1 pt-1 text-[10px] uppercase tracking-wider text-ink-300">Top-down · relative scale</p>
          </div>
        )}
      </div>

      {/* Grid of all viewpoints */}
      <AnimatePresence>
        {grid && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            className="glass-strong absolute inset-x-3 bottom-24 top-20 z-10 overflow-y-auto rounded-3xl p-4 scrollbar-thin sm:inset-x-8"
            role="dialog"
            aria-label="All viewpoints"
          >
            <div className="mb-3 flex items-center justify-between">
              <p className="font-display text-lg text-ivory-50">{n} viewpoints</p>
              <button aria-label="Close grid" onClick={() => setGrid(false)} className="rounded-lg p-1.5 text-ink-300 hover:bg-white/10"><X className="h-5 w-5" /></button>
            </div>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-7">
              {thumbs.map((t, i) => (
                <button key={i} onClick={() => { go(i); setGrid(false); }} className={clsx("group relative aspect-[3/4] overflow-hidden rounded-xl ring-2 transition", i === index ? "ring-teal-300" : "ring-transparent hover:ring-white/30")}>
                  {t.thumb ? <img src={t.thumb} alt="" className="h-full w-full object-cover transition group-hover:scale-105" /> : <div className="h-full w-full bg-ink-800" />}
                  <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-ink-950/90 to-transparent px-1.5 pb-1 pt-4 text-left text-[10px] text-ivory-100">{i + 1}. {t.label}</span>
                </button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Quality layers */}
      <AnimatePresence>
        {info && (
          <motion.div initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} className="glass-strong absolute right-3 top-20 z-10 w-[min(340px,calc(100%-1.5rem))] rounded-3xl p-4 sm:right-4" role="dialog" aria-label="What you are seeing">
            <div className="mb-2 flex items-center justify-between">
              <p className="font-display text-lg text-ivory-50">What you’re seeing</p>
              <button aria-label="Close" onClick={() => setInfo(false)} className="rounded-lg p-1 text-ink-300 hover:bg-white/10"><X className="h-4 w-4" /></button>
            </div>
            <ul className="space-y-2.5">
              {layers.map((l) => (
                <li key={l.key} className="text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-ivory-100">{l.label}</span>
                    <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-medium", l.status === "reconstructed" && "bg-teal-400/15 text-teal-300", l.status === "inferred" && "bg-amber-400/15 text-amber-400", l.status === "unavailable" && "bg-white/8 text-ink-300")}>{l.status}</span>
                  </div>
                  <p className="mt-0.5 text-xs text-ink-300">{l.detail}</p>
                </li>
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Bottom: filmstrip + glass control bar */}
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-2 p-3 sm:p-4">
        {!compact && n > 0 && (
          <div ref={stripRef} className="flex max-w-full gap-1.5 overflow-x-auto rounded-2xl px-1 pb-1 scrollbar-thin" aria-label="Viewpoint filmstrip">
            {thumbs.map((t, i) => (
              <button
                key={i}
                data-i={i}
                onClick={() => go(i)}
                aria-label={`Viewpoint ${i + 1}: ${t.label}`}
                aria-current={i === index ? "true" : undefined}
                className={clsx("relative h-14 w-11 shrink-0 overflow-hidden rounded-lg ring-2 transition sm:h-16 sm:w-12", i === index ? "ring-teal-300 opacity-100" : "ring-transparent opacity-60 hover:opacity-100")}
              >
                {t.thumb ? <img src={t.thumb} alt="" className="h-full w-full object-cover" loading="lazy" /> : <span className="flex h-full w-full items-center justify-center bg-ink-800 text-[10px] text-ink-300">{i + 1}</span>}
              </button>
            ))}
          </div>
        )}
        <div className="glass flex items-center gap-0.5 rounded-2xl p-1.5 sm:gap-1" role="toolbar" aria-label="Viewer controls">
          <CtrlButton label="All viewpoints (G)" onClick={() => setGrid((g) => !g)} active={grid}><LayoutGrid className="h-5 w-5" /></CtrlButton>
          <CtrlButton label="Previous viewpoint (←)" onClick={() => go(index - 1)} disabled={!n}><ChevronLeft className="h-5 w-5" /></CtrlButton>
          <CtrlButton label={playing ? "Pause auto-tour (Space)" : "Play auto-tour (Space)"} onClick={() => setPlaying((p) => !p)} disabled={!n} primary>
            {playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
          </CtrlButton>
          <CtrlButton label="Next viewpoint (→)" onClick={() => go(index + 1)} disabled={!n}><ChevronRight className="h-5 w-5" /></CtrlButton>
          <span className="mx-1 h-6 w-px bg-white/10" aria-hidden />
          <CtrlButton label={mode === "orbit" ? "Switch to first-person walk (V)" : "Switch to 3D overview (V)"} onClick={() => setMode((m) => (m === "orbit" ? "walk" : "orbit"))} active={mode === "walk"}>
            {mode === "orbit" ? <Footprints className="h-5 w-5" /> : <Box className="h-5 w-5" />}
          </CtrlButton>
          <CtrlButton label="Show source photo at viewpoint (walk mode)" onClick={() => { setPhoto((p) => !p); if (mode !== "walk") setMode("walk"); }} active={photo}><ImageIcon className="h-5 w-5" /></CtrlButton>
          <CtrlButton label="Measurement disabled: scale is relative (single-camera reconstruction has no metric scale)" disabled><Ruler className="h-5 w-5" /></CtrlButton>
          <CtrlButton label="What you’re seeing" onClick={() => setInfo((v) => !v)} active={info}><Info className="h-5 w-5" /></CtrlButton>
          <CtrlButton label={fullscreen ? "Exit fullscreen (F)" : "Fullscreen (F)"} onClick={toggleFullscreen}>{fullscreen ? <Minimize className="h-5 w-5" /> : <Maximize className="h-5 w-5" />}</CtrlButton>
        </div>
        <p className="sr-only" aria-live="polite">
          {current ? `Viewpoint ${index + 1} of ${n}, ${thumbs[index]?.label ?? current.name}, ${mode === "walk" ? "first-person" : "overview"} mode` : ""}
        </p>
        {n > 0 && (
          <p className="text-[11px] text-ink-300">
            {index + 1} / {n} · {mode === "walk" ? "Drag to look · WASD to move" : "Drag to orbit · scroll to zoom"}
          </p>
        )}
      </div>
    </div>
  );
}

function CtrlButton({ label, children, onClick, disabled, active, primary }: { label: string; children: React.ReactNode; onClick?: () => void; disabled?: boolean; active?: boolean; primary?: boolean }) {
  return (
    <span className="group relative">
      <button
        type="button"
        aria-label={label}
        aria-pressed={active ?? undefined}
        aria-disabled={disabled || undefined}
        onClick={disabled ? undefined : onClick}
        className={clsx(
          "flex h-10 w-10 items-center justify-center rounded-xl transition sm:h-11 sm:w-11",
          disabled ? "cursor-not-allowed text-ink-400" : "text-ivory-100 hover:bg-white/10 active:scale-95",
          active && "bg-white/12 text-teal-300",
          primary && !disabled && "bg-teal-400 text-ink-950 hover:bg-teal-300",
        )}
      >
        {children}
      </button>
      <span role="tooltip" className="glass-strong pointer-events-none absolute bottom-full left-1/2 mb-2 hidden w-max max-w-[220px] -translate-x-1/2 rounded-lg px-2.5 py-1.5 text-center text-xs text-ivory-100 group-hover:block group-focus-within:block">
        {label}
      </span>
    </span>
  );
}
