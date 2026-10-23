"use client";

import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  Image as ImageIcon,
  Info,
  Maximize,
  Minimize,
  MousePointer2,
  Pause,
  Play,
  Ruler,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { WorldSchema, type Artifact, type Fallback, type Tour, type World } from "@/lib/schemas";
import { headingOnMinimap } from "@/lib/world-nav";
import { BADGE, coveringPhoto, measurementTooltip, trustRows, type Honesty } from "@/lib/world-trust";
import type { Hotspot, MinimapInfo, WorldEngine } from "./world-engine";

export type WorldViewerProps = {
  title: string;
  artifacts: Artifact[];
  tour?: Tour | null;
  fallback?: Fallback | null;
  className?: string;
  compact?: boolean;
  autoPlay?: boolean;
};

declare global {
  interface Window {
    __roomscapeWorld?: () => ReturnType<WorldEngine["state"]> | null;
    /** test/capture hook: fixed-timestep control of the running engine (see WorldEngine.advance) */
    __roomscapeWorldEngine?: () => WorldEngine | null;
  }
}

function webgl2Available(): boolean {
  try {
    return Boolean(document.createElement("canvas").getContext("webgl2"));
  } catch {
    return false;
  }
}

/** Returns true when the artifacts describe an explorable world this viewer can show. */
export function isWorldArtifactSet(artifacts: Artifact[]): boolean {
  const kinds = new Set(artifacts.map((a) => a.kind));
  return kinds.has("WORLD") && (kinds.has("SPLAT_SPZ") || kinds.has("ROOM_MESH"));
}

