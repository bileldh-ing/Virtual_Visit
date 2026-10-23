import { notFound } from "next/navigation";
import { FixtureWorld } from "./FixtureWorld";

// Test-only page: the viewer on a tiny committed world (public/e2e/world). Hidden unless the server runs with
// ROOMSCAPE_E2E_FIXTURES=1 (set by the e2e workflow), so production never serves it.
export const dynamic = "force-dynamic";

export default function E2EWorldPage() {
  if (process.env.ROOMSCAPE_E2E_FIXTURES !== "1") notFound();
  return <FixtureWorld />;
}
