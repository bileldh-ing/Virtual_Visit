"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Button, Input } from "@/components/ui";
import { AuthCard, Form } from "@/components/shell/AuthForm";

function SignIn() {
  const router = useRouter();
  const sp = useSearchParams();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const next = sp.get("next");
  const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/app";

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/auth/signin", { method: "POST", body: { email, password } });
      await qc.invalidateQueries({ queryKey: ["me"] });
      router.replace(safeNext);
    } catch (e) {
      setError(e as ApiError);
      setBusy(false);
    }
  };
  return (
    <AuthCard title="Welcome back" subtitle="Sign in to your RoomScape workspace." footer={<>New here? <Link className="text-teal-300 hover:underline" href="/signup">Create an account</Link></>}>
      <Form onSubmit={submit} error={error}>
        <Input label="Email" name="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Input label="Password" name="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        <div className="flex justify-end">
          <Link href="/forgot-password" className="text-sm text-ink-300 hover:text-teal-300">Forgot password?</Link>
        </div>
        <Button type="submit" className="w-full" size="lg" loading={busy}>Sign in</Button>
      </Form>
    </AuthCard>
  );
}

export default function Page() {
  return <Suspense><SignIn /></Suspense>;
}
