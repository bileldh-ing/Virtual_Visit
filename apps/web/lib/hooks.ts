"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { api, ApiError } from "./api";
import type { Job, Me, Project, UploadItem } from "./schemas";

export function useMe() {
  return useQuery<Me, ApiError>({
    queryKey: ["me"],
    queryFn: () => api<Me>("/api/v1/me"),
    retry: (n, e) => e.status !== 401 && n < 2,
    staleTime: 30_000,
  });
}

const WS_KEY = "rs.workspace";
const listeners = new Set<() => void>();
function readWs(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(WS_KEY);
  } catch {
    return null;
  }
}

/** Current workspace id: persisted choice if still a membership, else the first workspace. */
export function useWorkspace(me: Me | undefined) {
  const stored = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    readWs,
    () => null,
  );
  const ws = me?.workspaces.find((w) => w.id === stored) ?? me?.workspaces[0] ?? null;
  const setWorkspace = useCallback((id: string) => {
    try {
      window.localStorage.setItem(WS_KEY, id);
    } catch {
      /* private mode */
    }
    listeners.forEach((l) => l());
  }, []);
  return { workspace: ws, setWorkspace };
}

export function useProject(id: string) {
  return useQuery<Project, ApiError>({ queryKey: ["project", id], queryFn: () => api<Project>(`/api/v1/projects/${id}`) });
}

export function useUploads(id: string) {
  return useQuery<{ items: UploadItem[] }, ApiError>({
    queryKey: ["uploads", id],
    queryFn: () => api(`/api/v1/projects/${id}/uploads`),
  });
}

export function useJobs(projectId: string, poll = false) {
  return useQuery<{ items: Job[] }, ApiError>({
    queryKey: ["jobs", projectId],
    queryFn: () => api(`/api/v1/projects/${projectId}/reconstruction-jobs`),
    refetchInterval: poll ? 4000 : false,
  });
}

/**
 * Live job state: SSE stream of persisted stage events (server is the source of truth), with
 * polling as a fallback when EventSource is unavailable or the stream drops.
 */
export function useLiveJob(jobId: string) {
  const q = useQuery<Job, ApiError>({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>(`/api/v1/reconstruction-jobs/${jobId}`),
    refetchInterval: (query) => (query.state.data?.is_terminal ? false : 3000),
  });
  const [streaming, setStreaming] = useState(false);
  const { refetch } = q;
  const terminal = q.data?.is_terminal;
  useEffect(() => {
    if (terminal || typeof EventSource === "undefined") return;
    const es = new EventSource(`/api/v1/reconstruction-jobs/${jobId}/events`, { withCredentials: true });
    es.onopen = () => setStreaming(true);
    const onAny = () => void refetch();
    es.addEventListener("stage", onAny);
    es.addEventListener("job", onAny);
    es.addEventListener("end", () => {
      void refetch();
      es.close();
      setStreaming(false);
    });
    es.onerror = () => setStreaming(false);
    return () => {
      es.close();
      setStreaming(false);
    };
  }, [jobId, terminal, refetch]);
  return { ...q, streaming };
}

/** Ticking clock for elapsed-time displays, corrected by the server's clock to avoid client skew. */
export function useNow(intervalMs = 1000, serverTime?: string) {
  const [offset, setOffset] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (serverTime) setOffset(new Date(serverTime).getTime() - Date.now());
  }, [serverTime]);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now + offset;
}
