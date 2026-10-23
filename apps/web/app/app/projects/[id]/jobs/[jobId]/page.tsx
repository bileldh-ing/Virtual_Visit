"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Eye } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useProject } from "@/lib/hooks";
import type { Job } from "@/lib/schemas";
import { Button, Card } from "@/components/ui";
import { JobProgress } from "@/components/project/JobProgress";

export default function JobPage() {
  const { id, jobId } = useParams<{ id: string; jobId: string }>();
  const project = useProject(id);
  const qc = useQueryClient();
  const [done, setDone] = useState<Job | null>(null);
  const ok = done && (done.state === "SUCCEEDED" || done.state === "PARTIAL_SUCCESS");
  return (
    <div className="mx-auto max-w-3xl">
      <Link href={`/app/projects/${id}?tab=jobs`} className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-300 hover:text-ivory-50"><ArrowLeft className="h-4 w-4" />{project.data?.title ?? "Project"}</Link>
      <h1 className="mb-6 font-display text-3xl text-ivory-50">Reconstruction <span className="font-mono text-xl text-ink-400">#{jobId.slice(0, 8)}</span></h1>
      <JobProgress
        jobId={jobId}
        onDone={(j) => {
          setDone(j);
          void qc.invalidateQueries({ queryKey: ["artifacts", id] });
          void qc.invalidateQueries({ queryKey: ["tour", id] });
          void qc.invalidateQueries({ queryKey: ["project", id] });
        }}
      />
      {ok && (
        <Card className="mt-6 flex flex-wrap items-center justify-between gap-4 border-teal-400/25 p-6">
          <div>
            <p className="font-display text-xl text-ivory-50">Your reconstruction is ready</p>
            <p className="text-sm text-ink-300">
              {done?.engine_used === "marble_world" ? "Walk through the generated world: WASD, mouse-look, minimap and your original photos." : done?.engine_used === "depth_room" ? "Walk through the depth-estimated room built from your photos." : "Explore the real camera viewpoints and sparse points in 3D."}
            </p>
          </div>
          <Link href={`/app/projects/${id}/viewer`}><Button size="lg"><Eye className="h-4 w-4" />Open viewer</Button></Link>
        </Card>
      )}
    </div>
  );
}
