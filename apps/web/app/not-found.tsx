import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main" className="flex min-h-dvh flex-col items-center justify-center p-6 text-center">
      <p className="font-display text-7xl text-teal-300">404</p>
      <h1 className="mt-4 font-display text-3xl text-ivory-50">This room doesn’t exist</h1>
      <p className="mt-2 text-ink-300">The page may have moved, or the link is incomplete.</p>
      <Link href="/" className="mt-6 rounded-xl bg-teal-400 px-5 py-2.5 font-medium text-ink-950 hover:bg-teal-300">Back home</Link>
    </main>
  );
}
