"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { CheckCircle2, MailCheck } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button, ErrorNote, Spinner } from "@/components/ui";
import { AuthCard } from "@/components/shell/AuthForm";

function Verify() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<"idle" | "verifying" | "done" | "error">(token ? "verifying" : "idle");
  const [error, setError] = useState<ApiError | null>(null);
  const [sent, setSent] = useState(false);
  const ran = useRef(false);

  useEffect(() => {
    if (!token || ran.current) return;
    ran.current = true;
    api("/api/v1/auth/verify-email", { method: "POST", body: { token } })
      .then(() => setState("done"))
      .catch((e: ApiError) => {
        setError(e);
        setState("error");
      });
  }, [token]);

  const resend = async () => {
    setError(null);
    try {
      await api("/api/v1/auth/resend-verification", { method: "POST" });
      setSent(true);
    } catch (e) {
      setError(e as ApiError);
    }
  };

  return (
    <AuthCard title="Verify your email" subtitle="A verified email is required before you publish a tour link.">
      {state === "verifying" && <Spinner label="Verifying your link" />}
      {state === "done" && (
        <div className="text-center">
          <CheckCircle2 className="mx-auto h-10 w-10 text-teal-300" aria-hidden />
          <p className="mt-3 text-ivory-50">Email verified.</p>
          <Link href="/app" className="mt-5 inline-block"><Button>Go to dashboard</Button></Link>
        </div>
      )}
      {(state === "idle" || state === "error") && (
        <div className="space-y-4">
          <ErrorNote error={error} />
          <div className="flex items-start gap-3 text-sm text-ink-300">
            <MailCheck className="mt-0.5 h-5 w-5 shrink-0 text-teal-300" aria-hidden />
            <p>Open the link we emailed you. It expires after 48 hours. You need to be signed in to request a new one.</p>
          </div>
          {sent ? <p role="status" className="text-sm text-teal-300">A new link is on its way.</p> : <Button variant="glass" onClick={resend} className="w-full">Send a new link</Button>}
        </div>
      )}
    </AuthCard>
  );
}

export default function Page() {
  return <Suspense><Verify /></Suspense>;
}
