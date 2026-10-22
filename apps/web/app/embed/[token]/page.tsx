import type { Metadata } from "next";
import { PublicTour } from "@/components/viewer/PublicTour";

export const metadata: Metadata = { title: "3D tour", robots: { index: false, follow: false } };

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <PublicTour token={token} embed />;
}
