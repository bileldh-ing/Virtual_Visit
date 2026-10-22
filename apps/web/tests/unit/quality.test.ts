import { describe, expect, it } from "vitest";
import { classifyLayers, outputHeadline } from "@/lib/quality";
import { passwordHints } from "@/lib/validation";
import { stageStatuses, PHOTOGRAMMETRY_STAGES } from "@/lib/schemas";

const cpuResult = {
  output_type: "sparse_point_cloud",
  metrics: { images_uploaded: 48, images_registered: 42, sparse_points: 3519, compute_mode: "CPU" },
};
const arts = [{ kind: "POINT_CLOUD_WEB" }, { kind: "VIEWPOINTS" }, { kind: "PREVIEW" }];

describe("honest quality labels", () => {
  it("labels the CPU sparse run truthfully", () => {
    const layers = classifyLayers(cpuResult, arts);
    const by = Object.fromEntries(layers.map((l) => [l.key, l]));
    expect(by.poses.status).toBe("reconstructed");
    expect(by.poses.detail).toContain("42 of 48");
    expect(by.sparse.detail).toContain("3,519");
    expect(by.dense.status).toBe("unavailable");
    expect(by.dense.detail).toMatch(/CUDA/);
    expect(by.scale.status).toBe("inferred");
    expect(by.up.status).toBe("inferred");
  });
  it("headline says the mesh is unavailable on CPU", () => {
    expect(outputHeadline(cpuResult)).toBe("Sparse point cloud · dense mesh unavailable on CPU");
    expect(outputHeadline(null)).toBe("No output yet");
  });
  it("panorama tours do not claim geometry", () => {
    const layers = classifyLayers({ output_type: "panorama_tour" }, [{ kind: "PANORAMA" }]);
    expect(layers.map((l) => l.key)).toEqual(["panorama"]);
  });
});

describe("password hints mirror API policy", () => {
  it("flags weak passwords", () => {
    expect(passwordHints("short")).toHaveLength(3);
    expect(passwordHints("Long-Enough-Pass1")).toEqual([]);
  });
});

describe("stage statuses come from events only", () => {
  it("marks done/skipped/running from persisted events", () => {
    const ev = (id: number, stage: string, event: string) => ({ id, stage, event, data: {}, created_at: "2026-10-22T00:00:00Z" });
    const st = stageStatuses(
      {
        state: "ARTIFACT_OPTIMIZATION",
        events: [ev(1, "INPUT_VALIDATION", "state"), ev(2, "INPUT_VALIDATION", "completed"), ev(3, "CAMERA_POSE_ESTIMATION", "completed"), ev(4, "SPARSE_RECONSTRUCTION", "completed"), ev(5, "DENSE_RECONSTRUCTION", "skipped"), ev(6, "MESH_OR_SPLAT_GENERATION", "skipped")],
      },
      PHOTOGRAMMETRY_STAGES,
    );
    expect(st.QUEUED).toBe("done");
    expect(st.SPARSE_RECONSTRUCTION).toBe("done");
    expect(st.DENSE_RECONSTRUCTION).toBe("skipped");
    expect(st.ARTIFACT_OPTIMIZATION).toBe("running");
    expect(st.PREVIEW_GENERATION).toBe("pending");
  });
});
