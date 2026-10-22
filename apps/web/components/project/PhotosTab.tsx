"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, CheckCircle2, ImagePlus, RefreshCw, ScanSearch, Trash2, UploadCloud, XCircle } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { api, ApiError, formatBytes, uploadWithProgress } from "@/lib/api";
import { planBatches } from "@/lib/batches";
import { useUploads } from "@/lib/hooks";
import type { Project, UploadItem } from "@/lib/schemas";
import { Badge, Button, Card, ErrorNote, Spinner } from "@/components/ui";

type UploadResult = { created: UploadItem[]; duplicates: { name: string }[]; rejected: { name: string; code: string; message: string }[] };
type CaptureImage = { name: string; blur_score?: number; mean_luminance?: number; orb_keypoints?: number; best_match_count?: number; is_panorama?: boolean; issues: { code: string; severity: string; message: string; fix: string }[] };
type CaptureReport = {
  id?: string;
  status: string;
  stale: boolean;
  report?: {
    status?: string;
    recommended_mode?: string;
    summary?: Record<string, number | string>;
    disclaimer?: string;
    images?: CaptureImage[];
    project_issues?: { code: string; severity: string; message: string; fix?: string }[];
    message?: string;
  };
};

const ACCEPT = "image/jpeg,image/png,image/webp";

