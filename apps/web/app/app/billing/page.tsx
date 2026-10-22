"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, Minus } from "lucide-react";
import { useState } from "react";
import { api, ApiError, formatBytes } from "@/lib/api";
import { Badge, Button, Card, ErrorNote, Meter, Spinner } from "@/components/ui";
import { PageHeader, useSession } from "@/components/shell/AppShell";

type Plan = { name: string; price_eur_month: number; active_projects: number; photo_storage_gb: number; reconstruction_minutes: number; concurrent_jobs: number; seats: number; share_links: number; embeds: boolean; password_links: boolean };
type Plans = { version: string; plans: Record<string, Plan>; usage_policy: string; mode: string };
type Usage = { plan: Plan & { code: string; status: string; provider: string; current_period_end: string | null }; period_start: string; usage: { reconstruction_minutes: number; photo_storage_bytes: number; active_projects: number }; ledger: { id: string; kind: string; quantity: number; unit: string; note: string; created_at: string }[]; policy: string };

export default function BillingPage() {
  const { workspace } = useSession();
  const qc = useQueryClient();
  const plans = useQuery<Plans, ApiError>({ queryKey: ["plans"], queryFn: () => api("/api/v1/billing/plans") });
  const usage = useQuery<Usage, ApiError>({ queryKey: ["usage", workspace.id], queryFn: () => api(`/api/v1/usage?org_id=${workspace.id}`) });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const isAdmin = workspace.role === "ADMIN" || workspace.role === "OWNER";

  const choose = async (code: string) => {
    setBusy(code);
    setError(null);
    try {
      const r = await api<{ mode: string; checkout_url?: string; note?: string }>("/api/v1/billing/checkout", { method: "POST", body: { org_id: workspace.id, plan_code: code } });
      if (r.checkout_url) window.location.assign(r.checkout_url);
      else {
        setNote(r.note ?? "Plan updated.");
        await qc.invalidateQueries({ queryKey: ["usage", workspace.id] });
      }
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(null);
    }
  };

  if (plans.isLoading || usage.isLoading) return <Spinner label="Loading billing" />;
  const u = usage.data;
  const current = u?.plan.code;
  return (
    <>
      <PageHeader title="Plan & usage" subtitle={<>Workspace {workspace.name}{plans.data?.mode === "test" && <Badge tone="amber" className="ml-2">test mode · no payments are taken</Badge>}</>} />
      <ErrorNote error={error ?? plans.error ?? usage.error} className="mb-4" />
      {note && <p role="status" className="mb-4 text-sm text-teal-300">{note}</p>}
      {u && (
        <Card className="mb-8 p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-display text-xl text-ivory-50">This month</h2>
            <span className="text-sm text-ink-300">Current plan: <strong className="text-ivory-50">{u.plan.name}</strong> · {u.plan.status}</span>
          </div>
          <div className="mt-5 grid gap-5 md:grid-cols-3">
            <Meter label="Reconstruction minutes" value={u.usage.reconstruction_minutes} max={u.plan.reconstruction_minutes} unit="min" />
            <Meter label="Photo storage" value={u.usage.photo_storage_bytes / 1024 ** 3} max={u.plan.photo_storage_gb} unit="GB" />
            <Meter label="Active projects" value={u.usage.active_projects} max={u.plan.active_projects} />
          </div>
          <p className="mt-4 text-xs text-ink-400">{u.policy}</p>
          {u.ledger.length > 0 && (
            <details className="mt-4">
              <summary className="cursor-pointer text-sm text-ink-300 hover:text-ivory-100">Usage ledger ({u.ledger.length})</summary>
              <ul className="mt-2 space-y-1 text-xs text-ink-300">
                {u.ledger.map((e) => <li key={e.id} className="flex justify-between gap-3"><span>{e.quantity} {e.unit} · {e.note || e.kind}</span><span>{new Date(e.created_at).toLocaleString()}</span></li>)}
              </ul>
            </details>
          )}
          <p className="mt-2 text-xs text-ink-400">Storage used: {formatBytes(u.usage.photo_storage_bytes)}</p>
        </Card>
      )}
      <div className="grid gap-5 md:grid-cols-3">
        {Object.entries(plans.data?.plans ?? {}).map(([code, p]) => (
          <Card key={code} className={clsx("flex flex-col p-6", code === current && "ring-2 ring-teal-400/60", code === "PRO" && "glass")}>
            <div className="flex items-center justify-between">
              <h3 className="font-display text-2xl text-ivory-50">{p.name}</h3>
              {code === current && <Badge tone="teal">current</Badge>}
            </div>
            <p className="mt-2"><span className="font-display text-4xl text-ivory-50">€{p.price_eur_month}</span><span className="text-ink-300"> / month</span></p>
            <ul className="mt-5 flex-1 space-y-2 text-sm">
              {[
                [`${p.active_projects} active projects`, true],
                [`${p.photo_storage_gb} GB photo storage`, true],
                [`${p.reconstruction_minutes} reconstruction min / month`, true],
                [`${p.concurrent_jobs} concurrent job${p.concurrent_jobs > 1 ? "s" : ""}`, true],
                [`${p.seats} seat${p.seats > 1 ? "s" : ""}`, true],
                [`${p.share_links} share links`, true],
                ["Website embeds", p.embeds],
                ["Password-protected links", p.password_links],
              ].map(([t, ok]) => (
                <li key={String(t)} className={clsx("flex items-center gap-2", ok ? "text-ivory-100" : "text-ink-400")}>
                  {ok ? <Check className="h-4 w-4 text-teal-300" aria-hidden /> : <Minus className="h-4 w-4" aria-hidden />}{t}
                </li>
              ))}
            </ul>
            {isAdmin && code !== current && <Button className="mt-6 w-full" variant={code === "PRO" ? "primary" : "glass"} loading={busy === code} onClick={() => choose(code)}>{p.price_eur_month > (u?.plan.price_eur_month ?? 0) ? "Upgrade" : "Switch"} to {p.name}</Button>}
          </Card>
        ))}
      </div>
      <p className="mt-6 text-xs text-ink-400">Plan definitions version {plans.data?.version}. Prices in EUR, VAT excluded.</p>
    </>
  );
}
