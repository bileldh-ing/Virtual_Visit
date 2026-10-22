import { z } from "zod";

/** Runtime validation of API payloads the viewer depends on. */

export const ViewpointSchema = z.object({
  image_id: z.number().optional(),
  name: z.string(),
  upload_id: z.string().nullable().optional(),
  position: z.tuple([z.number(), z.number(), z.number()]),
  rotation_c2w: z.array(z.array(z.number()).length(3)).length(3),
  width: z.number(),
  height: z.number(),
  focal_px: z.number().nullable().optional(),
});

export const CamerasSchema = z.object({
  schema: z.string().optional(),
  scale: z.string(),
  scale_note: z.string().optional(),
  up_estimate_method: z.string().optional(),
  viewer_transform: z.object({
    origin: z.tuple([z.number(), z.number(), z.number()]),
    rotation: z.array(z.array(z.number()).length(3)).length(3),
    extent: z.number(),
  }),
  viewpoints: z.array(ViewpointSchema),
});
export type Cameras = z.infer<typeof CamerasSchema>;
export type Viewpoint = z.infer<typeof ViewpointSchema>;

export const ArtifactSchema = z.object({
  id: z.string(),
  job_id: z.string(),
  kind: z.string(),
  name: z.string(),
  mime: z.string(),
  bytes: z.number(),
  sha256: z.string(),
  url: z.string(),
  meta: z.record(z.string(), z.unknown()).optional(),
  validation: z.object({ ok: z.boolean() }).passthrough().nullable().optional(),
});
export type Artifact = z.infer<typeof ArtifactSchema>;

export const TourSchema = z.object({
  rooms: z.array(
    z.object({ id: z.string(), name: z.string(), description: z.string(), sort: z.number(), dimensions_note: z.string().optional() }),
  ),
  nodes: z.array(
    z.object({
      id: z.string(),
      kind: z.enum(["VIEWPOINT", "PANORAMA"]),
      ref: z.string(),
      label: z.string(),
      sort: z.number(),
      source: z.string(),
      room_id: z.string().nullable(),
      upload_id: z.string().nullable(),
      image_url: z.string().nullable(),
      thumb_url: z.string().nullable(),
    }),
  ),
  edges: z.array(z.object({ id: z.string(), from: z.string(), to: z.string(), label: z.string(), yaw: z.number().nullable(), pitch: z.number().nullable() })),
  hotspots: z.array(
    z.object({
      id: z.string(),
      node_id: z.string().nullable(),
      room_id: z.string().nullable(),
      kind: z.string(),
      title: z.string(),
      description: z.string(),
      position: z.record(z.string(), z.unknown()),
    }),
  ),
});
export type Tour = z.infer<typeof TourSchema>;
export type TourNode = Tour["nodes"][number];
export type Hotspot = Tour["hotspots"][number];

export const PanoTourSchema = z.object({
  mode: z.literal("PANORAMA_TOUR"),
  nodes: z.array(z.object({ index: z.number(), upload_id: z.string().nullable().optional(), image: z.string(), thumb: z.string() })),
});

export type JobEvent = { id: number; stage: string; event: string; data: Record<string, unknown>; created_at: string };

export type Job = {
  id: string;
  project_id: string;
  mode: string;
  state: string;
  state_text: string;
  is_terminal: boolean;
  cancel_requested: boolean;
  attempt_count: number;
  max_attempts: number;
  error: { category: string; message: string } | null;
  result: {
    status?: string;
    output_type?: string;
    metrics?: Record<string, unknown>;
    warnings?: { code: string; message?: string }[];
    limitations?: string[];
    recapture_suggestions?: string[];
    steps?: { name: string; seconds: number }[];
  } | null;
  input_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  stage_started_at: string | null;
  next_attempt_at: string | null;
  server_time: string;
  events?: JobEvent[];
  attempts?: { number: number; state: string; worker_id: string; started_at: string; finished_at: string | null; error_category: string | null; engine?: Record<string, unknown> }[];
};