export function PhotosTab({ project }: { project: Project }) {
  const uploads = useUploads(project.id);
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; bytes: number; totalBytes: number } | null>(null);
  const [summary, setSummary] = useState<UploadResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const canEdit = project.role !== "VIEWER";

  const report = useQuery<CaptureReport, ApiError>({
    queryKey: ["capture", project.id],
    queryFn: () => api(`/api/v1/projects/${project.id}/capture-quality`),
    refetchInterval: (q) => (q.state.data?.status === "PENDING" ? 2000 : false),
  });

  const upload = useCallback(
    async (files: File[]) => {
      if (!files.length) return;
      setError(null);
      setSummary(null);
      const totalBytes = files.reduce((s, f) => s + f.size, 0);
      const agg: UploadResult = { created: [], duplicates: [], rejected: [] };
      let sent = 0;
      setProgress({ done: 0, total: files.length, bytes: 0, totalBytes });
      try {
        let done = 0;
        for (const batch of planBatches(files)) {
          const before = done;
          const form = new FormData();
          batch.forEach((f) => form.append("files", f, f.name));
          const r = await uploadWithProgress<UploadResult>(`/api/v1/projects/${project.id}/uploads`, form, (loaded) =>
            setProgress({ done: before, total: files.length, bytes: sent + loaded, totalBytes }),
          );
          sent += batch.reduce((s, f) => s + f.size, 0);
          agg.created.push(...r.created);
          agg.duplicates.push(...r.duplicates);
          agg.rejected.push(...r.rejected);
          done += batch.length;
          setProgress({ done, total: files.length, bytes: sent, totalBytes });
        }
      } catch (e) {
        setError(e as ApiError);
      } finally {
        setSummary(agg);
        setProgress(null);
        await Promise.all([
          qc.invalidateQueries({ queryKey: ["uploads", project.id] }),
          qc.invalidateQueries({ queryKey: ["project", project.id] }),
          qc.invalidateQueries({ queryKey: ["capture", project.id] }),
        ]);
      }
    },
    [project.id, qc],
  );

  const analyze = async () => {
    setError(null);
    try {
      await api(`/api/v1/projects/${project.id}/capture-quality/analyze`, { method: "POST" });
      await report.refetch();
    } catch (e) {
      setError(e as ApiError);
    }
  };

  const remove = async (u: UploadItem) => {
    if (!confirm(`Delete ${u.name}? The original and its derivatives are removed from storage.`)) return;
    try {
      await api(`/api/v1/projects/${project.id}/uploads/${u.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["uploads", project.id] });
      await qc.invalidateQueries({ queryKey: ["capture", project.id] });
    } catch (e) {
      setError(e as ApiError);
    }
  };

  const items = uploads.data?.items ?? [];
  const rep = report.data;
  const issuesByName = new Map((rep?.report?.images ?? []).map((im) => [im.name, im]));

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
      <div className="space-y-6">
        {canEdit && (
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              void upload(Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/")));
            }}
            className={clsx("relative rounded-3xl border-2 border-dashed p-8 text-center transition", drag ? "border-teal-300 bg-teal-400/5" : "border-white/12 hover:border-white/25")}
          >
            <UploadCloud className="mx-auto h-10 w-10 text-teal-300" aria-hidden />
            <p className="mt-3 font-display text-xl text-ivory-50">Drop photos here</p>
            <p className="mx-auto mt-1 max-w-md text-sm text-ink-300">
              20–200 overlapping shots per property. Walk slowly, keep ~60% overlap, cover doorways between rooms. JPEG, PNG or WebP, up to 40 MB each. 2:1 equirectangular images become a 360° tour.
            </p>
            <input ref={input} type="file" accept={ACCEPT} multiple className="sr-only" id="file-input" onChange={(e) => { void upload(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
            <Button className="mt-5" onClick={() => input.current?.click()} disabled={Boolean(progress)}><ImagePlus className="h-4 w-4" />Choose photos</Button>
            {progress && (
              <div className="mx-auto mt-6 max-w-md text-left" role="status" aria-live="polite">
                <div className="mb-1.5 flex justify-between text-xs text-ink-300">
                  <span>Uploading {progress.done}/{progress.total} files</span>
                  <span className="tabular-nums">{formatBytes(progress.bytes)} / {formatBytes(progress.totalBytes)}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white/8">
                  <div className="h-full rounded-full bg-teal-400 transition-[width]" style={{ width: `${(progress.bytes / Math.max(1, progress.totalBytes)) * 100}%` }} />
                </div>
              </div>
            )}
          </div>
        )}
        <ErrorNote error={error} />
        {summary && (
          <div className="flex flex-wrap gap-2 text-sm" role="status">
            <Badge tone="teal" icon={<CheckCircle2 className="h-3.5 w-3.5" />}>{summary.created.length} added</Badge>
            {summary.duplicates.length > 0 && <Badge tone="amber">{summary.duplicates.length} duplicate{summary.duplicates.length > 1 ? "s" : ""} skipped</Badge>}
            {summary.rejected.map((r) => <Badge key={r.name} tone="rose" icon={<XCircle className="h-3.5 w-3.5" />}>{r.name}: {r.message}</Badge>)}
          </div>
        )}
        <div>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-display text-xl text-ivory-50">{items.length} photo{items.length === 1 ? "" : "s"}</h2>
            {uploads.isFetching && <Spinner label="Refreshing" />}
          </div>
          {items.length === 0 && !uploads.isLoading && <p className="text-sm text-ink-300">No photos yet.</p>}
          <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4 xl:grid-cols-6">
            {items.map((u) => {
              const q = issuesByName.get(u.name);
              const issues = q?.issues ?? u.quality?.issues ?? [];
              const worst = issues.some((x) => x.severity === "error") ? "error" : issues.length ? "warning" : "ok";
              return (
                <li key={u.id} className="group relative aspect-[3/4] overflow-hidden rounded-xl bg-ink-850 ring-1 ring-white/8">
                  <img src={u.thumb_url} alt={u.name} className="h-full w-full object-cover" loading="lazy" />
                  <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-ink-950/90 to-transparent p-1.5 pt-6">
                    <p className="truncate text-[10px] text-ivory-100">{u.name}</p>
                  </div>
                  <span title={issues.map((x) => x.message).join("\n") || "No issues found"} className={clsx("absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full", worst === "ok" ? "bg-teal-400/90 text-ink-950" : worst === "warning" ? "bg-amber-400/90 text-ink-950" : "bg-rose-400/90 text-ink-950")}>
                    {worst === "ok" ? <CheckCircle2 className="h-3.5 w-3.5" aria-label="No issues" /> : <AlertTriangle className="h-3.5 w-3.5" aria-label={`${issues.length} issue(s)`} />}
                  </span>
                  {u.is_panorama && <Badge tone="blue" className="absolute right-1.5 top-1.5">360°</Badge>}
                  {canEdit && (
                    <button onClick={() => remove(u)} aria-label={`Delete ${u.name}`} className="absolute right-1.5 bottom-7 hidden rounded-lg bg-ink-950/80 p-1.5 text-rose-400 group-hover:block group-focus-within:block focus:block">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      <aside className="space-y-4">
        <Card>
          <div className="flex items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 font-display text-xl text-ivory-50"><ScanSearch className="h-5 w-5 text-teal-300" aria-hidden />Capture check</h2>
            {rep?.status && rep.status !== "NOT_ANALYZED" && <Badge tone={rep.status === "READY" ? "teal" : rep.status === "PENDING" ? "blue" : rep.status === "READY_WITH_WARNINGS" ? "amber" : "rose"}>{rep.status.replace(/_/g, " ").toLowerCase()}</Badge>}
          </div>
          <p className="mt-1 text-sm text-ink-300">Sharpness, exposure, duplicates and feature overlap, measured on your photos before you spend reconstruction minutes.</p>
          {rep?.status === "PENDING" && <div className="mt-4"><div className="indeterminate h-1 rounded-full bg-white/8" /><p className="mt-2 text-xs text-ink-300" role="status">Analysing {items.length} photos…</p></div>}
          {rep && rep.status !== "PENDING" && rep.status !== "NOT_ANALYZED" && rep.report?.summary && (
            <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <Stat k="Photos analysed" v={rep.report.summary.decoded ?? rep.report.summary.uploaded} />
              <Stat k="Recommended" v={(rep.report.recommended_mode ?? "—").replace(/_/g, " ").toLowerCase()} />
              <Stat k="Warnings" v={rep.report.summary.warnings} />
              <Stat k="Errors" v={rep.report.summary.errors} />
            </dl>
          )}
          {rep?.stale && rep.status !== "NOT_ANALYZED" && <p className="mt-3 text-xs text-amber-400">Photos changed since this report. Re-run the check.</p>}
          {rep?.report?.message && <p className="mt-3 text-sm text-rose-400">{rep.report.message}</p>}
          {canEdit && (
            <Button variant="glass" className="mt-4 w-full" onClick={analyze} disabled={!items.length || rep?.status === "PENDING"}>
              <RefreshCw className="h-4 w-4" />{rep?.status === "NOT_ANALYZED" ? "Run capture check" : "Re-run check"}
            </Button>
          )}
          {rep?.report?.disclaimer && <p className="mt-3 text-xs text-ink-400">{rep.report.disclaimer}</p>}
        </Card>
        {rep?.report?.images && <IssueSummary images={rep.report.images} />}
      </aside>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: unknown }) {
  return (
    <div className="rounded-xl bg-white/[0.04] p-3">
      <dt className="text-xs text-ink-300">{k}</dt>
      <dd className="mt-0.5 font-medium capitalize text-ivory-50 tabular-nums">{String(v ?? "—")}</dd>
    </div>
  );
}

function IssueSummary({ images }: { images: CaptureImage[] }) {
  const groups = new Map<string, { message: string; fix: string; severity: string; names: string[] }>();
  for (const im of images) for (const i of im.issues) {
    const g = groups.get(i.code) ?? { message: i.message, fix: i.fix, severity: i.severity, names: [] };
    g.names.push(im.name);
    groups.set(i.code, g);
  }
  if (!groups.size) return <Card><p className="flex items-center gap-2 text-sm text-teal-300"><CheckCircle2 className="h-4 w-4" />No per-photo issues detected.</p></Card>;
  return (
    <Card>
      <h3 className="font-display text-lg text-ivory-50">What to fix</h3>
      <ul className="mt-3 space-y-3">
        {[...groups.entries()].map(([code, g]) => (
          <li key={code} className="rounded-xl bg-white/[0.04] p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-ivory-100">{code.replace(/_/g, " ").toLowerCase()}</span>
              <Badge tone={g.severity === "error" ? "rose" : "amber"}>{g.names.length} photo{g.names.length > 1 ? "s" : ""}</Badge>
            </div>
            <p className="mt-1 text-xs text-ink-300">{g.message}</p>
            <p className="mt-1 text-xs text-teal-300">Fix: {g.fix}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}
