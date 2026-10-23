"use client";

import type { Artifact } from "@/lib/schemas";
import { WorldViewer } from "@/components/viewer/WorldViewer";

const art = (kind: string, name: string, mime: string, meta: Record<string, unknown> = {}): Artifact => ({
  id: `fixture-${name}`,
  job_id: "fixture",
  kind,
  name,
  mime,
  bytes: 0,
  sha256: "",
  url: `/e2e/world/${name}`,
  meta,
});

const ARTIFACTS: Artifact[] = [
  art("WORLD", "world.json", "application/json"),
  art("SPLAT_SPZ", "room.spz", "application/octet-stream", { variant: "100k" }),
  art("COLLIDER_MESH", "collider.glb", "model/gltf-binary"),
];

export function FixtureWorld() {
  return (
    <div className="h-dvh p-2">
      <WorldViewer title="E2E fixture room" artifacts={ARTIFACTS} tour={null} className="h-full" />
    </div>
  );
}
