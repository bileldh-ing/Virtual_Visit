"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Eye, Globe2, Link2, Lock, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Project } from "@/lib/schemas";
import { Badge, Button, Card, EmptyState, ErrorNote, Input, Modal, Select } from "@/components/ui";
import { relTime } from "./ProjectCard";

type Link = {
  id: string;
  visibility: string;
  token_prefix: string;
  password_protected: boolean;
  allow_embed: boolean;
  allowed_origins: string[];
  expires_at: string | null;
  revoked_at: string | null;
  view_count: number;
  created_at: string;
  active: boolean;
  token?: string;
  url?: string;
  embed_html?: string | null;
};

export function CopyField({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <span className="mb-1.5 block text-sm font-medium text-ivory-100">{label}</span>
      <div className="flex gap-2">
        <input readOnly value={value} aria-label={label} onFocus={(e) => e.currentTarget.select()} className={`h-11 min-w-0 flex-1 rounded-xl border border-white/10 bg-ink-850 px-3 text-sm text-ivory-50 ${mono ? "font-mono text-xs" : ""}`} />
        <Button
          variant="glass"
          aria-label={`Copy ${label}`}
          onClick={async () => {
            await navigator.clipboard?.writeText(value).catch(() => undefined);
            setCopied(true);
            setTimeout(() => setCopied(false), 1600);
          }}
        >
          {copied ? <Check className="h-4 w-4 text-teal-300" /> : <Copy className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
}

export function ShareTab({ project, hasResult }: { project: Project; hasResult: boolean }) {
  const qc = useQueryClient();
  const links = useQuery<{ items: Link[] }, ApiError>({ queryKey: ["links", project.id], queryFn: () => api(`/api/v1/projects/${project.id}/share-links`) });
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<Link | null>(null);
  const [f, setF] = useState({ visibility: "UNLISTED", expires_in_days: "30", password: "", allow_embed: false, allowed_origins: "", publish_consent: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const canEdit = project.role !== "VIEWER";

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const link = await api<Link>(`/api/v1/projects/${project.id}/share-links`, {
        method: "POST",
        body: {
          visibility: f.visibility,
          expires_in_days: f.expires_in_days === "never" ? null : Number(f.expires_in_days),
          password: f.password || null,
          allow_embed: f.allow_embed,
          allowed_origins: f.allowed_origins.split(/[\s,]+/).filter(Boolean),
          publish_consent: f.publish_consent,
        },
      });
      setCreated(link);
      await qc.invalidateQueries({ queryKey: ["links", project.id] });
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (l: Link) => {
    if (!confirm("Revoke this link? Anyone holding it immediately loses access, including embeds.")) return;
    try {
      await api(`/api/v1/share-links/${l.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["links", project.id] });
    } catch (e) {
      setError(e as ApiError);
    }
  };

  const items = links.data?.items ?? [];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl text-ivory-50">Share links</h2>
          <p className="text-sm text-ink-300">Links show the latest successful reconstruction. Originals with EXIF and the private address are never shared.</p>
        </div>
        {canEdit && <Button onClick={() => { setOpen(true); setCreated(null); setError(null); }} disabled={!hasResult}><Link2 className="h-4 w-4" />Create link</Button>}
      </div>
      {!hasResult && <p className="text-sm text-amber-400">Finish a successful reconstruction before sharing.</p>}
      <ErrorNote error={!open ? error : null} />
      {items.length === 0 && hasResult && !links.isLoading && <EmptyState icon={<Globe2 className="h-10 w-10" />} title="No links yet">Create an unlisted link for buyers, or a restricted link for your team.</EmptyState>}
      <ul className="space-y-2">
        {items.map((l) => (
          <li key={l.id} className="surface flex flex-wrap items-center gap-4 rounded-2xl px-5 py-4">
            {l.visibility === "RESTRICTED" ? <ShieldCheck className="h-5 w-5 text-[#9fb0ff]" aria-hidden /> : <Globe2 className="h-5 w-5 text-teal-300" aria-hidden />}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-ivory-100">/tour/{l.token_prefix}…</span>
                <Badge tone={l.active ? "teal" : "neutral"}>{l.active ? "active" : l.revoked_at ? "revoked" : "expired"}</Badge>
                <Badge>{l.visibility.toLowerCase()}</Badge>
                {l.password_protected && <Badge icon={<Lock className="h-3 w-3" />}>password</Badge>}
                {l.allow_embed && <Badge tone="blue">embed</Badge>}
              </div>
              <p className="mt-1 text-xs text-ink-300">
                <Eye className="mr-1 inline h-3.5 w-3.5" aria-hidden />{l.view_count} views · created {relTime(l.created_at)}
                {l.expires_at && ` · expires ${new Date(l.expires_at).toLocaleDateString()}`}
              </p>
            </div>
            {canEdit && l.active && <Button variant="ghost" size="sm" onClick={() => revoke(l)} aria-label="Revoke link"><Trash2 className="h-4 w-4 text-rose-400" />Revoke</Button>}
          </li>
        ))}
      </ul>

      <Modal open={open} onOpenChange={setOpen} title={created ? "Link created" : "Share this tour"} description={created ? "Copy it now: the full link is shown only once." : "Choose who can open it and for how long."} wide>
        {created ? (
          <div className="space-y-4">
            {created.url && <CopyField label="Tour link" value={created.url} />}
            {created.embed_html && <CopyField label="Embed code" value={created.embed_html} mono />}
            {created.url && <a href={created.url.replace(/^https?:\/\/[^/]+/, "")} target="_blank" rel="noreferrer" className="inline-block text-sm text-teal-300 hover:underline">Open the public page ↗</a>}
            <div className="flex justify-end"><Button onClick={() => setOpen(false)}>Done</Button></div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Select label="Who can open it" value={f.visibility} onChange={(e) => setF({ ...f, visibility: e.target.value })}>
                <option value="UNLISTED">Anyone with the link</option>
                <option value="RESTRICTED">Workspace members only</option>
              </Select>
              <Select label="Expires" value={f.expires_in_days} onChange={(e) => setF({ ...f, expires_in_days: e.target.value })}>
                <option value="7">In 7 days</option>
                <option value="30">In 30 days</option>
                <option value="90">In 90 days</option>
                <option value="365">In 1 year</option>
                <option value="never">Never</option>
              </Select>
            </div>
            <Input label="Password (optional, Pro)" type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} hint="At least 6 characters. Viewers enter it once per hour." />
            <label className="flex items-start gap-3 text-sm text-ivory-100">
              <input type="checkbox" className="mt-1 accent-teal-400" checked={f.allow_embed} onChange={(e) => setF({ ...f, allow_embed: e.target.checked })} />
              <span>Allow embedding on websites (Pro)<span className="block text-xs text-ink-300">Restrict to specific origins below, or leave empty to allow any site.</span></span>
            </label>
            {f.allow_embed && <Input label="Allowed origins" placeholder="https://listing.example.com" value={f.allowed_origins} onChange={(e) => setF({ ...f, allowed_origins: e.target.value })} />}
            {f.visibility === "UNLISTED" && (
              <label className="flex items-start gap-3 rounded-xl bg-white/[0.04] p-3 text-sm text-ivory-100">
                <input type="checkbox" className="mt-1 accent-teal-400" checked={f.publish_consent} onChange={(e) => setF({ ...f, publish_consent: e.target.checked })} />
                <span>I have the right to publish these photos, and they show no people, private documents, keys or security details.</span>
              </label>
            )}
            <ErrorNote error={error} />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button onClick={create} loading={busy}>Create link</Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