export function WorldViewer({ title, artifacts, tour, fallback, className, compact, autoPlay }: WorldViewerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<WorldEngine | null>(null);
  const markerRef = useRef<HTMLDivElement>(null);
  const [world, setWorld] = useState<World | null>(null);
  const [progress, setProgress] = useState<{ label: string; f: number | null }>({ label: "Preparing the world", f: null });
  const [ready, setReady] = useState<{ hotspots: Hotspot[]; minimap: MinimapInfo | null; mode: "walk" | "waypoint" } | null>(null);
  const [quality, setQuality] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noGl, setNoGl] = useState(false);
  const [touring, setTouring] = useState(false);
  const [arrived, setArrived] = useState<number | null>(null);
  const [showPhoto, setShowPhoto] = useState(true);
  const [info, setInfo] = useState(false);
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [fade, setFade] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const [touch, setTouch] = useState(false);
  const [help, setHelp] = useState(true);
  // index of the captured photo covering the current view; null = no photo looked this way; undefined = unknown yet
  const [cover, setCover] = useState<number | null | undefined>(undefined);
  const coverRef = useRef<number | null | undefined>(undefined);

  const a = useMemo(() => {
    const by = (k: string) => artifacts.filter((x) => x.kind === k);
    const spz = by("SPLAT_SPZ");
    const variant = (x: Artifact) => String(x.meta?.variant ?? "");
    return {
      world: by("WORLD")[0],
      spzLow: spz.find((x) => variant(x) === "100k"),
      spzHigh: spz.find((x) => variant(x) === "500k") ?? spz.find((x) => variant(x) === "full_res") ?? spz[0],
      room: by("ROOM_MESH")[0],
      collider: by("COLLIDER_MESH")[0],
      pano: by("PANORAMA")[0],
      thumb: by("THUMBNAIL")[0] ?? by("PREVIEW")[0],
    };
  }, [artifacts]);

  // original photos (always available, never modified)
  const photos = useMemo(() => {
    const nodes = tour?.nodes ?? [];
    return nodes.filter((n) => n.image_url).map((n) => ({ uploadId: n.upload_id, ref: n.ref, image: n.image_url as string, thumb: n.thumb_url, label: n.label || n.ref.replace(/\.[a-z]+$/i, "") }));
  }, [tour]);

  useEffect(() => {
    setTouch(typeof window !== "undefined" && ("ontouchstart" in window || navigator.maxTouchPoints > 0));
  }, []);

  // load world.json, then boot the engine
  useEffect(() => {
    if (!a.world) return;
    let cancelled = false;
    let engine: WorldEngine | null = null;
    (async () => {
      try {
        const r = await fetch(a.world!.url, { credentials: "include" });
        if (!r.ok) throw new Error(`Could not download the world description (${r.status}).`);
        const w = WorldSchema.parse(await r.json());
        if (cancelled) return;
        setWorld(w);
        if (!webgl2Available()) {
          setNoGl(true);
          return;
        }
        const { WorldEngine } = await import("./world-engine");
        const dq = new URLSearchParams(window.location.search).get("detail");
        const detail = dq === "preview" || dq === "full" ? dq : "auto";
        if (cancelled || !hostRef.current) return;
        engine = new WorldEngine(hostRef.current, w, {
          spzLow: a.spzLow?.url, spzHigh: a.spzHigh?.url !== a.spzLow?.url ? a.spzHigh?.url : undefined, room: a.room?.url, collider: a.collider?.url, pano: a.pano?.url,
        }, {
          onProgress: (label, f) => setProgress({ label, f }),
          onReady: (inf) => setReady(inf),
          onQuality: setQuality,
          onError: setError,
          onArrive: setArrived,
          onTour: setTouring,
          onFade: setFade,
          onPose: (p) => {
            const hs = readyRef.current?.hotspots;
            if (hs?.length) {
              const c = coveringPhoto(p, hs.map((h) => h.pose), 35, w.viewpoints?.length ? 1.5 : Infinity); // azimuth-only photos: heading decides
              if (c !== coverRef.current) {
                coverRef.current = c;
                setCover(c);
              }
            }
            const el = markerRef.current;
            const mm = engineRef.current ? readyRef.current?.minimap : null;
            if (!el || !mm) return;
            const { u, v } = mm.toUV(p.x, p.z);
            el.style.left = `${Math.min(100, Math.max(0, u * 100))}%`;
            el.style.top = `${Math.min(100, Math.max(0, v * 100))}%`;
            el.style.transform = `translate(-50%, -50%) rotate(${headingOnMinimap(p.yaw)}rad)`;
          },
        }, detail);
        engineRef.current = engine;
        window.__roomscapeWorld = () => engineRef.current?.state() ?? null;
        window.__roomscapeWorldEngine = () => engineRef.current;
        await engine.load();
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load the world.");
      }
    })();
    return () => {
      cancelled = true;
      engine?.dispose();
      engineRef.current = null;
    };
  }, [a]);

  const readyRef = useRef(ready);
  useEffect(() => {
    readyRef.current = ready;
  }, [ready]);

  useEffect(() => {
    if (ready && autoPlay) engineRef.current?.startTour();
  }, [ready, autoPlay]);

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
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      const k = e.key.toLowerCase();
      if (k === "f") toggleFullscreen();
      else if (k === "t") (touring ? engineRef.current?.stopTour() : engineRef.current?.startTour());
      else if (k === "p") setShowPhoto((s) => !s);
      else if (k === "i") setInfo((s) => !s);
      else if (k === "escape") {
        setInfo(false);
        setLightbox(null);
      }
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(k)) setHelp(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleFullscreen, touring]);

  const honesty = (world?.honesty ?? "ai_generated") as Honesty;
  const badge = BADGE[honesty];
  const hotspots = ready?.hotspots ?? [];
  const photoFor = (h: Hotspot | undefined) => (h ? photos.find((p) => p.uploadId === h.uploadId) ?? photos.find((p) => p.ref === h.photoName) : undefined);
  const arrivedPhoto = arrived !== null ? photoFor(hotspots[arrived]) : undefined;
  const metric = Boolean(world?.semantics_metadata && world.semantics_metadata.metric_scale_factor);
  const rows = trustRows({
    honesty,
    photos: photos.length,
    placedPhotos: hotspots.length,
    hasCollider: Boolean(a.collider),
    metric,
    engineLabel: honesty === "ai_generated" ? `World Labs Marble (${world?.model ?? "model"})` : undefined,
    depthModel: world?.depth_model?.name,
  });

  if (!a.world) return null;

  return (
    <div ref={rootRef} className={clsx("relative overflow-hidden rounded-3xl bg-ink-950 ring-1 ring-white/8", fullscreen && "rounded-none", className)} data-testid="world-viewer">
      <div ref={hostRef} className="absolute inset-0" />

      {/* door-style transition fade */}
      <div className="pointer-events-none absolute inset-0 bg-ink-950" style={{ opacity: fade }} aria-hidden />

      {/* loading: progress + panorama/thumbnail backdrop */}
      {!error && !noGl && (progress.f === null || progress.f < 1) && progress.label && (
        <div className="pointer-events-none absolute inset-x-0 bottom-28 flex justify-center" role="status" aria-live="polite">
          <div className="glass w-72 rounded-2xl px-4 py-3">
            <p className="text-sm text-ivory-100">{progress.label}…</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10">
              {progress.f === null ? <div className="indeterminate h-full" /> : <div className="h-full bg-teal-400 transition-[width]" style={{ width: `${Math.round(progress.f * 100)}%` }} />}
            </div>
            {!ready && a.pano && <p className="mt-1.5 text-[11px] text-ink-300">The panorama is shown while the 3D world streams in.</p>}
          </div>
        </div>
      )}

      {(noGl || error) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 p-6 text-center">
          {(a.thumb || a.pano) && <img src={(a.pano ?? a.thumb)!.url} alt="Panorama of the generated world" className="max-h-[55%] max-w-full rounded-2xl ring-1 ring-white/10" />}
          <p role="alert" className="max-w-md text-sm text-ink-300">
            {error ?? "Your browser has WebGL 2 disabled, so the walkable 3D world is unavailable. Showing the panorama and your original photos instead."}
          </p>
        </div>
      )}

      {/* view coverage: is a captured photo behind what is on screen, or is this part of the world inferred? */}
      {ready && ready.mode === "walk" && hotspots.length > 0 && cover !== undefined && fade === 0 && !error && (
        <div className="pointer-events-none absolute left-1/2 top-24 z-[5] -translate-x-1/2 sm:top-4" data-testid="coverage-label" data-covered={cover !== null ? "yes" : "no"}>
          {cover !== null ? (
            <span className="glass inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium text-teal-300">
              <span className="h-1.5 w-1.5 rounded-full bg-teal-300" aria-hidden />
              Captured view · photo {hotspots[cover]?.label}
            </span>
          ) : honesty === "ai_generated" ? (
            <span className="glass inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium text-amber-300">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-300" aria-hidden />
              Inferred area · no photo looked this way
            </span>
          ) : (
            <span className="glass inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium text-sky-300">
              <span className="h-1.5 w-1.5 rounded-full bg-sky-300" aria-hidden />
              Between photos · estimated surfaces
            </span>
          )}
        </div>
      )}

      {/* captured photo overlay at its viewpoint */}
      <AnimatePresence>
        {showPhoto && arrivedPhoto && (cover === undefined || cover === arrived) && (
          <motion.div key={arrived} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.5 }} className="pointer-events-none absolute bottom-28 right-3 w-[min(34vw,320px)] sm:right-4" data-testid="photo-overlay">
            <div className="glass overflow-hidden rounded-2xl p-1.5">
              <img src={arrivedPhoto.image} alt={`Captured photo ${arrivedPhoto.label}`} className="max-h-[42vh] w-full rounded-xl object-contain" />
              <p className="px-1.5 pb-0.5 pt-1.5 text-[11px] text-ivory-100">Captured photo · original, not a render — taken from about here</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* top: title, honesty badge, fallback notice; minimap */}
      <div className="pointer-events-none absolute left-3 right-3 top-3 flex items-start justify-between gap-3 sm:left-4 sm:top-4">
        <div className="flex max-w-[min(560px,62%)] flex-col gap-2">
          <div className="glass pointer-events-auto rounded-2xl px-4 py-2.5">
            <p className="truncate font-display text-base text-ivory-50 sm:text-lg">{title}</p>
            <p className={clsx("mt-0.5 inline-flex items-center gap-1.5 text-xs font-medium", badge.tone === "amber" && "text-amber-300", badge.tone === "sky" && "text-sky-300", badge.tone === "teal" && "text-teal-300")} data-testid="honesty-badge">
              <span className={clsx("h-1.5 w-1.5 rounded-full", badge.tone === "amber" && "bg-amber-400", badge.tone === "sky" && "bg-sky-400", badge.tone === "teal" && "bg-teal-400")} />
              {badge.text}
              {quality && <span className="font-normal text-ink-300">· {quality}</span>}
            </p>
          </div>
          {fallback && (
            <div className="glass pointer-events-auto flex items-start gap-2 rounded-2xl px-3 py-2 text-xs text-amber-200" role="note" data-testid="fallback-notice">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{fallback.notice}</span>
            </div>
          )}
        </div>
        {!compact && ready?.minimap && (
          <div className="glass pointer-events-auto hidden w-44 overflow-hidden rounded-2xl p-1.5 sm:block md:w-56" aria-label="Minimap" data-testid="minimap">
            <div
              className="relative aspect-square cursor-crosshair overflow-hidden rounded-xl"
              onClick={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                const u = (e.clientX - r.left) / r.width;
                const v = (e.clientY - r.top) / r.height;
                const mm = ready.minimap!;
                // invert toUV numerically (linear map)
                const o = mm.toUV(0, 0);
                const ux = mm.toUV(1, 0).u - o.u;
                const vz = mm.toUV(0, 1).v - o.v;
                engineRef.current?.walkToMapPoint((u - o.u) / ux, (v - o.v) / vz);
              }}
            >
              <img src={ready.minimap.image} alt="Top-down map of the walkable space" className="absolute inset-0 h-full w-full" />
              {hotspots.map((h, i) => {
                const { u, v } = ready.minimap!.toUV(h.pose.p[0], h.pose.p[2]);
                return (
                  <button
                    key={h.id}
                    aria-label={`Go to photo ${h.label}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      engineRef.current?.stopTour();
                      engineRef.current?.goTo(i);
                    }}
                    className={clsx("absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-ink-950", i === arrived ? "scale-125 bg-ivory-50" : "bg-teal-400 hover:scale-150")}
                    style={{ left: `${u * 100}%`, top: `${v * 100}%` }}
                  />
                );
              })}
              <div ref={markerRef} className="pointer-events-none absolute left-1/2 top-1/2 h-0 w-0" aria-hidden data-testid="minimap-marker">
                <div className="absolute -left-2 -top-3 h-0 w-0 border-x-8 border-b-[14px] border-x-transparent border-b-amber-400" />
              </div>
            </div>
            <p className="px-1 pt-1 text-[10px] uppercase tracking-wider text-ink-300">Top-down · click to walk · {metric ? "approx. metres" : "relative scale"}</p>
          </div>
        )}
      </div>

      {/* what you're seeing */}
      <AnimatePresence>
        {info && (
          <motion.div initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} className="glass-strong absolute right-3 top-20 z-10 max-h-[calc(100%-11rem)] w-[min(360px,calc(100%-1.5rem))] overflow-y-auto rounded-3xl p-4 sm:right-4" role="dialog" aria-label="What you are seeing">
            <div className="mb-2 flex items-center justify-between">
              <p className="font-display text-lg text-ivory-50">What you’re seeing</p>
              <button aria-label="Close" onClick={() => setInfo(false)} className="rounded-lg p-1 text-ink-300 hover:bg-white/10"><X className="h-4 w-4" /></button>
            </div>
            <ul className="space-y-2.5">
              {rows.map((r) => (
                <li key={r.key} className="text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-ivory-100">{r.label}</span>
                    <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-medium", r.status === "captured" && "bg-teal-400/15 text-teal-300", r.status === "inferred" && "bg-amber-400/15 text-amber-300", r.status === "estimated" && "bg-sky-400/15 text-sky-300", r.status === "not claimed" && "bg-white/8 text-ink-300")}>{r.status}</span>
                  </div>
                  <p className="mt-0.5 text-xs text-ink-300">{r.detail}</p>
                </li>
              ))}
            </ul>
            {world?.caption && <p className="mt-3 border-t border-white/8 pt-3 text-xs text-ink-300"><span className="text-ivory-100">Model’s own description: </span>{world.caption.slice(0, 420)}{world.caption.length > 420 ? "…" : ""}</p>}
            {photos.length > 0 && (
              <div className="mt-3 border-t border-white/8 pt-3">
                <p className="mb-2 text-xs text-ivory-100">Original photos</p>
                <div className="grid grid-cols-4 gap-1.5">
                  {photos.map((p, i) => (
                    <button key={p.ref + i} onClick={() => setLightbox(i)} className="aspect-[3/4] overflow-hidden rounded-lg ring-1 ring-white/10 hover:ring-teal-300" aria-label={`Open original photo ${p.label}`}>
                      {p.thumb ? <img src={p.thumb} alt="" className="h-full w-full object-cover" /> : <span className="text-[10px]">{p.label}</span>}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* original photo lightbox */}
      <AnimatePresence>
        {lightbox !== null && photos[lightbox] && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-ink-950/90 p-6" role="dialog" aria-label="Original photo">
            <img src={photos[lightbox].image} alt={`Original photo ${photos[lightbox].label}`} className="max-h-[80%] max-w-full rounded-2xl" />
            <p className="mt-3 text-xs text-ink-300">Original photo · unmodified · {photos[lightbox].label}</p>
            <button onClick={() => setLightbox(null)} className="glass mt-3 rounded-xl px-3 py-1.5 text-sm text-ivory-100">Close</button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* first-use help */}
      {ready && help && !touch && !compact && (
        <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
          <div className="glass rounded-2xl px-4 py-3 text-center text-sm text-ivory-100">
            {ready.mode === "walk" ? (
              <>
                <p><b>W A S D</b> move · <b>Shift</b> sprint · drag to look</p>
                <p className="mt-1 text-xs text-ink-300">Click the floor to walk there · double-click for mouse-look · scroll to zoom · T auto-tour</p>
              </>
            ) : (
              <p>No collision mesh: use the photo markers or W to move between known viewpoints only.</p>
            )}
          </div>
        </div>
      )}

      {/* mobile joystick */}
      {touch && ready?.mode === "walk" && <Joystick onMove={(f, r) => engineRef.current?.setTouchMove(f, r)} />}

      {/* bottom: photo filmstrip + controls */}
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-2 p-3 sm:p-4">
        {!compact && photos.length > 0 && (
          <div className="flex max-w-full gap-1.5 overflow-x-auto rounded-2xl px-1 pb-1 scrollbar-thin" aria-label="Original photos">
            {photos.map((p, i) => {
              const hi = hotspots.findIndex((h) => h.uploadId === p.uploadId || h.photoName === p.ref);
              return (
                <button
                  key={p.ref + i}
                  onClick={() => (hi >= 0 ? (engineRef.current?.stopTour(), engineRef.current?.goTo(hi)) : setLightbox(i))}
                  aria-label={hi >= 0 ? `Walk to where photo ${p.label} was taken` : `Open photo ${p.label} (position not known)`}
                  className={clsx("relative h-14 w-11 shrink-0 overflow-hidden rounded-lg ring-2 transition sm:h-16 sm:w-12", hi >= 0 && hi === arrived ? "opacity-100 ring-teal-300" : "opacity-70 ring-transparent hover:opacity-100")}
                >
                  {p.thumb ? <img src={p.thumb} alt="" className="h-full w-full object-cover" loading="lazy" /> : <span className="text-[10px]">{i + 1}</span>}
                  {hi < 0 && <span className="absolute inset-x-0 bottom-0 bg-ink-950/80 text-[8px] text-ink-300">photo</span>}
                </button>
              );
            })}
          </div>
        )}
        <div className="glass flex items-center gap-0.5 rounded-2xl p-1.5 sm:gap-1" role="toolbar" aria-label="World controls">
          <Ctrl label={touring ? "Stop auto-tour (T)" : "Auto-tour (T)"} onClick={() => (touring ? engineRef.current?.stopTour() : engineRef.current?.startTour())} primary disabled={!ready}>
            {touring ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
          </Ctrl>
          <Ctrl label="Mouse-look (double-click the view; Esc to release)" onClick={() => engineRef.current?.lockPointer()} disabled={!ready || touch}><MousePointer2 className="h-5 w-5" /></Ctrl>
          <Ctrl label="Zoom in (+)" onClick={() => engineRef.current?.zoom(-8)} disabled={!ready}><ZoomIn className="h-5 w-5" /></Ctrl>
          <Ctrl label="Zoom out (−)" onClick={() => engineRef.current?.zoom(8)} disabled={!ready}><ZoomOut className="h-5 w-5" /></Ctrl>
          <span className="mx-1 h-6 w-px bg-white/10" aria-hidden />
          <Ctrl label="Show the captured photo at each viewpoint (P)" onClick={() => setShowPhoto((s) => !s)} active={showPhoto}><ImageIcon className="h-5 w-5" /></Ctrl>
          <Ctrl label={measurementTooltip(honesty)} disabled><Ruler className="h-5 w-5" /></Ctrl>
          <Ctrl label="What you’re seeing (I)" onClick={() => setInfo((v) => !v)} active={info}><Info className="h-5 w-5" /></Ctrl>
          <Ctrl label={fullscreen ? "Exit fullscreen (F)" : "Fullscreen (F)"} onClick={toggleFullscreen}>{fullscreen ? <Minimize className="h-5 w-5" /> : <Maximize className="h-5 w-5" />}</Ctrl>
        </div>
      </div>
    </div>
  );
}

function Joystick({ onMove }: { onMove: (forward: number, right: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [knob, setKnob] = useState<[number, number]>([0, 0]);
  const id = useRef<number | null>(null);
  const R = 48;
  const update = (cx: number, cy: number) => {
    const r = ref.current!.getBoundingClientRect();
    let dx = cx - (r.left + r.width / 2);
    let dy = cy - (r.top + r.height / 2);
    const d = Math.hypot(dx, dy);
    if (d > R) {
      dx *= R / d;
      dy *= R / d;
    }
    setKnob([dx, dy]);
    onMove(-dy / R, dx / R);
  };
  return (
    <div
      ref={ref}
      className="glass absolute bottom-32 left-5 z-10 h-28 w-28 touch-none rounded-full"
      aria-label="Movement joystick"
      data-testid="joystick"
      onTouchStart={(e) => {
        const t = e.changedTouches[0];
        id.current = t.identifier;
        update(t.clientX, t.clientY);
      }}
      onTouchMove={(e) => {
        for (const t of Array.from(e.changedTouches)) if (t.identifier === id.current) update(t.clientX, t.clientY);
      }}
      onTouchEnd={() => {
        id.current = null;
        setKnob([0, 0]);
        onMove(0, 0);
      }}
    >
      <div className="absolute left-1/2 top-1/2 h-11 w-11 rounded-full bg-white/25 ring-1 ring-white/40" style={{ transform: `translate(calc(-50% + ${knob[0]}px), calc(-50% + ${knob[1]}px))` }} />
    </div>
  );
}

function Ctrl({ label, children, onClick, disabled, active, primary }: { label: string; children: React.ReactNode; onClick?: () => void; disabled?: boolean; active?: boolean; primary?: boolean }) {
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
      <span role="tooltip" className="glass-strong pointer-events-none absolute bottom-full left-1/2 mb-2 hidden w-max max-w-[240px] -translate-x-1/2 rounded-lg px-2.5 py-1.5 text-center text-xs text-ivory-100 group-hover:block group-focus-within:block">
        {label}
      </span>
    </span>
  );
}
