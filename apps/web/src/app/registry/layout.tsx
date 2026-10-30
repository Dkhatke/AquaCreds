import type { Metadata } from "next";
import type { ReactNode } from "react";
import { SiteFooter } from "@/components/landing/SiteFooter";
import { SiteNav } from "@/components/landing/SiteNav";
import { RegistryNav } from "@/components/public/RegistryNav";

export const metadata: Metadata = {
  title: { default: "Public registry", template: "%s · Public registry · AquaCreds" },
};

/** Public registry: light surfaces, no login. */
export default function RegistryLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <SiteNav />
      <div className="surface-light bg-off-white">
        <RegistryNav />
        <main id="main" className="mx-auto max-w-6xl px-5 py-10 md:px-8">
          {children}
          <p className="mt-12 border-t border-line pt-4 text-xs text-fg-muted">
            Carbon figures are estimated, pending accredited verification; registry units are on the
            Polygon Amoy testnet. Satellite figures are context only and never produce credits.
          </p>
        </main>
      </div>
      <SiteFooter />
    </>
  );
}
