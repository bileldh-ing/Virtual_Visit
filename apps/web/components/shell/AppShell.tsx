"use client";

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bell, CreditCard, FolderKanban, LogOut, Menu, Shield, Users, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useMe, useWorkspace } from "@/lib/hooks";
import type { Me } from "@/lib/schemas";
import { Badge, Spinner } from "@/components/ui";
import { Logo } from "./Logo";

type Ctx = { me: Me; workspace: Me["workspaces"][number]; setWorkspace: (id: string) => void };
const SessionCtx = createContext<Ctx | null>(null);

export function useSession(): Ctx {
  const c = useContext(SessionCtx);
  if (!c) throw new Error("useSession outside AppShell");
  return c;
}

const NAV = [
  { href: "/app", label: "Projects", icon: FolderKanban, exact: true },
  { href: "/app/team", label: "Team", icon: Users },
  { href: "/app/billing", label: "Billing", icon: CreditCard },
];

export function AppShell({ children }: { children: ReactNode }) {
  const me = useMe();
  const { workspace, setWorkspace } = useWorkspace(me.data);
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (me.error?.status === 401) router.replace(`/signin?next=${encodeURIComponent(pathname)}`);
  }, [me.error, router, pathname]);
  useEffect(() => setOpen(false), [pathname]);

  if (me.isLoading || me.error?.status === 401) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner label="Loading your workspace" />
      </div>
    );
  }
  if (me.error || !me.data || !workspace) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-6 text-center text-ink-300" role="alert">
        Could not reach RoomScape. {me.error?.message}
      </div>
    );
  }

  const signOut = async () => {
    await api("/api/v1/auth/signout", { method: "POST" }).catch(() => undefined);
    qc.clear();
    router.replace("/signin");
  };

  const nav = me.data.user.is_platform_admin ? [...NAV, { href: "/app/admin", label: "Admin", icon: Shield }] : NAV;
  const isActive = (href: string, exact?: boolean) => (exact ? pathname === href || pathname.startsWith("/app/projects") : pathname.startsWith(href));

  return (
    <SessionCtx.Provider value={{ me: me.data, workspace, setWorkspace }}>
      <header className="glass sticky top-0 z-40 border-x-0 border-t-0">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:px-6">
          <Logo href="/app" />
          <nav className="ml-6 hidden items-center gap-1 md:flex" aria-label="Main">
            {nav.map((n) => (
              <Link key={n.href} href={n.href} aria-current={isActive(n.href, n.exact) ? "page" : undefined} className={clsx("inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm transition", isActive(n.href, n.exact) ? "bg-white/10 text-ivory-50" : "text-ink-300 hover:bg-white/5 hover:text-ivory-100")}>
                <n.icon className="h-4 w-4" aria-hidden />
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <label className="sr-only" htmlFor="ws">Workspace</label>
            <select id="ws" value={workspace.id} onChange={(e) => setWorkspace(e.target.value)} className="hidden h-9 max-w-[220px] truncate rounded-xl border border-white/10 bg-ink-850 px-2.5 text-sm text-ivory-100 sm:block">
              {me.data.workspaces.map((w) => (
                <option key={w.id} value={w.id}>{w.name} · {w.role.toLowerCase()}</option>
              ))}
            </select>
            <span className="relative hidden sm:inline-flex" aria-label={`${me.data.unread_notifications} unread notifications`}>
              <Bell className="h-5 w-5 text-ink-300" aria-hidden />
              {me.data.unread_notifications > 0 && <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-teal-400" />}
            </span>
            <div className="hidden items-center gap-2 pl-2 lg:flex">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-teal-400/20 text-sm font-semibold text-teal-300" aria-hidden>
                {(me.data.user.display_name || me.data.user.email)[0]?.toUpperCase()}
              </span>
              <span className="max-w-[160px] truncate text-sm text-ivory-100">{me.data.user.display_name || me.data.user.email}</span>
            </div>
            <button onClick={signOut} className="hidden rounded-xl p-2 text-ink-300 hover:bg-white/10 hover:text-ivory-50 md:inline-flex" aria-label="Sign out"><LogOut className="h-4 w-4" /></button>
            <button className="rounded-xl p-2 text-ivory-100 md:hidden" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>
        </div>
        {open && (
          <div className="border-t border-white/8 px-4 pb-4 md:hidden">
            <nav className="flex flex-col gap-1 pt-3" aria-label="Mobile">
              {nav.map((n) => (
                <Link key={n.href} href={n.href} className="flex items-center gap-2 rounded-xl px-3 py-2.5 text-ivory-100 hover:bg-white/5"><n.icon className="h-4 w-4" />{n.label}</Link>
              ))}
              <select value={workspace.id} onChange={(e) => setWorkspace(e.target.value)} aria-label="Workspace" className="mt-2 h-10 rounded-xl border border-white/10 bg-ink-850 px-2.5 text-sm">
                {me.data.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
              <button onClick={signOut} className="mt-2 flex items-center gap-2 rounded-xl px-3 py-2.5 text-left text-ink-300 hover:bg-white/5"><LogOut className="h-4 w-4" />Sign out</button>
            </nav>
          </div>
        )}
      </header>
      {!me.data.user.email_verified && (
        <div className="border-b border-amber-400/20 bg-amber-400/10 px-4 py-2 text-center text-sm text-amber-400">
          Verify your email to publish share links. <Link href="/verify-email" className="underline underline-offset-2">Resend or enter a link</Link>
        </div>
      )}
      <main id="main" className="mx-auto max-w-7xl px-4 py-8 sm:px-6">{children}</main>
    </SessionCtx.Provider>
  );
}

export function PageHeader({ title, subtitle, actions, badge }: { title: string; subtitle?: ReactNode; actions?: ReactNode; badge?: string }) {
  return (
    <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="font-display text-3xl text-ivory-50 sm:text-4xl">
          {title} {badge && <Badge className="ml-2 align-middle">{badge}</Badge>}
        </h1>
        {subtitle && <div className="mt-2 text-ink-300">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
