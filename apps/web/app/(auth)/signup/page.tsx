"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { passwordHints } from "@/lib/validation";
import { Button, Input } from "@/components/ui";
import { AuthCard, Form } from "@/components/shell/AuthForm";

export default function SignUp() {
  const router = useRouter();
  const qc = useQueryClient();
  const [f, setF] = useState({ display_name: "", email: "", password: "", workspace_name: "" });
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const hints = passwordHints(f.password);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/auth/signup", { method: "POST", body: { ...f, workspace_name: f.workspace_name || undefined } });
      await qc.invalidateQueries({ queryKey: ["me"] });
      router.replace("/app?welcome=1");
    } catch (e) {
      setError(e as ApiError);
      setBusy(false);
    }
  };
  return (
    <AuthCard title="Create your account" subtitle="Free plan: 3 projects, 60 reconstruction minutes a month." footer={<>Already have an account? <Link className="text-teal-300 hover:underline" href="/signin">Sign in</Link></>}>
      <Form onSubmit={submit} error={error}>
        <Input label="Your name" name="display_name" autoComplete="name" value={f.display_name} onChange={set("display_name")} />
        <Input label="Work email" name="email" type="email" autoComplete="email" required value={f.email} onChange={set("email")} error={error?.fields?.email} />
        <Input
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          value={f.password}
          onChange={set("password")}
          error={error?.fields?.password}
          hint={hints.length ? hints.join(" · ") : "Strong password ✓"}
        />
        <Input label="Workspace name (optional)" name="workspace_name" placeholder="e.g. Harbor & Pine Realty" value={f.workspace_name} onChange={set("workspace_name")} />
        <Button type="submit" className="w-full" size="lg" loading={busy}>Create account</Button>
        <p className="text-xs text-ink-400">We email a verification link. Publishing public tours requires a verified email.</p>
      </Form>
    </AuthCard>
  );
}
