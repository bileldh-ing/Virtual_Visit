"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Button, Input } from "@/components/ui";
import { AuthCard, Form } from "@/components/shell/AuthForm";

export default function Forgot() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/auth/forgot-password", { method: "POST", body: { email } });
      setDone(true);
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthCard title="Reset your password" subtitle="We’ll email a link that works for one hour." footer={<Link href="/signin" className="text-teal-300 hover:underline">Back to sign in</Link>}>
      {done ? (
        <p role="status" className="text-ivory-100">If an account exists for <strong>{email}</strong>, a reset link is on its way.</p>
      ) : (
        <Form onSubmit={submit} error={error}>
          <Input label="Email" type="email" name="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <Button type="submit" size="lg" className="w-full" loading={busy}>Send reset link</Button>
        </Form>
      )}
    </AuthCard>
  );
}
