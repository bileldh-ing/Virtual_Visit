"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Project } from "@/lib/schemas";
import { Button, Card, ErrorNote, Input, Select, Textarea } from "@/components/ui";
import { PageHeader, useSession } from "@/components/shell/AppShell";

const TYPES = ["apartment", "house", "office", "studio", "commercial", "other"];

export default function NewProject() {
  const { workspace } = useSession();
  const router = useRouter();
  const qc = useQueryClient();
  const [f, setF] = useState({ title: "", description: "", address_private: "", property_type: "apartment", rooms_estimate: "" });
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!f.title.trim()) {
      setError(new ApiError(422, { message: "Give the project a title.", fields: { title: "Required" } }));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const p = await api<Project>("/api/v1/projects", {
        method: "POST",
        body: { org_id: workspace.id, ...f, rooms_estimate: f.rooms_estimate ? Number(f.rooms_estimate) : null },
      });
      await qc.invalidateQueries({ queryKey: ["projects"] });
      router.push(`/app/projects/${p.id}?tab=photos`);
    } catch (err) {
      setError(err as ApiError);
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title="New project" subtitle="One project per property. You’ll upload photos next." />
      <Card className="p-6 sm:p-8">
        <form onSubmit={submit} className="space-y-5" noValidate>
          <ErrorNote error={error} />
          <Input label="Title" name="title" required placeholder="e.g. 14 Rue des Oliviers — 3-room flat" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} error={error?.fields?.title} />
          <div className="grid gap-5 sm:grid-cols-2">
            <Select label="Property type" value={f.property_type} onChange={(e) => setF({ ...f, property_type: e.target.value })}>
              {TYPES.map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
            </Select>
            <Input label="Rooms (estimate)" type="number" min={1} max={100} inputMode="numeric" value={f.rooms_estimate} onChange={(e) => setF({ ...f, rooms_estimate: e.target.value })} />
          </div>
          <Input label="Address (private)" hint="Never shown on share links or public tours." value={f.address_private} onChange={(e) => setF({ ...f, address_private: e.target.value })} />
          <Textarea label="Description" rows={4} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => router.back()}>Cancel</Button>
            <Button type="submit" loading={busy}>Create project</Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
