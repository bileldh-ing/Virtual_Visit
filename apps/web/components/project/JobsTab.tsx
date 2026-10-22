"use client";

import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Box, Orbit, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError, formatDuration, newIdempotencyKey } from "@/lib/api";
import { useJobs } from "@/lib/hooks";
import type { Job, Project } from "@/lib/schemas";
import { Badge, Button, Card, EmptyState, ErrorNote, Modal, stateTone } from "@/components/ui";
import { relTime } from "./ProjectCard";

const MODES = [
  { v: "AUTO", t: "Automatic", d: "Panoramas → 360° tour, perspective photos → photogrammetry (COLMAP)." },
  { v: "PHOTOGRAMMETRY", t: "Photogrammetry", d: "Camera poses + sparse points with COLMAP. Dense mesh needs a GPU worker." },
  { v: "PANORAMA_TOUR", t: "360° tour", d: "Linked equirectangular panoramas, shown exactly as captured." },
];

export function JobsTab({ project }: { project: Project }) {
  const jobs = useJobs(project.id, true);
  const qc = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("AUTO");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [needConfirm, setNeedConfirm] = useState<string | null>(null);
  const canEdit = project.role !== "VIEWER";
  const [key] = useState(newIdempotencyKey);

  const start = async (confirm = false) => {
    setBusy(true);
    setError(null);
    try {
      const job = await api<Job>(`/api/v1/projects/${project.id}/reconstruction-jobs`, { method: "POST", body: { mode, idempotency_key: key, confirm_warnings: confirm } });
      await qc.invalidateQueries({ queryKey: ["jobs", project.id] });
      await qc.invalidateQueries({ queryKey: ["project", project.id] });
      router.push(`/app/projects/${project.id}/jobs/${job.id}`);
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "CAPTURE_WARNINGS" || err.code === "CONFIRMATION_REQUIRED") setNeedConfirm(err.message);
      else setError(err);
      setBusy(false);
    }
  };

  const items = jobs.data?.items ?? [];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl text-ivory-50">Reconstructions</h2>
          <p className="text-sm text-ink-300">Each run is versioned. Minutes are only charged when a run succeeds.</p>
        </div>
        {canEdit && <Button onClick={() => setOpen(true)} disabled={!project.photo_count}><Play className="h-4 w-4" />New reconstruction</Button>}
      </div>
      <ErrorNote error={jobs.error} />
      {items.length === 0 && !jobs.isLoading && (
        <EmptyState icon={<Orbit className="h-10 w-10" />} title="No reconstructions yet">
          {project.photo_count ? "Run the capture check, then start a reconstruction." : "Upload photos first."}
        </EmptyState>
      )}
      <ul className="space-y-2">
        {items.map((j) => (
          <li key={j.id}>
            <Link href={`/app/projects/${project.id}/jobs/${j.id}`} className="surface flex flex-wrap items-center gap-4 rounded-2xl px-5 py-4 transition hover:border-white/15">
              <Box className="h-5 w-5 text-teal-300" aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={stateTone(j.state)}>{j.state.replace(/_/g, " ").toLowerCase()}</Badge>
                  <span className="text-sm text-ivory-100">{j.mode.replace(/_/g, " ").toLowerCase()}</span>
                  <span className="text-xs text-ink-400">#{j.id.slice(0, 8)}</span>
                </div>
                <p className="mt-1 text-xs text-ink-300">
                  {j.input_count} photos · started {relTime(j.created_at)}
                  {j.result?.metrics?.processing_seconds != null && ` · ${formatDuration(Number(j.result.metrics.processing_seconds))}`}
                  {j.result?.metrics?.images_registered != null && ` · ${j.result.metrics.images_registered} registered`}
                  {j.result?.metrics?.sparse_points != null && ` · ${Number(j.result.metrics.sparse_points).toLocaleString("en-US")} points`}
                </p>
              </div>
              <ArrowRight className="h-4 w-4 text-ink-400" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>

      <Modal open={open} onOpenChange={(v) => { setOpen(v); setNeedConfirm(null); setError(null); }} title="Start a reconstruction" description={`${project.photo_count} photos will be processed by a CPU worker.`}>
        <fieldset className="space-y-2">
          <legend className="sr-only">Mode</legend>
          {MODES.map((m) => (
            <label key={m.v} className={`flex cursor-pointer gap-3 rounded-2xl border p-3.5 transition ${mode === m.v ? "border-teal-400/60 bg-teal-400/[0.06]" : "border-white/10 hover:border-white/20"}`}>
              <input type="radio" name="mode" value={m.v} checked={mode === m.v} onChange={() => setMode(m.v)} className="mt-1 accent-teal-400" />
              <span>
                <span className="block text-sm font-medium text-ivory-50">{m.t}</span>
                <span className="block text-xs text-ink-300">{m.d}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <p className="mt-4 rounded-xl bg-white/[0.04] p-3 text-xs text-ink-300">
          Honest expectations: on CPU, ~50 photos take about 5 minutes and produce camera viewpoints and a sparse point cloud. A dense mesh is not produced without a CUDA GPU worker.
        </p>
        <ErrorNote error={error} className="mt-4" />
        {needConfirm && (
          <div role="alert" className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-400">
            <AlertTriangle className="mr-1.5 inline h-4 w-4" />{needConfirm}
            <Button size="sm" variant="secondary" className="mt-3 w-full" onClick={() => start(true)} loading={busy}>Start anyway</Button>
          </div>
        )}
        {!needConfirm && (
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={() => start(false)} loading={busy}>Start</Button>
          </div>
        )}
      </Modal>
    </div>
  );
}
