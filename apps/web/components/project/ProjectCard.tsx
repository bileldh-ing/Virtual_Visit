"use client";

import { motion } from "framer-motion";
import { Camera, Clock } from "lucide-react";
import Link from "next/link";
import type { Project } from "@/lib/schemas";
import { Badge, stateTone } from "@/components/ui";

export function relTime(iso: string, now = Date.now()): string {
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function ProjectCard({ p, i }: { p: Project; i: number }) {
  return (
    <motion.li initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 8) * 0.04, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}>
      <Link href={`/app/projects/${p.id}`} className="group surface block overflow-hidden rounded-3xl transition hover:-translate-y-0.5 hover:border-white/15 hover:shadow-[0_20px_60px_-30px_rgb(69_207_187/0.5)]">
        <div className="relative aspect-[16/10] overflow-hidden bg-ink-850">
          {p.cover_url ? (
            <img src={p.cover_url} alt="" className="h-full w-full object-cover transition duration-700 group-hover:scale-[1.04]" />
          ) : (
            <div className="flex h-full items-center justify-center text-ink-400"><Camera className="h-8 w-8" aria-hidden /></div>
          )}
          <div className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-ink-950/80 to-transparent" />
          <div className="absolute left-3 top-3 flex gap-1.5">
            {p.latest_job ? <Badge tone={stateTone(p.latest_job.state)} className="backdrop-blur">{p.latest_job.state.replace(/_/g, " ").toLowerCase()}</Badge> : <Badge className="backdrop-blur">no reconstruction</Badge>}
            {p.is_public_sample && <Badge tone="blue">sample</Badge>}
          </div>
        </div>
        <div className="p-4">
          <h3 className="truncate font-display text-lg text-ivory-50">{p.title}</h3>
          <div className="mt-1.5 flex items-center gap-3 text-xs text-ink-300">
            <span className="capitalize">{p.property_type}</span>
            <span className="inline-flex items-center gap-1"><Camera className="h-3.5 w-3.5" aria-hidden />{p.photo_count} photos</span>
            <span className="ml-auto inline-flex items-center gap-1"><Clock className="h-3.5 w-3.5" aria-hidden />{relTime(p.updated_at)}</span>
          </div>
        </div>
      </Link>
    </motion.li>
  );
}
