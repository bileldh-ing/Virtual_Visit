"use client";

import clsx from "clsx";
import { CheckCircle2, CircleSlash, Download, FileCheck2, Maximize2, Sparkles } from "lucide-react";
import Link from "next/link";
import { formatBytes, formatDuration } from "@/lib/api";
import { classifyLayers, num, outputHeadline } from "@/lib/quality";
import type { Artifact, Job, Project, Tour } from "@/lib/schemas";
import { Badge, Button, Card } from "@/components/ui";
import { TourViewer } from "@/components/viewer/TourViewer";
import { isWorldArtifactSet } from "@/components/viewer/WorldViewer";
import { EngineSummary } from "./EngineSummary";
import { PanoramaViewer } from "@/components/viewer/PanoramaViewer";

const KIND_LABEL: Record<string, string> = {
  POINT_CLOUD_WEB: "Web point cloud (PLY)",
  POINT_CLOUD_RAW: "Raw sparse points (PLY)",
  POINT_CLOUD_DENSE: "Dense point cloud (PLY)",
  VIEWPOINTS: "Camera viewpoints (JSON)",
  PREVIEW: "Top-down preview (PNG)",
  QUALITY_REPORT: "Quality report (JSON)",
  MANIFEST: "Artifact manifest (JSON)",
  LOG: "Processing log",
  PANORAMA: "Panorama",
  PANORAMA_THUMB: "Panorama thumbnail",
  TOUR: "Tour graph (JSON)",
  MESH: "Mesh (GLB)",
  WORLD: "World description (JSON)",
  SPLAT_SPZ: "Gaussian splats (SPZ)",
  COLLIDER_MESH: "Collision mesh (GLB)",
  ROOM_MESH: "Depth-estimated room (GLB)",
  THUMBNAIL: "Thumbnail",
  PROVIDER_LOG: "Provider call log (JSON)",
};

/** Lightweight poster for explorable worlds: the 3D viewer only loads on the viewer page. */
function WorldPoster({ projectId, artifacts }: { projectId: string; artifacts: Artifact[] }) {
  const img = artifacts.find((a) => a.kind === "PANORAMA") ?? artifacts.find((a) => a.kind === "THUMBNAIL") ?? artifacts.find((a) => a.kind === "PREVIEW");
  return (
    <Link href={`/app/projects/${projectId}/viewer`} className="group relative block h-[62vh] min-h-[420px] overflow-hidden rounded-3xl bg-ink-950 ring-1 ring-white/8" aria-label="Open the walkable world">
      {img && <img src={img.url} alt="Preview of the generated space" className={clsx("h-full w-full transition duration-700 group-hover:scale-[1.02]", img.kind === "PREVIEW" ? "object-contain p-10" : "object-cover")} />}
      <span className="absolute inset-0 bg-gradient-to-t from-ink-950/80 via-transparent to-transparent" />
      <span className="absolute bottom-6 left-1/2 inline-flex -translate-x-1/2 items-center gap-2 rounded-2xl bg-teal-400 px-5 py-3 text-sm font-medium text-ink-950 shadow-lg">Walk through it</span>
    </Link>
  );
}

export function Metric({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-2xl bg-white/[0.04] p-4">
      <dt className="text-xs uppercase tracking-wider text-ink-400">{label}</dt>
      <dd className="mt-1 font-display text-2xl text-ivory-50 tabular-nums">{value}</dd>
      {hint && <p className="mt-0.5 text-xs text-ink-300">{hint}</p>}
    </div>
  );
}

