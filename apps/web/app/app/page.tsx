"use client";

import { useQuery } from "@tanstack/react-query";
import { FolderPlus, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useDeferredValue, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Project } from "@/lib/schemas";
import { Button, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { PageHeader, useSession } from "@/components/shell/AppShell";
import { ProjectCard } from "@/components/project/ProjectCard";

export default function Dashboard() {
  const { workspace, me } = useSession();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("updated");
  const dq = useDeferredValue(q);
  const projects = useQuery<{ total: number; items: Project[] }, ApiError>({
    queryKey: ["projects", workspace.id, dq, status, sort],
    queryFn: () => api(`/api/v1/projects?${new URLSearchParams({ org_id: workspace.id, q: dq, status, sort, limit: "48" })}`),
  });
  const canEdit = workspace.role !== "VIEWER";
  const name = me.user.display_name?.split(" ")[0];

  return (
    <>
      <PageHeader
        title={name ? `Hello, ${name}` : "Projects"}
        subtitle={<>{workspace.name} · {projects.data ? `${projects.data.total} project${projects.data.total === 1 ? "" : "s"}` : "…"}</>}
        actions={canEdit && <Link href="/app/projects/new"><Button size="lg"><Plus className="h-4 w-4" />New project</Button></Link>}
      />
      <div className="mb-6 flex flex-col gap-3 sm:flex-row">
        <label className="relative flex-1">
          <span className="sr-only">Search projects</span>
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" aria-hidden />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by title or description" className="h-11 w-full rounded-xl border border-white/10 bg-ink-850 pl-10 pr-3 text-[15px] placeholder:text-ink-400 focus:border-teal-400 focus:outline-none" />
        </label>
        <select aria-label="Listing status" value={status} onChange={(e) => setStatus(e.target.value)} className="h-11 rounded-xl border border-white/10 bg-ink-850 px-3 text-sm">
          <option value="">All statuses</option>
          <option value="draft">Draft</option>
          <option value="active">Active listing</option>
          <option value="archived">Archived</option>
        </select>
        <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value)} className="h-11 rounded-xl border border-white/10 bg-ink-850 px-3 text-sm">
          <option value="updated">Recently updated</option>
          <option value="created">Newest</option>
          <option value="title">Title A–Z</option>
        </select>
      </div>
      {projects.isLoading && <Spinner label="Loading projects" />}
      <ErrorNote error={projects.error} />
      {projects.data && projects.data.items.length === 0 && (
        <EmptyState
          icon={<FolderPlus className="h-10 w-10" />}
          title={q || status ? "No matching projects" : "Start your first 3D tour"}
          action={canEdit && !q && <Link href="/app/projects/new"><Button>Create a project</Button></Link>}
        >
          {q || status ? "Try a different search." : "Upload 20–200 overlapping photos of a room or home and RoomScape reconstructs the real camera positions and 3D points."}
        </EmptyState>
      )}
      {projects.data && projects.data.items.length > 0 && (
        <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data.items.map((p, i) => <ProjectCard key={p.id} p={p} i={i} />)}
        </ul>
      )}
    </>
  );
}
