"use client";

import clsx from "clsx";
import { AlertTriangle, Cpu, Sparkles } from "lucide-react";
import { costText, engineName, jobEngine, MODEL_LABEL } from "@/lib/engines";
import type { Job } from "@/lib/schemas";

/** Which engine built this job, at what cost, and — when it is not the one asked for — why. */
export function EngineSummary({ job, className, compact }: { job: Job; className?: string; compact?: boolean }) {
  const e = jobEngine(job);
  const paid = e.used === "marble_world";
  return (
    <div className={clsx("space-y-2", className)} data-testid="engine-summary">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="inline-flex items-center gap-1.5 text-ivory-100">
          {paid ? <Sparkles className="h-4 w-4 text-amber-300" aria-hidden /> : <Cpu className="h-4 w-4 text-teal-300" aria-hidden />}
          <span className="text-ink-300">{job.is_terminal ? "Built with" : "Engine"}</span>
          <strong className="font-medium">{engineName(e.used)}</strong>
          {paid && e.model && <span className="text-ink-300">· {MODEL_LABEL[e.model] ?? e.model} ({e.model})</span>}
        </span>
        {!compact && <span className="text-ink-300">· {paid ? "estimate" : "cost"}: {costText(e.estimate)}</span>}
        {!compact && e.requested && e.requested !== e.used && <span className="text-ink-400">· requested: {engineName(e.requested)}</span>}
      </div>
      {e.fallbackNotice && (
        <p role="note" className="flex items-start gap-2 rounded-xl border border-amber-400/25 bg-amber-400/[0.07] px-3 py-2 text-xs text-amber-200" data-testid="fallback-reason">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            {e.fallbackNotice}
            {!compact && e.fallbackReason && <span className="block text-amber-200/70">Reason recorded: {e.fallbackReason}</span>}
          </span>
        </p>
      )}
    </div>
  );
}
