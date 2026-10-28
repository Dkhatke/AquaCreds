import type { Metadata } from "next";
import { SiteFooter } from "@/components/landing/SiteFooter";
import { SiteNav } from "@/components/landing/SiteNav";
import { ButtonLink } from "@/components/ui";

export const metadata: Metadata = { title: "Marketplace" };

export default function MarketplacePage() {
  return (
    <>
      <SiteNav />
      <main id="main" className="surface-light bg-off-white">
        <div className="mx-auto max-w-3xl px-5 py-16 md:py-24">
          <p className="text-sm font-medium uppercase tracking-[0.18em] text-mint-ink">
            Marketplace
          </p>
          <h1 className="mt-3 font-display text-5xl font-semibold leading-tight">
            Credits with their evidence attached
          </h1>
          <p className="mt-6 leading-relaxed text-fg-muted">
            Every listing links to its vintage, the approved MRV report it was issued from and the
            project's public monitoring record. Buyers can retire credits and share a public
            certificate with a QR code. Payments use a mock stablecoin on the Polygon Amoy testnet,
            so listings are demonstration transactions in testnet registry units.
          </p>
          <p className="mt-4 leading-relaxed text-fg-muted">
            The public listing pages are the next part of the web app to be built; the API already
            serves them at <code>/api/v1/public/marketplace</code>.
          </p>
          <ButtonLink href="/console/demo" className="mt-8">
            Explore the demo <span aria-hidden="true">↗</span>
          </ButtonLink>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
