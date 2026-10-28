import type { Metadata } from "next";
import Link from "next/link";
import { Logo } from "@/components/brand/Logo";
import { DemoStart } from "@/components/console/DemoStart";
import { ButtonLink, DemoBanner } from "@/components/ui";
import { demoAvailability } from "@/lib/demo-status";

export const metadata: Metadata = { title: "Explore the demo" };
export const dynamic = "force-dynamic";

export default async function DemoPage() {
  const availability = await demoAvailability();
  return (
    <>
      <DemoBanner text={availability.state === "available" ? availability.banner : null} />
      <main id="main" className="grid min-h-[80dvh] place-items-center px-5 py-12">
        <div className="w-full max-w-lg">
          <Link href="/" aria-label="AquaCreds home">
            <Logo />
          </Link>
          <h1 className="mt-10 font-display text-4xl font-semibold">Explore the demo</h1>
          <p className="mt-3 text-fg-muted">
            The demo opens the console on a synthetic project in Bhitarkanika, Odisha: generated
            plots, photos and reports, run through the real evidence checks and carbon engine. No
            account is needed, and nothing in it is a real project, measurement or credit.
          </p>
          <div className="mt-8">
            {availability.state === "available" ? (
              <DemoStart />
            ) : (
              <div className="rounded-[var(--radius-card)] border border-line bg-mangrove p-5">
                <h2 className="font-display text-lg font-semibold">
                  The demo is not available here
                </h2>
                <p className="mt-2 text-sm text-fg-muted">{availability.reason}</p>
                <div className="mt-4 flex flex-wrap gap-3">
                  <ButtonLink href="/how-it-works" variant="secondary" size="sm">
                    Read how it works
                  </ButtonLink>
                  <ButtonLink href="/console/login" variant="ghost" size="sm">
                    Sign in
                  </ButtonLink>
                </div>
              </div>
            )}
          </div>
        </div>
      </main>
    </>
  );
}
