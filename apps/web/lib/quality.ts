/**
 * Honest output labelling. Every viewer layer is classified as:
 *  - "reconstructed": computed from the user's photos by the pipeline (e.g. COLMAP sparse points, camera poses)
 *  - "inferred": derived by assumption, not measurement (e.g. estimated "up" direction, relative scale)
 *  - "unavailable": not produced by this run, with the reason
 * Pure, unit-tested.
 */

export type LayerStatus = "reconstructed" | "inferred" | "unavailable";
export type Layer = { key: string; label: string; status: LayerStatus; detail: string };

type ArtifactLite = { kind: string; meta?: Record<string, unknown> };
type ResultLite = { output_type?: string; metrics?: Record<string, unknown>; limitations?: string[] } | null | undefined;

export function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function classifyLayers(result: ResultLite, artifacts: ArtifactLite[]): Layer[] {
  const kinds = new Set(artifacts.map((a) => a.kind));
  const m = result?.metrics ?? {};
  const cpu = m.compute_mode === "CPU";
  const layers: Layer[] = [];
  const registered = num(m.images_registered);
  const uploaded = num(m.images_uploaded);
  const sparse = num(m.sparse_points);

  if (kinds.has("PANORAMA") || result?.output_type === "panorama_tour") {
    layers.push({ key: "panorama", label: "360° panoramas", status: "reconstructed", detail: "Your equirectangular photos, shown as captured." });
  }
  if (kinds.has("VIEWPOINTS")) {
    layers.push({
      key: "poses",
      label: "Camera viewpoints",
      status: "reconstructed",
      detail: registered != null && uploaded != null ? `${registered} of ${uploaded} photos placed by COLMAP.` : "Camera positions solved by COLMAP.",
    });
  }
  if (kinds.has("POINT_CLOUD_WEB")) {
    layers.push({
      key: "sparse",
      label: "Sparse point cloud",
      status: "reconstructed",
      detail: sparse != null ? `${sparse.toLocaleString("en-US")} triangulated points from matched features.` : "Triangulated feature points.",
    });
  }
  if (kinds.has("POINT_CLOUD_DENSE")) {
    layers.push({ key: "dense", label: "Dense point cloud", status: "reconstructed", detail: "PatchMatch stereo output." });
  } else if (result?.output_type !== "panorama_tour") {
    layers.push({
      key: "dense",
      label: "Dense mesh",
      status: "unavailable",
      detail: cpu ? "Dense stereo needs a CUDA GPU; this run used a CPU worker." : "Not produced by this run.",
    });
  }
  if (kinds.has("MESH")) layers.push({ key: "mesh", label: "Textured mesh", status: "reconstructed", detail: "Poisson surface from the dense cloud." });
  if (kinds.has("VIEWPOINTS") || kinds.has("POINT_CLOUD_WEB")) {
    layers.push({ key: "up", label: "Floor orientation", status: "inferred", detail: "“Up” is estimated from the average camera tilt (assumes upright photos)." });
    layers.push({ key: "scale", label: "Scale", status: "inferred", detail: "Relative only. Distances are not metres, so measurement is disabled." });
  }
  return layers;
}

export function outputHeadline(result: ResultLite): string {
  const t = result?.output_type;
  if (t === "sparse_point_cloud") return "Sparse point cloud · dense mesh unavailable on CPU";
  if (t === "dense_point_cloud") return "Dense point cloud";
  if (t === "mesh") return "Textured mesh";
  if (t === "panorama_tour") return "360° panorama tour";
  return t ? t.replace(/_/g, " ") : "No output yet";
}
