"use client";

import clsx from "clsx";
import { motion } from "framer-motion";
import { Check, CircleDashed, Loader2, MinusCircle, RotateCcw, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, formatDuration } from "@/lib/api";
import { useLiveJob, useNow } from "@/lib/hooks";
import { jobEngine } from "@/lib/engines";
import { DEPTH_STAGES, MARBLE_STAGES, PANORAMA_STAGES, PHOTOGRAMMETRY_STAGES, stageStatuses, type Job, type StageStatus } from "@/lib/schemas";
import { EngineSummary } from "./EngineSummary";
import { Badge, Button, Card, ErrorNote, stateTone } from "@/components/ui";

const ICON: Record<StageStatus, React.ReactNode> = {
  pending: <CircleDashed className="h-4 w-4 text-ink-400" aria-hidden />,
  running: <Loader2 className="h-4 w-4 animate-spin text-teal-300" aria-hidden />,
  done: <Check className="h-4 w-4 text-ink-950" aria-hidden />,
  skipped: <MinusCircle className="h-4 w-4 text-ink-300" aria-hidden />,
  failed: <X className="h-4 w-4 text-ink-950" aria-hidden />,
};

function stageSeconds(job: Job, stage: string): number | null {
  const evs = job.events ?? [];
  const start = [...evs].reverse().find((e) => e.stage === stage && (e.event === "started" || e.event === "state"));
  const end = [...evs].reverse().find((e) => e.stage === stage && (e.event === "completed" || e.event === "failed"));
  if (start && end) return (new Date(end.created_at).getTime() - new Date(start.created_at).getTime()) / 1000;
  return null;
}

export function JobProgress({ jobId, onDone }: { jobId: string; onDone?: (job: Job) => void }) {
  const { data: job, error, streaming } = useLiveJob(jobId);
  const now = useNow(1000, job?.server_time);
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  const terminal = job?.is_terminal;
  useEffect(() => {
    if (terminal && job) doneRef.current?.(job);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terminal]);
  if (error) return <ErrorNote error={error} />;
  if (!job) return <div className="indeterminate h-1 rounded-full bg-white/8" />;

  const eng = jobEngine(job).used;
  const stages = job.mode === "PANORAMA_TOUR" ? PANORAMA_STAGES : eng === "marble_world" ? MARBLE_STAGES : eng === "depth_room" ? DEPTH_STAGES : PHOTOGRAMMETRY_STAGES;
  const st = stageStatuses(job, stages);
  const elapsed = job.started_at ? ((job.finished_at ? new Date(job.finished_at).getTime() : now) - new Date(job.started_at).getTime()) / 1000 : 0;
  const stageElapsed = job.stage_started_at && !job.is_terminal ? (now - new Date(job.stage_started_at).getTime()) / 1000 : null;
  const doneCount = Object.values(st).filter((s) => s === "done" || s === "skipped").length;

  const act = async (kind: "cancel" | "retry") => {
    setBusy(kind);
    setActionError(null);
    try {
      await api(`/api/v1/reconstruction-jobs/${job.id}/${kind}`, { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["job", job.id] });
      await qc.invalidateQueries({ queryKey: ["jobs", job.project_id] });
    } catch (e) {
      setActionError(e as ApiError);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className="p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Badge tone={stateTone(job.state)}>{job.state.replace(/_/g, " ").toLowerCase()}</Badge>
            <span className="text-xs text-ink-400">{job.mode.replace(/_/g, " ").toLowerCase()} · attempt {Math.max(1, job.attempt_count)} of {job.max_attempts}</span>
            {!job.is_terminal && <span className={clsx("text-xs", streaming ? "text-teal-300" : "text-ink-400")}>{streaming ? "● live" : "polling"}</span>}
          </div>
          <p className="mt-2 font-display text-2xl text-ivory-50" aria-live="polite">{job.state_text}</p>
          <p className="mt-1 text-sm text-ink-300">
            {job.input_count} photos · elapsed {formatDuration(elapsed)}
            {stageElapsed != null && ` · this stage ${formatDuration(stageElapsed)}`}
          </p>
        </div>
        <div className="flex gap-2">
          {!job.is_terminal && !job.cancel_requested && (
            <Button variant="glass" size="sm" onClick={() => act("cancel")} loading={busy === "cancel"}><Square className="h-3.5 w-3.5" />Cancel</Button>
          )}
          {["FAILED", "CANCELLED", "PARTIAL_SUCCESS"].includes(job.state) && (
            <Button size="sm" onClick={() => act("retry")} loading={busy === "retry"}><RotateCcw className="h-3.5 w-3.5" />Retry</Button>
          )}
        </div>
      </div>
      {job.engine && <EngineSummary job={job} className="mt-4 border-t border-white/8 pt-4" />}
      <ErrorNote error={actionError} className="mt-4" />
      {job.error && (
        <div role="alert" className="mt-4 rounded-xl border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-400">
          <strong className="font-medium">{job.error.category.replace(/_/g, " ").toLowerCase()}:</strong> {job.error.message}
          {job.next_attempt_at && !job.is_terminal && <span className="block text-xs text-rose-400/80">Automatic retry scheduled.</span>}
        </div>
      )}

      {/* Stage progress is derived only from persisted events, never from elapsed time. */}
      <div className="mt-6 h-1.5 overflow-hidden rounded-full bg-white/8" role="progressbar" aria-label="Completed stages" aria-valuemin={0} aria-valuemax={stages.length} aria-valuenow={doneCount}>
        <motion.div className="h-full rounded-full bg-teal-400" animate={{ width: `${(doneCount / stages.length) * 100}%` }} transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }} />
      </div>
      <p className="mt-1.5 text-xs text-ink-400">{doneCount} of {stages.length} stages finished. No percentage is shown inside a stage because the engines do not report one.</p>

      <ol className="mt-5 space-y-1">
        {stages.map(([key, label, desc]) => {
          const s = st[key];
          const secs = stageSeconds(job, key);
          const skipEv = (job.events ?? []).find((e) => e.stage === key && e.event === "skipped");
          return (
            <li key={key} className={clsx("flex items-start gap-3 rounded-xl px-3 py-2.5", s === "running" && "bg-teal-400/[0.07]")}>
              <span className={clsx("mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full", s === "done" && "bg-teal-400", s === "failed" && "bg-rose-400", s === "running" && "bg-teal-400/15", (s === "pending" || s === "skipped") && "bg-white/5")}>{ICON[s]}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className={clsx("text-sm", s === "pending" ? "text-ink-400" : "text-ivory-100")}>{label}</span>
                  {secs != null && <span className="text-xs tabular-nums text-ink-400">{formatDuration(secs)}</span>}
                </div>
                <p className="text-xs text-ink-400">{s === "skipped" && skipEv ? String(skipEv.data?.reason ?? "Skipped") : desc}</p>
                {s === "running" && <div className="indeterminate mt-2 h-0.5 rounded-full bg-white/8" />}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
