"use client";

import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { TourBody, type PublicTourData } from "@/components/viewer/PublicTour";

/** The landing hero's live sample: a real reconstruction an admin flagged as public sample. */
export function LandingSample() {
  const q = useQuery<PublicTourData, ApiError>({ queryKey: ["sample"], queryFn: () => api("/api/v1/public/sample"), retry: false });
  if (q.isLoading) return <div className="glass flex h-full items-center justify-center rounded-3xl"><div className="indeterminate h-1 w-40 rounded-full bg-white/8" /></div>;
  if (!q.data) {
    return (
      <div className="glass flex h-full flex-col items-center justify-center rounded-3xl p-8 text-center">
        <p className="font-display text-xl text-ivory-50">Live sample unavailable</p>
        <p className="mt-2 max-w-sm text-sm text-ink-300">No public sample reconstruction is published on this deployment yet. Sign up to reconstruct your own photos.</p>
      </div>
    );
  }
  return <TourBody data={q.data} className="h-full" />;
}
