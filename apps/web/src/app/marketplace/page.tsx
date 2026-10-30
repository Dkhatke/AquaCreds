import type { Metadata } from "next";
import { SiteFooter } from "@/components/landing/SiteFooter";
import { SiteNav } from "@/components/landing/SiteNav";
import { MarketNotice } from "@/components/market/MarketConsole";
import { PublicMarket } from "@/components/market/PublicMarket";
import { RegistryNav } from "@/components/public/RegistryNav";
import { ButtonLink } from "@/components/ui";

export const metadata: Metadata = { title: "Marketplace" };

export default function MarketplacePage() {
  return (
    <>
      <SiteNav />
      <div className="surface-light bg-off-white">
        <RegistryNav />
        <main id="main" className="mx-auto max-w-6xl px-5 py-10 md:px-8">
          <p className="text-sm font-medium uppercase tracking-[0.18em] text-mint-ink">
            Marketplace
          </p>
          <h1 className="mt-2 font-display text-4xl font-semibold leading-tight">
            Credits with their evidence attached
          </h1>
          <p className="mt-3 max-w-2xl leading-relaxed text-fg-muted">
            Every listing shows its project, the hash of the MRV report it was issued from and the
            project's stewardship score with its version. Browsing needs no account.
          </p>
          <div className="mt-3">
            <MarketNotice />
          </div>
          <div className="mt-8">
            <PublicMarket />
          </div>
          <ButtonLink href="/console/demo" variant="secondary" className="mt-10">
            Explore the demo <span aria-hidden="true">↗</span>
          </ButtonLink>
        </main>
      </div>
      <SiteFooter />
    </>
  );
}
