import type { ReactNode } from "react";
import { Logo } from "@/components/shell/Logo";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="relative grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      <aside className="relative hidden overflow-hidden lg:block" aria-hidden>
        <div className="absolute inset-0 bg-[radial-gradient(800px_500px_at_30%_30%,rgb(34_179_160/0.22),transparent_60%),radial-gradient(700px_500px_at_80%_90%,rgb(79_107_255/0.15),transparent_60%)]" />
        <div className="absolute inset-0 opacity-[0.18] [background-image:linear-gradient(rgb(255_255_255/0.6)_1px,transparent_1px),linear-gradient(90deg,rgb(255_255_255/0.6)_1px,transparent_1px)] [background-size:56px_56px] [mask-image:radial-gradient(closest-side,black,transparent)]" />
        <div className="relative flex h-full flex-col justify-between p-12">
          <Logo />
          <div className="max-w-md">
            <p className="font-display text-4xl leading-tight text-ivory-50">Real geometry from real photos, with every limit stated.</p>
            <p className="mt-4 text-ink-300">COLMAP structure-from-motion, honest quality reports and secure share links for property tours.</p>
          </div>
          <p className="text-xs text-ink-400">© {new Date().getFullYear()} RoomScape AI</p>
        </div>
      </aside>
      <main id="main" className="flex items-center justify-center px-4 py-12 sm:px-8">
        <div className="w-full max-w-md">
          <div className="mb-8 lg:hidden"><Logo /></div>
          {children}
        </div>
      </main>
    </div>
  );
}
