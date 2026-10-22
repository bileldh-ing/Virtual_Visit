import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: { default: "RoomScape AI — Photos to explorable 3D property tours", template: "%s · RoomScape AI" },
  description:
    "Turn overlapping property photos into a real, explorable 3D reconstruction with honest quality reporting, 360° tours and secure share links.",
  applicationName: "RoomScape AI",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = { themeColor: "#070b12", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-lg focus:bg-teal-400 focus:px-4 focus:py-2 focus:text-ink-950">
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
