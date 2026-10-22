"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/hooks";
import type { Artifact, Job, Tour } from "@/lib/schemas";
import { ErrorNote, Spinner } from "@/components/ui";
import { TourViewer } from "@/components/viewer/TourViewer";
import { PanoramaViewer } from "@/components/viewer/PanoramaViewer";
import { isWorldArtifactSet, WorldViewer } from "@/components/viewer/WorldViewer";

export default function ViewerPage() {
  const { id } = useParams<{ id: string }>();
  const project = useProject(id);
  const arts = useQuery<{ job: Job | null; artifacts: Artifact[] }, ApiError>({ queryKey: ["artifacts", id], queryFn: () => api(`/api/v1/projects/${id}/artifacts`) });
  const tour = useQuery<Tour, ApiError>({ queryKey: ["tour", id], queryFn: () => api(`/api/v1/projects/${id}/tour`) });
  if (arts.isLoading || project.isLoading) return <Spinner label="Loading viewer" />;
  if (arts.error) return <ErrorNote error={arts.error} />;
  const job = arts.data?.job;
  const pano = (tour.data?.nodes ?? []).filter((n) => n.kind === "PANORAMA" && n.image_url).map((n) => ({ id: n.id, label: n.label || n.ref, image: n.image_url as string, thumb: n.thumb_url }));
  return (
    <div className="-mx-4 -my-8 sm:-mx-6">
      <div className="h-[calc(100dvh-4rem)] p-2 sm:p-3">
        {isWorldArtifactSet(arts.data?.artifacts ?? []) ? (
          <WorldViewer title={project.data?.title ?? ""} artifacts={arts.data?.artifacts ?? []} tour={tour.data} fallback={job?.fallback} className="h-full" />
        ) : job?.mode === "PANORAMA_TOUR" && pano.length ? (
          <PanoramaViewer nodes={pano} title={project.data?.title ?? ""} className="h-full" />
        ) : (
          <TourViewer title={project.data?.title ?? ""} artifacts={arts.data?.artifacts ?? []} tour={tour.data} result={job?.result} className="h-full" />
        )}
      </div>
      <Link href={`/app/projects/${id}`} className="glass fixed bottom-6 left-6 z-30 hidden items-center gap-1.5 rounded-xl px-3 py-2 text-sm text-ivory-100 hover:bg-white/10 lg:inline-flex">
        <ArrowLeft className="h-4 w-4" />Project
      </Link>
    </div>
  );
}
