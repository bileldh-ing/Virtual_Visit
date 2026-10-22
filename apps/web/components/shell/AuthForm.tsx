"use client";

import type { FormEvent, ReactNode } from "react";
import { ErrorNote } from "@/components/ui";

export function AuthCard({ title, subtitle, children, footer }: { title: string; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div>
      <h1 className="font-display text-3xl text-ivory-50">{title}</h1>
      {subtitle && <p className="mt-2 text-ink-300">{subtitle}</p>}
      <div className="glass mt-8 rounded-3xl p-6 sm:p-7">{children}</div>
      {footer && <div className="mt-6 text-center text-sm text-ink-300">{footer}</div>}
    </div>
  );
}

export function Form({ onSubmit, error, children }: { onSubmit: () => void | Promise<void>; error?: unknown; children: ReactNode }) {
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        void onSubmit();
      }}
    >
      <ErrorNote error={error} />
      {children}
    </form>
  );
}
