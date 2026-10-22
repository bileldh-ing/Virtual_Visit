import { ArrowRight, Camera, Check, Cpu, Eye, Link2, Lock, Orbit, ScanSearch, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { Logo } from "@/components/shell/Logo";
import { LandingSample } from "@/components/marketing/LandingSample";
import { Reveal } from "@/components/marketing/Reveal";

const STEPS = [
  { icon: Camera, t: "Capture", d: "Walk the property and take 20–200 overlapping photos, or upload 360° panoramas." },
  { icon: ScanSearch, t: "Check", d: "Blur, exposure, duplicates and feature overlap are measured before you spend minutes." },
  { icon: Orbit, t: "Reconstruct", d: "COLMAP solves where every photo was taken and triangulates real 3D points." },
  { icon: Link2, t: "Share", d: "Expiring, revocable links, passwords and website embeds. Never the original EXIF." },
];

const HONEST = [
  ["Camera positions from your photos (COLMAP SfM)", true],
  ["Sparse 3D point cloud, coloured from the photos", true],
  ["360° panorama tours, shown exactly as captured", true],
  ["Capture-quality report with concrete fixes", true],
  ["Dense mesh: needs a CUDA GPU worker (not on CPU)", false],
  ["Real-world measurements: scale is relative, so disabled", false],
] as const;

export default function Landing() {
  return (
    <>
      <header className="glass fixed inset-x-0 top-0 z-50 border-x-0 border-t-0">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
          <Logo />
          <nav className="flex items-center gap-1 sm:gap-2" aria-label="Main">
            <a href="#how" className="hidden rounded-xl px-3 py-2 text-sm text-ink-300 hover:text-ivory-50 sm:inline">How it works</a>
            <a href="#honest" className="hidden rounded-xl px-3 py-2 text-sm text-ink-300 hover:text-ivory-50 md:inline">What’s real</a>
            <a href="#pricing" className="hidden rounded-xl px-3 py-2 text-sm text-ink-300 hover:text-ivory-50 md:inline">Pricing</a>
            <Link href="/signin" className="rounded-xl px-3 py-2 text-sm text-ivory-100 hover:bg-white/5">Sign in</Link>
            <Link href="/signup" className="rounded-xl bg-teal-400 px-4 py-2 text-sm font-medium text-ink-950 hover:bg-teal-300">Start free</Link>
          </nav>
        </div>
      </header>
      <main id="main">
        <section className="relative overflow-hidden pt-28 sm:pt-32">
          <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.12] [background-image:linear-gradient(rgb(255_255_255/0.5)_1px,transparent_1px),linear-gradient(90deg,rgb(255_255_255/0.5)_1px,transparent_1px)] [background-size:64px_64px] [mask-image:radial-gradient(ellipse_at_top,black,transparent_70%)]" />
          <div className="relative mx-auto max-w-7xl px-4 sm:px-6">
            <Reveal className="mx-auto max-w-3xl text-center">
              <p className="inline-flex items-center gap-2 rounded-full border border-teal-400/30 bg-teal-400/10 px-3 py-1 text-xs text-teal-300"><Cpu className="h-3.5 w-3.5" />Real photogrammetry · honest quality labels</p>
              <h1 className="mt-6 font-display text-5xl leading-[1.05] text-ivory-50 sm:text-6xl lg:text-7xl">Property photos,<br /><span className="bg-gradient-to-r from-teal-300 to-ivory-100 bg-clip-text text-transparent">reconstructed in 3D.</span></h1>
              <p className="mx-auto mt-6 max-w-xl text-lg text-ink-300">RoomScape turns overlapping listing photos into an explorable 3D tour, with every camera position solved from your images and every limitation stated plainly.</p>
              <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
                <Link href="/signup" className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-teal-400 px-6 font-medium text-ink-950 shadow-[0_8px_30px_-10px_rgb(69_207_187/0.7)] hover:bg-teal-300">Reconstruct your first property <ArrowRight className="h-4 w-4" /></Link>
                <a href="#sample" className="glass inline-flex h-12 items-center justify-center gap-2 rounded-xl px-6 text-ivory-50 hover:bg-white/10"><Eye className="h-4 w-4" />Explore the live sample</a>
              </div>
            </Reveal>
            <Reveal delay={0.15} className="mt-14">
              <div id="sample" className="relative h-[68vh] min-h-[460px] scroll-mt-24 rounded-[2rem] p-1.5 ring-1 ring-white/10 [background:linear-gradient(180deg,rgb(255_255_255/0.08),rgb(255_255_255/0.02))]">
                <LandingSample />
              </div>
              <p className="mt-3 text-center text-xs text-ink-400">Live: 48 apartment photos (Eyeful Tower dataset, MIT licence) → 42 registered viewpoints and 3,519 points, COLMAP on a 2-CPU worker in 5 minutes.</p>
            </Reveal>
          </div>
        </section>

        <section id="how" className="mx-auto max-w-7xl scroll-mt-20 px-4 py-28 sm:px-6">
          <Reveal className="max-w-2xl">
            <h2 className="font-display text-4xl text-ivory-50 sm:text-5xl">From camera roll to walkable tour</h2>
            <p className="mt-4 text-ink-300">Four steps, each one observable: you see the real state of every job, stage by stage.</p>
          </Reveal>
          <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s, i) => (
              <Reveal key={s.t} delay={i * 0.08}>
                <div className="surface h-full rounded-3xl p-6">
                  <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-teal-400/12 text-teal-300"><s.icon className="h-5 w-5" aria-hidden /></span>
                  <p className="mt-5 text-xs uppercase tracking-widest text-ink-400">Step {i + 1}</p>
                  <h3 className="mt-1 font-display text-2xl text-ivory-50">{s.t}</h3>
                  <p className="mt-2 text-sm text-ink-300">{s.d}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        <section id="honest" className="scroll-mt-20 border-y border-white/5 bg-ink-900/60 py-28">
          <div className="mx-auto grid max-w-7xl gap-12 px-4 sm:px-6 lg:grid-cols-2">
            <Reveal>
              <h2 className="font-display text-4xl text-ivory-50 sm:text-5xl">What’s real, and what isn’t</h2>
              <p className="mt-4 max-w-lg text-ink-300">A handful of photos can’t guarantee a complete, metric 3D model. So every viewer layer is labelled <span className="text-teal-300">reconstructed</span>, <span className="text-amber-400">inferred</span> or <span className="text-ink-300">unavailable</span>, and the reasons are shown to your clients too.</p>
            </Reveal>
            <Reveal delay={0.1}>
              <ul className="glass space-y-1 rounded-3xl p-3">
                {HONEST.map(([t, ok]) => (
                  <li key={t} className="flex items-center gap-3 rounded-2xl px-4 py-3">
                    <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${ok ? "bg-teal-400 text-ink-950" : "bg-white/8 text-ink-300"}`}>{ok ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}</span>
                    <span className={ok ? "text-ivory-100" : "text-ink-300"}>{t}</span>
                  </li>
                ))}
              </ul>
            </Reveal>
          </div>
        </section>

        <section className="mx-auto max-w-7xl px-4 py-28 sm:px-6">
          <div className="grid gap-5 md:grid-cols-3">
            {[
              { icon: Orbit, t: "Orbit & first-person walk", d: "Fly between real camera viewpoints, auto-tour, minimap with live position, source-photo overlay." },
              { icon: ShieldCheck, t: "Private by default", d: "Signed, expiring file URLs; EXIF never leaves storage; addresses never shared; verified deletion." },
              { icon: Lock, t: "Team workspaces", d: "Owner, admin, editor and viewer roles, invitations, audit trail and per-workspace retention." },
            ].map((f, i) => (
              <Reveal key={f.t} delay={i * 0.08}>
                <div className="surface h-full rounded-3xl p-7">
                  <f.icon className="h-6 w-6 text-teal-300" aria-hidden />
                  <h3 className="mt-4 font-display text-2xl text-ivory-50">{f.t}</h3>
                  <p className="mt-2 text-sm text-ink-300">{f.d}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        <section id="pricing" className="mx-auto max-w-7xl scroll-mt-20 px-4 pb-28 sm:px-6">
          <Reveal className="text-center">
            <h2 className="font-display text-4xl text-ivory-50 sm:text-5xl">Simple plans</h2>
            <p className="mt-3 text-ink-300">Minutes are charged only for successful reconstructions.</p>
          </Reveal>
          <div className="mx-auto mt-12 grid max-w-5xl gap-5 md:grid-cols-3">
            {[
              ["Free", "€0", ["3 projects", "60 min / month", "2 share links"]],
              ["Pro", "€29", ["25 projects", "600 min / month", "Embeds & passwords", "3 seats"]],
              ["Agency", "€99", ["200 projects", "3,000 min / month", "20 seats", "4 concurrent jobs"]],
            ].map(([name, price, feats], i) => (
              <Reveal key={name as string} delay={i * 0.08}>
                <div className={`h-full rounded-3xl p-7 ${name === "Pro" ? "glass ring-2 ring-teal-400/50" : "surface"}`}>
                  <h3 className="font-display text-2xl text-ivory-50">{name}</h3>
                  <p className="mt-2"><span className="font-display text-4xl text-ivory-50">{price}</span><span className="text-ink-300"> / month</span></p>
                  <ul className="mt-5 space-y-2 text-sm text-ivory-100">{(feats as string[]).map((f) => <li key={f} className="flex gap-2"><Check className="h-4 w-4 text-teal-300" aria-hidden />{f}</li>)}</ul>
                  <Link href="/signup" className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-xl bg-white/8 text-sm text-ivory-50 hover:bg-white/12">Get started</Link>
                </div>
              </Reveal>
            ))}
          </div>
        </section>
      </main>
      <footer className="border-t border-white/5">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-4 py-10 text-sm text-ink-400 sm:flex-row sm:px-6">
          <Logo />
          <p>Reconstruction by COLMAP (BSD). Sample photos: Eyeful Tower dataset by Meta (MIT).</p>
        </div>
      </footer>
    </>
  );
}
