import Link from "next/link";

export function Logo({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="group inline-flex items-center gap-2.5" aria-label="RoomScape AI home">
      <svg viewBox="0 0 64 64" className="h-8 w-8" aria-hidden>
        <rect width="64" height="64" rx="16" fill="#0f1724" stroke="rgb(255 255 255 / 0.1)" />
        <path d="M14 40 32 18l18 22" fill="none" stroke="#45cfbb" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M22 40v8h20v-8" fill="none" stroke="#f4efe4" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="32" cy="34" r="3.5" fill="#f4efe4" />
      </svg>
      <span className="font-display text-lg text-ivory-50">
        RoomScape<span className="text-teal-300"> AI</span>
      </span>
    </Link>
  );
}
