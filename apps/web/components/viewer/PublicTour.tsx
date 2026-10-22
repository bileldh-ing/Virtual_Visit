"use client";

import { useQuery } from "@tanstack/react-query";
import { Flag, Lock } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Artifact, Job, Tour } from "@/lib/schemas";
import { Button, ErrorNote, Input, Modal, Select, Spinner, Textarea } from "@/components/ui";
import { Logo } from "@/components/shell/Logo";
import { TourViewer } from "./TourViewer";
import { PanoramaViewer } from "./PanoramaViewer";

export type PublicTourData = {
  project: { id: string; title: string; description: string; property_type: string; is_public_sample: boolean };
  job: Job | null;
  artifacts: Artifact[];
  tour: Tour;
  embed: { allow: boolean; origins: string[] } | null;
  disclaimer: string;
};

const unlockKey = (t: string) => `rs.unlock.${t.slice(0, 8)}`;

export function TourBody({ data, embed, className }: { data: PublicTourData; embed?: boolean; className?: string }) {
  const pano = data.tour.nodes.filter((n) => n.kind === "PANORAMA" && n.image_url).map((n) => ({ id: n.id, label: n.label || n.ref, image: n.image_url as string, thumb: n.thumb_url }));
  if (data.job?.mode === "PANORAMA_TOUR" && pano.length) return <PanoramaViewer nodes={pano} title={data.project.title} className={className} />;
  return <TourViewer title={data.project.title} artifacts={data.artifacts} tour={data.tour} result={data.job?.result} className={className} compact={embed} />;
}

export function PublicTour({ token, embed }: { token: string; embed?: boolean }) {
  const [unlock, setUnlock] = useState<string | null>(() => (typeof window === "undefined" ? null : sessionStorage.getItem(unlockKey(token))));
  const q = useQuery<PublicTourData, ApiError>({
    queryKey: ["public-tour", token, unlock],
    queryFn: () => api(`/api/v1/public/tours/${encodeURIComponent(token)}`, { headers: unlock ? { "X-Share-Unlock": unlock } : {} }),
    retry: false,
  });
  const [pw, setPw] = useState("");
  const [pwErr, setPwErr] = useState<ApiError | null>(null);
  const [report, setReport] = useState(false);

  if (q.isLoading) return <div className="flex h-dvh items-center justify-center"><Spinner label="Opening tour" /></div>;
  const err = q.error;
  if (err?.code === "PASSWORD_REQUIRED") {
    return (
      <Center>
        <Lock className="mx-auto h-9 w-9 text-teal-300" aria-hidden />
        <h1 className="mt-3 font-display text-2xl text-ivory-50">This tour is password protected</h1>
        <form
          className="mt-6 space-y-3 text-left"
          onSubmit={async (e) => {
            e.preventDefault();
            setPwErr(null);
            try {
              const r = await api<{ unlock: string }>(`/api/v1/public/tours/${encodeURIComponent(token)}/unlock`, { method: "POST", body: { password: pw } });
              sessionStorage.setItem(unlockKey(token), r.unlock);
              setUnlock(r.unlock);
            } catch (x) {
              setPwErr(x as ApiError);
            }
          }}
        >
          <ErrorNote error={pwErr} />
          <Input label="Password" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
          <Button type="submit" className="w-full">Open tour</Button>
        </form>
      </Center>
    );
  }
  if (err?.code === "SIGN_IN_REQUIRED") {
    return (
      <Center>
        <h1 className="font-display text-2xl text-ivory-50">Workspace members only</h1>
        <p className="mt-2 text-ink-300">{err.message}</p>
        <Link href={`/signin?next=/tour/${token}`} className="mt-5 inline-block"><Button>Sign in</Button></Link>
      </Center>
    );
  }
  if (err || !q.data) {
    return (
      <Center>
        <h1 className="font-display text-2xl text-ivory-50">{err?.status === 410 ? "This link is no longer active" : "Tour not found"}</h1>
        <p className="mt-2 text-ink-300">{err?.status === 410 ? "The owner revoked it or it expired. Ask them for a new link." : "Check the link and try again."}</p>
      </Center>
    );
  }
  const data = q.data;
  if (embed) {
    if (!data.embed?.allow) return <Center><p className="text-ink-300">Embedding is disabled for this tour.</p></Center>;
    return (
      <div className="relative h-dvh p-0">
        <TourBody data={data} embed className="h-full !rounded-none" />
        <a href={`/tour/${token}`} target="_blank" rel="noreferrer" className="glass absolute bottom-3 right-3 rounded-lg px-2.5 py-1 text-[11px] text-ivory-100">RoomScape AI ↗</a>
      </div>
    );
  }
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="glass sticky top-0 z-30 border-x-0 border-t-0">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
          <Logo />
          <button onClick={() => setReport(true)} className="inline-flex items-center gap-1.5 text-xs text-ink-300 hover:text-ivory-100"><Flag className="h-3.5 w-3.5" />Report a privacy concern</button>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-3 py-4 sm:px-4">
        <TourBody data={data} className="h-[calc(100dvh-9rem)] min-h-[480px]" />
        <div className="mx-auto mt-5 max-w-3xl px-2 pb-8">
          <h1 className="font-display text-2xl text-ivory-50">{data.project.title}</h1>
          {data.project.description && <p className="mt-2 text-ink-300">{data.project.description}</p>}
          <p className="mt-4 text-xs text-ink-400">{data.disclaimer}</p>
        </div>
      </main>
      <ReportDialog open={report} onOpenChange={setReport} token={token} />
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return (
    <main id="main" className="flex min-h-dvh items-center justify-center p-6">
      <div className="glass w-full max-w-sm rounded-3xl p-8 text-center">{children}</div>
    </main>
  );
}

function ReportDialog({ open, onOpenChange, token }: { open: boolean; onOpenChange: (v: boolean) => void; token: string }) {
  const [reason, setReason] = useState("people");
  const [details, setDetails] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Report a privacy concern" description="Our team reviews every report. The owner is not told who reported.">
      {done ? (
        <p role="status" className="text-ivory-100">Thank you. The report was filed.</p>
      ) : (
        <form
          className="space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api("/api/v1/privacy-reports", { method: "POST", body: { token, reason, details } });
              setDone(true);
            } catch (x) {
              setError(x as ApiError);
            }
          }}
        >
          <ErrorNote error={error} />
          <Select label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="people">Shows identifiable people</option>
            <option value="documents">Shows private documents</option>
            <option value="security">Shows keys, codes or security details</option>
            <option value="copyright">Copyright</option>
            <option value="other">Other</option>
          </Select>
          <Textarea label="Details (optional)" rows={3} value={details} onChange={(e) => setDetails(e.target.value)} />
          <div className="flex justify-end"><Button type="submit">Send report</Button></div>
        </form>
      )}
    </Modal>
  );
}