export function OverviewTab({ project, job, artifacts, tour }: { project: Project; job: Job | null; artifacts: Artifact[]; tour: Tour | null }) {
  const m = job?.result?.metrics ?? {};
  const layers = classifyLayers(job?.result, artifacts);
  const isPano = job?.mode === "PANORAMA_TOUR" || job?.result?.output_type === "panorama_tour";
  const panoNodes = (tour?.nodes ?? []).filter((n) => n.kind === "PANORAMA" && n.image_url).map((n) => ({ id: n.id, label: n.label || n.ref, image: n.image_url as string, thumb: n.thumb_url }));

  if (!job) {
    return (
      <Card className="flex flex-col items-center gap-3 py-14 text-center">
        <Sparkles className="h-9 w-9 text-teal-300" aria-hidden />
        <h2 className="font-display text-2xl text-ivory-50">No reconstruction yet</h2>
        <p className="max-w-md text-sm text-ink-300">{project.photo_count ? `${project.photo_count} photos uploaded. Run the capture check, then start a reconstruction.` : "Upload overlapping photos of the property to begin."}</p>
        <Link href={`/app/projects/${project.id}?tab=${project.photo_count ? "jobs" : "photos"}`}><Button>{project.photo_count ? "Start a reconstruction" : "Upload photos"}</Button></Link>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="relative">
        {isWorldArtifactSet(artifacts) ? (
          <WorldPoster projectId={project.id} artifacts={artifacts} />
        ) : isPano && panoNodes.length ? (
          <PanoramaViewer nodes={panoNodes} title={project.title} className="h-[62vh] min-h-[420px]" />
        ) : (
          <TourViewer title={project.title} artifacts={artifacts} tour={tour} result={job.result} compact className="h-[62vh] min-h-[420px]" />
        )}
        <Link href={`/app/projects/${project.id}/viewer`} className="glass absolute right-4 top-4 inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-ivory-50 hover:bg-white/10">
          <Maximize2 className="h-4 w-4" />Open full viewer
        </Link>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <Card className="p-6">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-display text-2xl text-ivory-50">{outputHeadline(job.result)}</h2>
            <Badge tone={job.state === "SUCCEEDED" ? "teal" : "amber"}>{job.state.replace(/_/g, " ").toLowerCase()}</Badge>
          </div>
          {job.engine && <EngineSummary job={job} className="mt-3" />}
          <dl className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
            {num(m.images_registered) != null && <Metric label="Registered" value={`${m.images_registered}/${m.images_uploaded}`} hint="photos placed in 3D" />}
            {num(m.sparse_points) != null && <Metric label="3D points" value={Number(m.sparse_points).toLocaleString("en-US")} hint="sparse, triangulated" />}
            {num(m.mean_reprojection_error_px) != null && <Metric label="Reproj. error" value={`${Number(m.mean_reprojection_error_px).toFixed(3)} px`} hint="mean, lower is better" />}
            {num(m.processing_seconds) != null && <Metric label="Processing" value={formatDuration(Number(m.processing_seconds))} hint={`${String(m.compute_mode ?? "")} worker`} />}
            {num(m.mean_track_length) != null && <Metric label="Track length" value={Number(m.mean_track_length).toFixed(2)} hint="views per point" />}
            {num(m.disconnected_models) != null && <Metric label="Models" value={String(m.disconnected_models)} hint={Number(m.disconnected_models) > 1 ? "largest shown; others disconnected" : "single connected model"} />}
          </dl>
          {(job.result?.limitations?.length ?? 0) > 0 && (
            <div className="mt-6">
              <h3 className="text-sm font-medium text-ivory-100">Limitations of this result</h3>
              <ul className="mt-2 space-y-1.5 text-sm text-ink-300">
                {job.result?.limitations?.map((l) => <li key={l} className="flex gap-2"><CircleSlash className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" aria-hidden />{l}</li>)}
              </ul>
            </div>
          )}
          {(job.result?.recapture_suggestions?.length ?? 0) > 0 && (
            <div className="mt-5">
              <h3 className="text-sm font-medium text-ivory-100">How to get a better result</h3>
              <ul className="mt-2 space-y-1.5 text-sm text-ink-300">
                {job.result?.recapture_suggestions?.map((l) => <li key={l} className="flex gap-2"><Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-teal-300" aria-hidden />{l}</li>)}
              </ul>
            </div>
          )}
        </Card>

        <div className="space-y-6">
          <Card>
            <h3 className="font-display text-lg text-ivory-50">Quality labels</h3>
            <ul className="mt-3 space-y-2.5">
              {layers.map((l) => (
                <li key={l.key}>
                  <div className="flex items-center justify-between gap-2 text-sm">
                    <span className="text-ivory-100">{l.label}</span>
                    <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-medium", l.status === "reconstructed" && "bg-teal-400/15 text-teal-300", l.status === "inferred" && "bg-amber-400/15 text-amber-400", l.status === "unavailable" && "bg-white/8 text-ink-300")}>{l.status}</span>
                  </div>
                  <p className="text-xs text-ink-300">{l.detail}</p>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <h3 className="font-display text-lg text-ivory-50">Artifacts</h3>
            <ul className="mt-3 divide-y divide-white/5">
              {artifacts.map((a) => (
                <li key={a.id} className="flex items-center gap-3 py-2.5">
                  {a.validation?.ok ? <FileCheck2 className="h-4 w-4 shrink-0 text-teal-300" aria-label="Validated" /> : <CheckCircle2 className="h-4 w-4 shrink-0 text-ink-400" aria-hidden />}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-ivory-100">{KIND_LABEL[a.kind] ?? a.kind}</p>
                    <p className="truncate text-xs text-ink-400">{a.name} · {formatBytes(a.bytes)} · sha256 {a.sha256.slice(0, 10)}…</p>
                  </div>
                  <a href={a.url} download={a.name} className="rounded-lg p-1.5 text-ink-300 hover:bg-white/10 hover:text-ivory-50" aria-label={`Download ${a.name}`}><Download className="h-4 w-4" /></a>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
