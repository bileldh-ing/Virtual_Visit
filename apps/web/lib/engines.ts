import type { CostEstimate, Job } from "./schemas";

/** Short, honest names for each engine (the API's `label` is longer). */
export const ENGINE_SHORT: Record<string, string> = {
  auto: "Automatic",
  marble_world: "World Labs Marble",
  depth_room: "Free depth room",
  colmap_sparse: "Photogrammetry (COLMAP)",
  panorama_tour: "360° tour",
  gaussian_splat_local: "Gaussian splatting (GPU)",
};

export const MODEL_LABEL: Record<string, string> = {
  "marble-1.0-draft": "Draft",
  "marble-1.0": "Standard 1.0",
  "marble-1.1": "Standard",
  "marble-1.1-plus": "Plus",
};

export function engineName(name: string | null | undefined): string {
  if (!name) return "—";
  return ENGINE_SHORT[name] ?? name.replace(/_/g, " ");
}

/** "250 credits (≈ $0.20)" / "Free (CPU)" / "—" */
export function costText(e: CostEstimate | null | undefined): string {
  if (!e) return "—";
  if (e.credits != null && e.credits > 0) {
    const usd = e.usd != null ? ` (≈ $${e.usd.toFixed(2)})` : "";
    return `${Math.round(e.credits).toLocaleString("en-US")} credits${usd}`;
  }
  return "Free (CPU)";
}

/** The engine that actually produced (or is producing) this job, and why it is not the one asked for. */
export function jobEngine(job: Pick<Job, "engine" | "engine_used" | "fallback" | "cost_estimate" | "config" | "provider">) {
  const used = job.engine_used ?? job.cost_estimate?.chain?.primary ?? job.engine ?? null;
  const requested = job.engine ?? null;
  const intended = job.cost_estimate?.chain?.intended ?? requested;
  const skippedNotice = job.cost_estimate?.chain?.notice ?? null;
  const fallbackNotice = job.fallback?.notice ?? (used && intended && used !== intended ? skippedNotice : null);
  const model = (job.provider?.model as string | undefined) ?? ((job.config as Record<string, unknown> | undefined)?.marble_model as string | undefined) ?? null;
  const estimate = used ? job.cost_estimate?.engines?.[used] ?? null : null;
  return { used, requested, intended, fallbackNotice, fallbackReason: job.fallback?.reason ?? null, model, estimate };
}
