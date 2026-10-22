"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useProject } from "@/lib/hooks";
import type { Artifact, Job, Project, Tour } from "@/lib/schemas";
import { Badge, Button, Card, ErrorNote, Input, Select, Spinner, Textarea } from "@/components/ui";
import { OverviewTab } from "@/components/project/OverviewTab";
import { PhotosTab } from "@/components/project/PhotosTab";
import { JobsTab } from "@/components/project/JobsTab";
import { ShareTab } from "@/components/project/ShareTab";

const TABS = [
  ["overview", "Overview"],
  ["photos", "Photos & capture check"],
  ["jobs", "Reconstructions"],
  ["share", "Share"],
  ["settings", "Settings"],
] as const;

function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const sp = useSearchParams();
  const tab = (sp.get("tab") ?? "overview") as (typeof TABS)[number][0];
  const project = useProject(id);
  const arts = useQuery<{ job: Job | null; artifacts: Artifact[] }, ApiError>({ queryKey: ["artifacts", id], queryFn: () => api(`/api/v1/projects/${id}/artifacts`) });
  const tour = useQuery<Tour, ApiError>({ queryKey: ["tour", id], queryFn: () => api(`/api/v1/projects/${id}/tour`) });

  if (project.isLoading) return <Spinner label="Loading project" />;
  if (project.error || !project.data) return <ErrorNote error={project.error ?? { message: "Project not found." }} />;
  const p = project.data;

  return (
    <>
      <Link href="/app" className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-300 hover:text-ivory-50"><ArrowLeft className="h-4 w-4" />All projects</Link>
      <div className="mb-6 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-3xl text-ivory-50 sm:text-4xl">{p.title}</h1>
          <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-ink-300">
            <span className="capitalize">{p.property_type}</span>·<span>{p.photo_count} photos</span>·<Badge>{p.listing_status}</Badge>
            {p.role && <Badge tone="blue">{p.role.toLowerCase()}</Badge>}
          </p>
        </div>
      </div>
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b border-white/8 scrollbar-thin" aria-label="Project sections">
        {TABS.map(([k, label]) => (
          <Link key={k} href={`/app/projects/${id}?tab=${k}`} scroll={false} aria-current={tab === k ? "page" : undefined} className={clsx("relative whitespace-nowrap px-3.5 py-3 text-sm transition", tab === k ? "text-ivory-50" : "text-ink-300 hover:text-ivory-100")}>
            {label}
            {tab === k && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-teal-400" />}
          </Link>
        ))}
      </nav>
      {tab === "overview" && (arts.isLoading ? <Spinner /> : <OverviewTab project={p} job={arts.data?.job ?? null} artifacts={arts.data?.artifacts ?? []} tour={tour.data ?? null} />)}
      {tab === "photos" && <PhotosTab project={p} />}
      {tab === "jobs" && <JobsTab project={p} />}
      {tab === "share" && <ShareTab project={p} hasResult={Boolean(arts.data?.job)} />}
      {tab === "settings" && <SettingsTab project={p} />}
    </>
  );
}

function SettingsTab({ project }: { project: Project }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [f, setF] = useState({ title: project.title, description: project.description, address_private: project.address_private ?? "", listing_status: project.listing_status });
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const canEdit = project.role !== "VIEWER";
  const canDelete = project.role === "ADMIN" || project.role === "OWNER";

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api(`/api/v1/projects/${project.id}`, { method: "PATCH", body: { version: project.version, ...f } });
      await qc.invalidateQueries({ queryKey: ["project", project.id] });
      setSaved(true);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setBusy(false);
    }
  };
  const del = async () => {
    try {
      await api(`/api/v1/projects/${project.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["projects"] });
      router.replace("/app");
    } catch (err) {
      setError(err as ApiError);
    }
  };
  return (
    <div className="grid max-w-3xl gap-6">
      <Card className="p-6">
        <form onSubmit={save} className="space-y-4">
          <h2 className="font-display text-xl text-ivory-50">Details</h2>
          <ErrorNote error={error} />
          <Input label="Title" value={f.title} disabled={!canEdit} onChange={(e) => setF({ ...f, title: e.target.value })} />
          <Textarea label="Description" rows={3} value={f.description} disabled={!canEdit} onChange={(e) => setF({ ...f, description: e.target.value })} />
          <Input label="Address (private)" value={f.address_private} disabled={!canEdit} onChange={(e) => setF({ ...f, address_private: e.target.value })} hint="Never included in share links." />
          <Select label="Listing status" value={f.listing_status} disabled={!canEdit} onChange={(e) => setF({ ...f, listing_status: e.target.value })}>
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="archived">Archived</option>
          </Select>
          {canEdit && <div className="flex items-center justify-end gap-3">{saved && <span role="status" className="text-sm text-teal-300">Saved</span>}<Button type="submit" loading={busy}>Save changes</Button></div>}
        </form>
      </Card>
      {canDelete && (
        <Card className="border-rose-400/20 p-6">
          <h2 className="font-display text-xl text-rose-400">Delete project</h2>
          <p className="mt-1 text-sm text-ink-300">Revokes every share link, cancels running jobs and permanently deletes photos and artifacts from storage. Deletion is verified and cannot be undone.</p>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1"><Input label={`Type "${project.title}" to confirm`} value={confirmText} onChange={(e) => setConfirmText(e.target.value)} /></div>
            <Button variant="danger" disabled={confirmText !== project.title} onClick={del}>Delete permanently</Button>
          </div>
        </Card>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense><ProjectPage /></Suspense>;
}
