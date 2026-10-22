"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { passwordHints } from "@/lib/validation";
import { Button, Input } from "@/components/ui";
import { AuthCard, Form } from "@/components/shell/AuthForm";

function Reset() {
  const token = useSearchParams().get("token") ?? "";
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const hints = passwordHints(pw);
  const submit = async () => {
    if (pw !== pw2) {
      setError(new ApiError(422, { message: "Passwords do not match." }));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/auth/reset-password", { method: "POST", body: { token, password: pw } });
      setDone(true);
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthCard title="Choose a new password" subtitle="All other sessions are signed out after a reset.">
      {!token ? (
        <p className="text-ink-300">This page needs the link from your reset email. <Link className="text-teal-300" href="/forgot-password">Request one</Link>.</p>
      ) : done ? (
        <div className="space-y-4">
          <p role="status" className="text-ivory-100">Password updated.</p>
          <Link href="/signin"><Button className="w-full">Sign in</Button></Link>
        </div>
      ) : (
        <Form onSubmit={submit} error={error}>
          <Input label="New password" type="password" name="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} hint={hints.length ? hints.join(" · ") : "Strong password ✓"} />
          <Input label="Confirm password" type="password" name="password2" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
          <Button type="submit" size="lg" className="w-full" loading={busy}>Update password</Button>
        </Form>
      )}
    </AuthCard>
  );
}

export default function Page() {
  return <Suspense><Reset /></Suspense>;
}
