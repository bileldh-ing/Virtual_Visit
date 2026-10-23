"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, ArrowRight, Box, Orbit, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError, formatDuration, newIdempotencyKey } from "@/lib/api";
import { useJobs } from "@/lib/hooks";
import { costText, engineName, jobEngine, MODEL_LABEL } from "@/lib/engines";
import type { EngineOptions, Job, Project } from "@/lib/schemas";
import { Badge, Button, Card, EmptyState, ErrorNote, Modal, stateTone } from "@/components/ui";
import { relTime } from "./ProjectCard";

const PICKER_ORDER = ["auto", "marble_world", "depth_room", "colmap_sparse", "panorama_tour", "gaussian_splat_local"];

export function JobsTab({ project }: { project: Project }) {
  const jobs = useJobs(project.id, true);
  const qc = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [engine, setEngine] = useState("auto");
  const [model, setModel] = useState("marble-1.0-draft");
  const [allowFallback, setAllowFallback] = useState(true);
  const opts = useQuery<EngineOptions, ApiError>({
    queryKey: ["engines", project.id, engine, model, project.photo_count],
    queryFn: () => api(`/api/v1/projects/${project.id}/engines?engine=${engine}&marble_model=${encodeURIComponent(model)}`),
    enabled: open,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [needConfirm, setNeedConfirm] = useState<string | null>(null);
  const canEdit = project.role !== "VIEWER";
  const [key] = useState(newIdempotencyKey);

  const start = async (confirm = false) => {
    setBusy(true);
    setError(null);
    try {
      const job = await api<Job>(`/api/v1/projects/${project.id}/reconstruction-jobs`, { method: "POST", body: { engine, allow_fallback: allowFallback, marble_model: model, idempotency_key: key, confirm_warnings: confirm } });
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
                  <span className="text-sm text-ivory-100">{j.engine ? engineName(jobEngine(j).used) : j.mode.replace(/_/g, " ").toLowerCase()}</span>
                  {j.fallback && <Badge tone="amber">fallback</Badge>}
                  <span className="text-xs text-ink-400">#{j.id.slice(0, 8)}</span>
                </div>
                <p className="mt-1 text-xs text-ink-300">
                  {j.input_count} photos · started {relTime(j.created_at)}
                  {j.result?.metrics?.processing_seconds != null && ` · ${formatDuration(Number(j.result.metrics.processing_seconds))}`}
                  {j.result?.metrics?.images_registered != null && ` · ${j.result.metrics.images_registered} registered`}
                  {j.result?.metrics?.sparse_points != null && ` · ${Number(j.result.metrics.sparse_points).toLocaleString("en-US")} points`}
                </p>
                {j.fallback && <p className="mt-1 text-xs text-amber-300">{j.fallback.notice}</p>}
              </div>
              <ArrowRight className="h-4 w-4 text-ink-400" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>

      <Modal open={open} onOpenChange={(v) => { setOpen(v); setNeedConfirm(null); setError(null); }} title="Start a reconstruction" description={`Choose how ${project.photo_count} photos become a 3D space. Costs are shown before anything starts.`}>
        <EnginePicker opts={opts.data} loading={opts.isLoading} error={opts.error} engine={engine} setEngine={setEngine} model={model} setModel={setModel} allowFallback={allowFallback} setAllowFallback={setAllowFallback} />
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
            <Button onClick={() => start(false)} loading={busy} disabled={!opts.data?.chain.engines.length} data-testid="start-job">
              {startLabel(opts.data)}
            </Button>
          </div>
        )}
      </Modal>
    </div>
  );
}

function startLabel(o: EngineOptions | undefined): string {
  const p = o?.chain.primary;
  if (!p) return "Start";
  const est = o?.engines.find((e) => e.name === p)?.estimate;
  return p === "marble_world" && est?.credits ? `Start · ${Math.round(est.credits).toLocaleString("en-US")} credits` : `Start · ${engineName(p)}`;
}

function EnginePicker(props: {
  opts: EngineOptions | undefined;
  loading: boolean;
  error: ApiError | null;
  engine: string;
  setEngine: (v: string) => void;
  model: string;
  setModel: (v: string) => void;
  allowFallback: boolean;
  setAllowFallback: (v: boolean) => void;
}) {
  const { opts, engine, setEngine, model, setModel, allowFallback, setAllowFallback } = props;
  const byName = new Map((opts?.engines ?? []).map((e) => [e.name, e]));
  const marble = byName.get("marble_world");
  const chain = opts?.chain;
  return (
    <div className="space-y-3" data-testid="engine-picker">
      <ErrorNote error={props.error} />
      <fieldset className="space-y-2">
        <legend className="sr-only">Engine</legend>
        {PICKER_ORDER.map((name) => {
          const e = byName.get(name);
          const isAuto = name === "auto";
          if (!isAuto && !e) return null;
          const runnable = isAuto ? Boolean(chain?.engines.length) : Boolean(e?.runnable);
          const desc = isAuto
            ? "Best available: a Marble world when it is configured and has credits, otherwise the free depth room. Every switch is recorded."
            : e!.description;
          return (
            <label
              key={name}
              className={clsx(
                "flex gap-3 rounded-2xl border p-3.5 transition",
                engine === name ? "border-teal-400/60 bg-teal-400/[0.06]" : "border-white/10",
                runnable || isAuto ? "cursor-pointer hover:border-white/20" : "cursor-not-allowed opacity-60",
              )}
            >
              <input type="radio" name="engine" value={name} checked={engine === name} disabled={!runnable && !isAuto} onChange={() => setEngine(name)} className="mt-1 accent-teal-400" />
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="text-sm font-medium text-ivory-50">{isAuto ? "Automatic" : e!.label}</span>
                  {!isAuto && <span className={clsx("text-xs tabular-nums", e!.name === "marble_world" ? "text-amber-300" : "text-teal-300")} data-testid={`cost-${name}`}>{costText(e!.estimate)}{e!.estimate?.minutes ? ` · ~${Math.round(e!.estimate.minutes)} min` : ""}</span>}
                </span>
                <span className="mt-0.5 block text-xs text-ink-300">{desc.length > 190 ? desc.slice(0, 187) + "…" : desc}</span>
                {!isAuto && e!.honesty_text && <span className="mt-1 block text-[11px] text-ink-400">Result: {e!.honesty_text}</span>}
                {!runnable && !isAuto && e?.not_runnable_reason && <span className="mt-1 block text-xs text-amber-300">{e.not_runnable_reason}</span>}
                {name === "marble_world" && engine === name && (opts?.marble_models?.length ?? 0) > 1 && (
                  <span className="mt-2 flex flex-wrap gap-1.5" role="radiogroup" aria-label="Marble quality">
                    {opts!.marble_models!.map((m) => (
                      <button key={m} type="button" role="radio" aria-checked={model === m} onClick={(ev) => { ev.preventDefault(); setModel(m); }} className={clsx("rounded-lg px-2.5 py-1 text-xs ring-1 transition", model === m ? "bg-teal-400/15 text-teal-200 ring-teal-400/50" : "text-ink-300 ring-white/10 hover:ring-white/25")}>
                        {MODEL_LABEL[m] ?? m}
                      </button>
                    ))}
                  </span>
                )}
              </span>
            </label>
          );
        })}
      </fieldset>
      {(engine === "auto" || engine === "marble_world") && (
        <label className="flex items-start gap-2 text-xs text-ink-300">
          <input type="checkbox" checked={allowFallback} onChange={(ev) => setAllowFallback(ev.target.checked)} className="mt-0.5 accent-teal-400" />
          If Marble fails (key, credits, outage), build it with the free engine instead and say so on the result.
        </label>
      )}
      <div className="rounded-xl bg-white/[0.04] p-3 text-xs text-ink-300" data-testid="cost-summary">
        {props.loading && <span>Checking engines and live Marble balance…</span>}
        {chain && (
          <>
            <p>
              <span className="text-ivory-100">Will run:</span> {chain.engines.map(engineName).join(" → ") || "nothing (no engine can process these photos)"}
            </p>
            {chain.notice && <p className="mt-1 text-amber-300">{chain.notice}</p>}
            {marble && (
              <p className="mt-1">
                Marble balance: {opts?.marble.balance != null ? `${Math.round(opts.marble.balance).toLocaleString("en-US")} credits` : opts?.marble.configured ? "unknown" : "not configured"}
                {opts?.marble_allowance && ` · plan allowance used this month: ${Math.round(opts.marble_allowance.used).toLocaleString("en-US")} / ${Math.round(opts.marble_allowance.limit).toLocaleString("en-US")}`}
              </p>
            )}
            {chain.primary === "marble_world" && marble?.estimate?.note && <p className="mt-1 text-ink-400">{marble.estimate.note}</p>}
          </>
        )}
      </div>
    </div>
  );
}
