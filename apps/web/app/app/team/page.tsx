"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MailPlus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Badge, Button, Card, ErrorNote, Input, Select, Spinner } from "@/components/ui";
import { PageHeader, useSession } from "@/components/shell/AppShell";

type Org = { id: string; name: string; role: string; retention_days: number; default_share_visibility: string; plan: string; subscription_status: string };
type Members = { members: { id: string; user_id: string; email: string; display_name: string; role: string; joined_at: string }[]; invitations: { id: string; email: string; role: string; expires_at: string }[] };
type AuditEv = { id: string; action: string; created_at: string; target_type: string };

export default function TeamPage() {
  const { workspace, me } = useSession();
  const qc = useQueryClient();
  const org = useQuery<Org, ApiError>({ queryKey: ["org", workspace.id], queryFn: () => api(`/api/v1/orgs/${workspace.id}`) });
  const members = useQuery<Members, ApiError>({ queryKey: ["members", workspace.id], queryFn: () => api(`/api/v1/orgs/${workspace.id}/members`) });
  const isAdmin = workspace.role === "ADMIN" || workspace.role === "OWNER";
  const audit = useQuery<{ events: AuditEv[] }, ApiError>({ queryKey: ["org-audit", workspace.id], queryFn: () => api(`/api/v1/orgs/${workspace.id}/audit`), enabled: isAdmin });
  const [inv, setInv] = useState({ email: "", role: "EDITOR" });
  const [settings, setSettings] = useState<{ name: string; retention_days: number; default_share_visibility: string } | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const s = settings ?? (org.data ? { name: org.data.name, retention_days: org.data.retention_days, default_share_visibility: org.data.default_share_visibility } : null);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      await Promise.all([qc.invalidateQueries({ queryKey: ["members", workspace.id] }), qc.invalidateQueries({ queryKey: ["org", workspace.id] }), qc.invalidateQueries({ queryKey: ["me"] })]);
    } catch (e) {
      setError(e as ApiError);
    }
  };

  return (
    <>
      <PageHeader title="Workspace & team" subtitle={`${workspace.name} · your role: ${workspace.role.toLowerCase()}`} />
      <ErrorNote error={error ?? org.error ?? members.error} className="mb-4" />
      {msg && <p role="status" className="mb-4 text-sm text-teal-300">{msg}</p>}
      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div className="space-y-6">
          <Card className="p-6">
            <h2 className="font-display text-xl text-ivory-50">Members</h2>
            {members.isLoading && <Spinner />}
            <ul className="mt-4 divide-y divide-white/5">
              {members.data?.members.map((m) => (
                <li key={m.id} className="flex flex-wrap items-center gap-3 py-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-teal-400/15 text-sm font-semibold text-teal-300" aria-hidden>{(m.display_name || m.email)[0]?.toUpperCase()}</span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-ivory-100">{m.display_name || m.email} {m.user_id === me.user.id && <span className="text-ink-400">(you)</span>}</p>
                    <p className="truncate text-xs text-ink-400">{m.email}</p>
                  </div>
                  {isAdmin && m.user_id !== me.user.id ? (
                    <select aria-label={`Role for ${m.email}`} value={m.role} onChange={(e) => run(() => api(`/api/v1/orgs/${workspace.id}/members/${m.id}`, { method: "PATCH", body: { role: e.target.value } }), "Role updated.")} className="h-9 rounded-lg border border-white/10 bg-ink-850 px-2 text-sm">
                      {["OWNER", "ADMIN", "EDITOR", "VIEWER"].map((r) => <option key={r} value={r}>{r.toLowerCase()}</option>)}
                    </select>
                  ) : (
                    <Badge>{m.role.toLowerCase()}</Badge>
                  )}
                  {isAdmin && m.user_id !== me.user.id && (
                    <button aria-label={`Remove ${m.email}`} onClick={() => confirm(`Remove ${m.email} from the workspace?`) && run(() => api(`/api/v1/orgs/${workspace.id}/members/${m.id}`, { method: "DELETE" }), "Member removed.")} className="rounded-lg p-2 text-ink-400 hover:bg-white/10 hover:text-rose-400"><Trash2 className="h-4 w-4" /></button>
                  )}
                </li>
              ))}
              {members.data?.invitations.map((i) => (
                <li key={i.id} className="flex items-center gap-3 py-3 opacity-80">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full border border-dashed border-white/20 text-ink-400" aria-hidden><MailPlus className="h-4 w-4" /></span>
                  <div className="flex-1"><p className="text-sm text-ivory-100">{i.email}</p><p className="text-xs text-ink-400">Invitation pending · expires {new Date(i.expires_at).toLocaleDateString()}</p></div>
                  <Badge tone="amber">{i.role.toLowerCase()}</Badge>
                </li>
              ))}
            </ul>
            {isAdmin && (
              <form className="mt-5 flex flex-col gap-3 border-t border-white/5 pt-5 sm:flex-row sm:items-end" onSubmit={(e) => { e.preventDefault(); void run(() => api(`/api/v1/orgs/${workspace.id}/invitations`, { method: "POST", body: inv }), `Invitation sent to ${inv.email}.`).then(() => setInv({ ...inv, email: "" })); }}>
                <div className="flex-1"><Input label="Invite by email" type="email" value={inv.email} onChange={(e) => setInv({ ...inv, email: e.target.value })} placeholder="colleague@agency.com" /></div>
                <div className="sm:w-36"><Select label="Role" value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}><option value="ADMIN">Admin</option><option value="EDITOR">Editor</option><option value="VIEWER">Viewer</option></Select></div>
                <Button type="submit" disabled={!inv.email}><MailPlus className="h-4 w-4" />Invite</Button>
              </form>
            )}
          </Card>
          {isAdmin && (
            <Card className="p-6">
              <h2 className="font-display text-xl text-ivory-50">Recent activity</h2>
              <ul className="mt-3 space-y-1.5 text-sm">
                {audit.data?.events.slice(0, 12).map((e) => (
                  <li key={e.id} className="flex justify-between gap-3"><span className="font-mono text-xs text-ivory-100">{e.action}</span><span className="text-xs text-ink-400">{new Date(e.created_at).toLocaleString()}</span></li>
                ))}
              </ul>
            </Card>
          )}
        </div>
        <Card className="h-fit p-6">
          <h2 className="font-display text-xl text-ivory-50">Settings</h2>
          {s && (
            <form className="mt-4 space-y-4" onSubmit={(e) => { e.preventDefault(); void run(() => api(`/api/v1/orgs/${workspace.id}`, { method: "PATCH", body: s }), "Workspace saved."); }}>
              <Input label="Workspace name" value={s.name} disabled={!isAdmin} onChange={(e) => setSettings({ ...s, name: e.target.value })} />
              <Input label="Photo retention (days)" type="number" min={7} max={3650} value={s.retention_days} disabled={!isAdmin} onChange={(e) => setSettings({ ...s, retention_days: Number(e.target.value) })} hint="Originals are deleted after this period; artifacts remain." />
              <Select label="Default link visibility" value={s.default_share_visibility} disabled={!isAdmin} onChange={(e) => setSettings({ ...s, default_share_visibility: e.target.value })}>
                <option value="UNLISTED">Anyone with the link</option>
                <option value="RESTRICTED">Workspace members only</option>
              </Select>
              {isAdmin && <Button type="submit" className="w-full">Save settings</Button>}
            </form>
          )}
        </Card>
      </div>
    </>
  );
}
