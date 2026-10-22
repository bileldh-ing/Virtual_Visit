"use client";

import * as Dialog from "@radix-ui/react-dialog";
import clsx from "clsx";
import { Loader2, X } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

export { clsx };

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "glass";
  size?: "sm" | "md" | "lg" | "icon";
  loading?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-all duration-200 select-none",
        "disabled:cursor-not-allowed disabled:opacity-50 active:scale-[0.98]",
        size === "sm" && "h-8 px-3 text-sm",
        size === "md" && "h-10 px-4 text-sm",
        size === "lg" && "h-12 px-6 text-base",
        size === "icon" && "h-10 w-10",
        variant === "primary" && "bg-teal-400 text-ink-950 hover:bg-teal-300 shadow-[0_8px_30px_-10px_rgb(69_207_187/0.7)]",
        variant === "secondary" && "bg-ivory-100 text-ink-900 hover:bg-white",
        variant === "ghost" && "text-ivory-100 hover:bg-white/8",
        variant === "danger" && "bg-rose-400/90 text-ink-950 hover:bg-rose-400",
        variant === "glass" && "glass text-ivory-50 hover:bg-white/10",
        className,
      )}
      {...rest}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
});

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { label?: string; error?: string; hint?: string }>(
  function Input({ label, error, hint, className, id, ...rest }, ref) {
    const iid = id ?? rest.name;
    return (
      <label className="block space-y-1.5" htmlFor={iid}>
        {label && <span className="text-sm font-medium text-ivory-100">{label}</span>}
        <input
          ref={ref}
          id={iid}
          aria-invalid={Boolean(error) || undefined}
          aria-describedby={error ? `${iid}-err` : hint ? `${iid}-hint` : undefined}
          className={clsx(
            "h-11 w-full rounded-xl border bg-ink-850 px-3.5 text-[15px] text-ivory-50 placeholder:text-ink-400",
            "border-white/10 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-400/30",
            error && "border-rose-400",
            className,
          )}
          {...rest}
        />
        {error ? (
          <span id={`${iid}-err`} role="alert" className="text-sm text-rose-400">{error}</span>
        ) : hint ? (
          <span id={`${iid}-hint`} className="text-xs text-ink-300">{hint}</span>
        ) : null}
      </label>
    );
  },
);

export function Textarea({ label, className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string }) {
  return (
    <label className="block space-y-1.5">
      {label && <span className="text-sm font-medium text-ivory-100">{label}</span>}
      <textarea
        className={clsx(
          "w-full rounded-xl border border-white/10 bg-ink-850 px-3.5 py-2.5 text-[15px] text-ivory-50 placeholder:text-ink-400",
          "focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-400/30",
          className,
        )}
        {...rest}
      />
    </label>
  );
}

export function Select({ label, className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { label?: string }) {
  return (
    <label className="block space-y-1.5">
      {label && <span className="text-sm font-medium text-ivory-100">{label}</span>}
      <select
        className={clsx(
          "h-11 w-full rounded-xl border border-white/10 bg-ink-850 px-3 text-[15px] text-ivory-50",
          "focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-400/30",
          className,
        )}
        {...rest}
      >
        {children}
      </select>
    </label>
  );
}

const TONES = {
  neutral: "bg-white/8 text-ivory-100 border-white/10",
  teal: "bg-teal-400/15 text-teal-300 border-teal-400/30",
  amber: "bg-amber-400/15 text-amber-400 border-amber-400/30",
  rose: "bg-rose-400/15 text-rose-400 border-rose-400/30",
  blue: "bg-signal-500/15 text-[#9fb0ff] border-signal-500/30",
} as const;

export function Badge({ tone = "neutral", children, className, icon }: { tone?: keyof typeof TONES; children: ReactNode; className?: string; icon?: ReactNode }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium", TONES[tone], className)}>
      {icon}
      {children}
    </span>
  );
}

export function stateTone(state: string): keyof typeof TONES {
  if (state === "SUCCEEDED" || state === "READY") return "teal";
  if (state === "PARTIAL_SUCCESS" || state === "READY_WITH_WARNINGS" || state === "NEEDS_MORE_IMAGES" || state === "CANCEL_REQUESTED") return "amber";
  if (state === "FAILED" || state === "UNSUPPORTED_INPUT" || state === "ANALYSIS_FAILED") return "rose";
  if (state === "CANCELLED") return "neutral";
  return "blue";
}

export function Card({ children, className, ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx("surface rounded-2xl p-5", className)} {...rest}>
      {children}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-ink-300">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {label}
    </span>
  );
}

export function ErrorNote({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const e = error as { message?: string; requestId?: string; code?: string };
  return (
    <div role="alert" className={clsx("rounded-xl border border-rose-400/30 bg-rose-400/10 px-4 py-3 text-sm text-rose-400", className)}>
      {e.message ?? "Something went wrong."}
      {e.requestId && <span className="ml-2 text-xs text-rose-400/70">Ref {e.requestId.slice(0, 8)}</span>}
    </div>
  );
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/12 px-6 py-14 text-center">
      {icon && <div className="mb-4 text-teal-300">{icon}</div>}
      <h3 className="font-display text-xl text-ivory-50">{title}</h3>
      {children && <div className="mt-2 max-w-md text-sm text-ink-300">{children}</div>}
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  wide,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-ink-950/70 backdrop-blur-sm data-[state=open]:animate-in" />
        <Dialog.Content
          className={clsx(
            "glass-strong fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-3xl p-6 scrollbar-thin",
            wide ? "max-w-2xl" : "max-w-lg",
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="font-display text-2xl text-ivory-50">{title}</Dialog.Title>
              {description ? (
                <Dialog.Description className="mt-1 text-sm text-ink-300">{description}</Dialog.Description>
              ) : (
                <Dialog.Description className="sr-only">{title}</Dialog.Description>
              )}
            </div>
            <Dialog.Close asChild>
              <button aria-label="Close" className="rounded-lg p-1.5 text-ink-300 hover:bg-white/10 hover:text-ivory-50">
                <X className="h-5 w-5" />
              </button>
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Meter({ value, max, label, unit }: { value: number; max: number; label: string; unit?: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div>
      <div className="mb-1.5 flex justify-between text-sm">
        <span className="text-ink-300">{label}</span>
        <span className="tabular-nums text-ivory-100">
          {value.toLocaleString(undefined, { maximumFractionDigits: 1 })} / {max.toLocaleString()} {unit}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/8" role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} aria-label={label}>
        <div className={clsx("h-full rounded-full", pct > 90 ? "bg-rose-400" : pct > 70 ? "bg-amber-400" : "bg-teal-400")} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