export type Project = {
  id: string;
  org_id: string;
  title: string;
  description: string;
  address_private: string | null;
  property_type: string;
  rooms_estimate: number | null;
  listing_status: string;
  status: string;
  capture_checklist: Record<string, boolean>;
  version: number;
  role: string | null;
  photo_count: number;
  cover_url: string | null;
  is_public_sample: boolean;
  created_at: string;
  updated_at: string;
  latest_job: { id: string; state: string; mode: string; updated_at: string } | null;
};

export type Me = {
  user: { id: string; email: string; display_name: string; email_verified: boolean; is_platform_admin: boolean; created_at: string };
  unread_notifications: number;
  workspaces: { id: string; name: string; role: string; is_personal: boolean }[];
};

export type UploadItem = {
  id: string;
  name: string;
  bytes: number;
  width: number;
  height: number;
  is_panorama: boolean;
  thumb_url: string;
  web_url: string;
  quality: { metrics: Record<string, unknown>; issues: { code: string; severity: string; message: string; fix: string }[] } | null;
};

/** Pipeline stages shown in the processing monitor, in order. */
export const PHOTOGRAMMETRY_STAGES = [
  ["QUEUED", "Queued", "Waiting for a reconstruction worker"],
  ["INPUT_VALIDATION", "Input validation", "Checksums, decoding and orientation of every photo"],
  ["CAMERA_POSE_ESTIMATION", "Feature matching", "COLMAP SIFT features + exhaustive matching"],
  ["SPARSE_RECONSTRUCTION", "Camera poses & sparse points", "COLMAP incremental mapper"],
  ["DENSE_RECONSTRUCTION", "Dense reconstruction", "PatchMatch stereo (requires a CUDA GPU)"],
  ["MESH_OR_SPLAT_GENERATION", "Mesh", "Poisson meshing of the dense cloud"],
  ["ARTIFACT_OPTIMIZATION", "Web optimisation", "Outlier filtering, point budget, viewer metadata"],
  ["PREVIEW_GENERATION", "Preview", "Top-down render of the real points"],
  ["ARTIFACT_VALIDATION", "Validation", "Every file parsed, counted and checksummed"],
] as const;

export const PANORAMA_STAGES = [
  ["QUEUED", "Queued", "Waiting for a worker"],
  ["INPUT_VALIDATION", "Input validation", "Equirectangular 2:1 check per image"],
  ["TEXTURE_OR_APPEARANCE_PROCESSING", "Panorama derivatives", "EXIF-free web copies and thumbnails"],
  ["ARTIFACT_VALIDATION", "Validation", "Manifest and checksums"],
] as const;

export const TERMINAL = new Set(["SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED", "EXPIRED"]);

export type StageStatus = "pending" | "running" | "done" | "skipped" | "failed";

/** Derive per-stage status purely from persisted events of the latest attempt (never from elapsed time). */
export function stageStatuses(job: Pick<Job, "state" | "events">, stages: readonly (readonly [string, string, string])[]): Record<string, StageStatus> {
  const out: Record<string, StageStatus> = {};
  for (const [k] of stages) out[k] = "pending";
  const events = job.events ?? [];
  let start = -1;
  events.forEach((e, i) => {
    if (e.event === "state" && e.stage === "INPUT_VALIDATION") start = i;
  });
  if (job.state === "QUEUED" || start < 0) {
    if ("QUEUED" in out) out.QUEUED = job.state === "QUEUED" ? "running" : TERMINAL.has(job.state) ? "done" : "pending";
    return out;
  }
  if ("QUEUED" in out) out.QUEUED = "done";
  let lastFailedStage: string | null = null;
  for (const e of events.slice(start)) {
    if (!(e.stage in out)) continue;
    if (e.event === "completed") out[e.stage] = "done";
    else if (e.event === "skipped") out[e.stage] = "skipped";
    else if (e.event === "failed") {
      out[e.stage] = "failed";
      lastFailedStage = e.stage;
    }
  }
  if (job.state in out && out[job.state] === "pending") out[job.state] = "running";
  if (job.state === "FAILED" && !lastFailedStage) {
    const running = Object.keys(out).find((k) => out[k] === "running");
    if (running) out[running] = "failed";
  }
  return out;
}
