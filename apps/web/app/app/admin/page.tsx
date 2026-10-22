"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Activity, Database, Flag, RefreshCw, Users } from "lucide-react";
import { useState } from "react";
import { api, ApiError, formatBytes, formatDuration } from "@/lib/api";
import { Badge, Button, Card, ErrorNote, stateTone } from "@/components/ui";
import { PageHeader, useSession } from "@/components/shell/AppShell";
import { Metric } from "@/components/project/OverviewTab";

type Overview = { jobs_by_state: Record<string, number>; oldest_queued_age_s: number | null; stale_leases: number; queue: Record<string, unknown>; photo_storage_bytes: number; users: number; projects: number; open_privacy_reports: number; pending_deletions: number };
type AdminJob = { id: string; project_id: string; mode: string; state: string; attempts: number; error_category: string | null; error_message: string | null; inputs: number; created_at: string; duration_s: number | null };
type AdminUser = { id: string; email: string; status: string; verified: boolean; is_platform_admin: boolean; created_at: string };
type Report = { id: string; project_id: string | null; reason: string; details: string; status: string; created_at: string };
type AuditEv = { id: string; action: string; target_type: string; created_at: string };

const TABS = ["jobs", "users", "reports", "audit"] as const;

export default function AdminPage() {
  const { me } = useSession();
  const qc = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]>("jobs");
  const [state, setState] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const ov = useQuery<Overview, ApiError>({ queryKey: ["admin-ov"], queryFn: () => api("/api/v1/admin/overview"), refetchInterval: 10000, enabled: me.user.is_platform_admin });
  const jobs = useQuery<{ total: number; items: AdminJob[] }, ApiError>({ queryKey: ["admin-jobs", state], queryFn: () => api(`/api/v1/admin/jobs?state=${state}&limit=50`), enabled: tab === "jobs" && me.user.is_platform_admin });
  const users = useQuery<{ items: AdminUser[] }, ApiError>({ queryKey: ["admin-users"], queryFn: () => api("/api/v1/admin/users"), enabled: tab === "users" });
  const reports = useQuery<{ items: Report[] }, ApiError>({ queryKey: ["admin-reports"], queryFn: () => api("/api/v1/admin/privacy-reports"), enabled: tab === "reports" });
  const audit = useQuery<{ items: AuditEv[] }, ApiError>({ queryKey: ["admin-audit"], queryFn: () => api("/api/v1/admin/audit?limit=100"), enabled: tab === "audit" });

  if (!me.user.is_platform_admin) return <ErrorNote error={{ message: "Platform administrators only." }} />;

  const act = async (path: string, body?: unknown) => {
    setError(null);
    try {
      await api(path, { method: body === undefined ? "POST" : "PATCH", body });
      await qc.invalidateQueries({ queryKey: ["admin-jobs"] });
      await qc.invalidateQueries({ queryKey: ["admin-users"] });
      await qc.invalidateQueries({ queryKey: ["admin-ov"] });
    } catch (e) {
      setError(e as ApiError);
    }
  };
  const o = ov.data;
  return (
    <>
      <PageHeader title="Operations" subtitle="Redacted platform view: no photos, addresses or artifact links." actions={<Button variant="glass" onClick={() => act("/api/v1/admin/reconcile")}><RefreshCw className="h-4 w-4" />Run reconciler</Button>} />
      <ErrorNote error={error ?? ov.error} className="mb-4" />
      {o && (
        <dl className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Metric label="Users" value={o.users} />
          <Metric label="Projects" value={o.projects} />
          <Metric label="Storage" value={formatBytes(o.photo_storage_bytes)} />
          <Metric label="Queued age" value={o.oldest_queued_age_s != null ? formatDuration(o.oldest_queued_age_s) : "—"} hint="oldest queued job" />
          <Metric label="Stale leases" value={o.stale_leases} hint="crashed workers" />
          <Metric label="Privacy reports" value={o.open_privacy_reports} hint={`${o.pending_deletions} pending deletions`} />
        </dl>
      )}
      {o && (
        <div className="mb-6 flex flex-wrap gap-2">
          {Object.entries(o.jobs_by_state).map(([k, v]) => <Badge key={k} tone={stateTone(k)}>{k.toLowerCase()} · {v}</Badge>)}
        </div>
      )}
      <div className="mb-4 flex gap-1 border-b border-white/8" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={clsx("relative px-3.5 py-2.5 text-sm capitalize", tab === t ? "text-ivory-50" : "text-ink-300 hover:text-ivory-100")}>
            {t === "jobs" ? <Activity className="mr-1.5 inline h-4 w-4" /> : t === "users" ? <Users className="mr-1.5 inline h-4 w-4" /> : t === "reports" ? <Flag className="mr-1.5 inline h-4 w-4" /> : <Database className="mr-1.5 inline h-4 w-4" />}
            {t}
            {tab === t && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-teal-400" />}
          </button>
        ))}
      </div>
      <Card className="overflow-x-auto p-0">
        {tab === "jobs" && (
          <>
            <div className="flex items-center gap-2 p-4">
              <label htmlFor="st" className="text-sm text-ink-300">State</label>
              <select id="st" value={state} onChange={(e) => setState(e.target.value)} className="h-9 rounded-lg border border-white/10 bg-ink-850 px-2 text-sm">
                <option value="">All</option>
                {["QUEUED", "INPUT_VALIDATION", "CAMERA_POSE_ESTIMATION", "SPARSE_RECONSTRUCTION", "SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"].map((s) => <option key={s}>{s}</option>)}
              </select>
              <span className="ml-auto text-xs text-ink-400">{jobs.data?.total ?? 0} jobs</span>
            </div>
            <table className="w-full text-left text-sm">
              <thead className="border-y border-white/5 text-xs uppercase tracking-wider text-ink-400"><tr><th className="px-4 py-2">Job</th><th className="px-4 py-2">State</th><th className="px-4 py-2">Mode</th><th className="px-4 py-2">Inputs</th><th className="px-4 py-2">Attempts</th><th className="px-4 py-2">Duration</th><th className="px-4 py-2">Error</th><th className="px-4 py-2" /></tr></thead>
              <tbody className="divide-y divide-white/5">
                {jobs.data?.items.map((j) => (
                  <tr key={j.id}>
                    <td className="px-4 py-2.5 font-mono text-xs">{j.id.slice(0, 8)}</td>
                    <td className="px-4 py-2.5"><Badge tone={stateTone(j.state)}>{j.state.toLowerCase()}</Badge></td>
                    <td className="px-4 py-2.5 text-ink-300">{j.mode.toLowerCase()}</td>
                    <td className="px-4 py-2.5 tabular-nums">{j.inputs}</td>
                    <td className="px-4 py-2.5 tabular-nums">{j.attempts}</td>
                    <td className="px-4 py-2.5 tabular-nums">{j.duration_s != null ? formatDuration(j.duration_s) : "—"}</td>
                    <td className="max-w-[220px] truncate px-4 py-2.5 text-xs text-rose-400" title={j.error_message ?? ""}>{j.error_category ?? ""}</td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-right">
                      {["SUCCEEDED", "PARTIAL_SUCCESS"].includes(j.state) && <Button size="sm" variant="ghost" onClick={() => act(`/api/v1/admin/projects/${j.project_id}/sample`, { is_public_sample: true })}>Set as landing sample</Button>}
                      {["FAILED", "CANCELLED"].includes(j.state) && <Button size="sm" variant="ghost" onClick={() => act(`/api/v1/admin/jobs/${j.id}/retry`)}>Retry</Button>}
                      {!["SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"].includes(j.state) && <Button size="sm" variant="ghost" onClick={() => act(`/api/v1/admin/jobs/${j.id}/cancel`)}>Cancel</Button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {tab === "users" && (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-white/5 text-xs uppercase tracking-wider text-ink-400"><tr><th className="px-4 py-2">Email</th><th className="px-4 py-2">Status</th><th className="px-4 py-2">Verified</th><th className="px-4 py-2">Joined</th><th className="px-4 py-2" /></tr></thead>
            <tbody className="divide-y divide-white/5">
              {users.data?.items.map((u) => (
                <tr key={u.id}>
                  <td className="px-4 py-2.5">{u.email} {u.is_platform_admin && <Badge tone="blue">admin</Badge>}</td>
                  <td className="px-4 py-2.5"><Badge tone={u.status === "active" ? "teal" : "rose"}>{u.status}</Badge></td>
                  <td className="px-4 py-2.5">{u.verified ? "yes" : "no"}</td>
                  <td className="px-4 py-2.5 text-ink-300">{new Date(u.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-2.5 text-right">{u.id !== me.user.id && <Button size="sm" variant="ghost" onClick={() => act(`/api/v1/admin/users/${u.id}`, { status: u.status === "active" ? "disabled" : "active" })}>{u.status === "active" ? "Disable" : "Enable"}</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "reports" && (
          <ul className="divide-y divide-white/5">
            {reports.data?.items.length === 0 && <li className="p-6 text-sm text-ink-300">No privacy reports.</li>}
            {reports.data?.items.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-3 p-4 text-sm">
                <Badge tone={r.status === "open" ? "amber" : "neutral"}>{r.status}</Badge>
                <span className="text-ivory-100">{r.reason}</span>
                <span className="flex-1 truncate text-ink-300">{r.details}</span>
                <span className="text-xs text-ink-400">{new Date(r.created_at).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        )}
        {tab === "audit" && (
          <ul className="divide-y divide-white/5">
            {audit.data?.items.map((e) => (
              <li key={e.id} className="flex justify-between gap-3 px-4 py-2 text-sm"><span className="font-mono text-xs text-ivory-100">{e.action}</span><span className="text-xs text-ink-400">{e.target_type} · {new Date(e.created_at).toLocaleString()}</span></li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
