import { FinalCta } from "@/components/landing/FinalCta";
import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { PlatformPreview } from "@/components/landing/PlatformPreview";
import { SiteFooter } from "@/components/landing/SiteFooter";
import { SiteNav } from "@/components/landing/SiteNav";
import { Stakeholders } from "@/components/landing/Stakeholders";
import { Technology } from "@/components/landing/Technology";
import { getPublicCounts } from "@/lib/public-api";

// Rendered per request so the live registry block appears as soon as the API is reachable; the
// public-API fetches themselves are cached for 5 minutes (lib/public-api.ts).
export const dynamic = "force-dynamic";

export default async function HomePage() {
  const counts = await getPublicCounts();
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-lagoon-mint focus:px-3 focus:py-2 focus:text-deep-ocean"
      >
        Skip to content
      </a>
      <SiteNav overlay />
      <main id="main">
        <Hero />
        <PlatformPreview />
        <HowItWorks />
        <Technology />
        <Stakeholders counts={counts} />
        <FinalCta />
      </main>
      <SiteFooter />
    </>
  );
}
