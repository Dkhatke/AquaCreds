import type { Metadata } from "next";
import { SiteFooter } from "@/components/landing/SiteFooter";
import { SiteNav } from "@/components/landing/SiteNav";

export const metadata: Metadata = { title: "How it works" };

const SECTIONS = [
  {
    id: "methodology",
    title: "Methodology",
    paras: [
      "AquaCreds computes credits per monitoring period as verified change, using the India Carbon Credit Trading Scheme methodology BM FR05.001 for mangrove afforestation and reforestation. Above- and below-ground biomass come from field sample plots (CDM AR-TOOL14), with species wood densities, a carbon fraction of 0.47 and the methodology's uncertainty discount.",
      "The carbon engine is deterministic: the same inputs, methodology profile and versions always give the same numbers, and each MRV report stores a hash of the full record. Results are labelled “estimated, pending accredited verification” until an accredited body verifies them.",
    ],
  },
  {
    id: "satellites",
    title: "What satellites can and cannot do",
    paras: [
      "Sentinel-2 (10 m optical) and Sentinel-1 (radar) show where mangrove canopy is, how vegetation indices change between seasons, and when something is lost. AquaCreds uses them to map ecosystems, stratify plots, raise loss alerts and check field evidence for consistency.",
      "They cannot measure biomass precisely enough to issue credits on their own, and young saplings are invisible at 10 m for years. Satellite figures are therefore shown as context, and crediting always needs field plots.",
    ],
  },
  {
    id: "verification",
    title: "How verification works",
    paras: [
      "Evidence photos are captured in the app with a single-use nonce, then checked for EXIF consistency, capture window, geofence and duplicates. A verifier from outside the project's organisation reviews the MRV report, with two-factor authentication, and can approve it or request changes.",
      "Approved reports are anchored on-chain by hash and credits are issued as ERC-1155 vintages. AquaCreds currently runs on the Polygon Amoy testnet, so these are testnet registry units, not tradeable carbon credits.",
    ],
  },
];

export default function HowItWorksPage() {
  return (
    <>
      <SiteNav />
      <main id="main" className="surface-light bg-off-white">
        <div className="mx-auto max-w-3xl px-5 py-16 md:py-24">
          <p className="text-sm font-medium uppercase tracking-[0.18em] text-mint-ink">
            How it works
          </p>
          <h1 className="mt-3 font-display text-5xl font-semibold leading-tight">
            From plots and pixels to a verifiable record
          </h1>
          <nav aria-label="On this page" className="mt-8">
            <ul className="flex flex-wrap gap-4 text-sm">
              {SECTIONS.map((s) => (
                <li key={s.id}>
                  <a className="text-mint-ink underline underline-offset-2" href={`#${s.id}`}>
                    {s.title}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          {SECTIONS.map((s) => (
            <section
              key={s.id}
              id={s.id}
              aria-labelledby={`${s.id}-title`}
              className="mt-14 border-t border-line pt-8"
            >
              <h2 id={`${s.id}-title`} className="font-display text-3xl font-semibold">
                {s.title}
              </h2>
              {s.paras.map((p) => (
                <p key={p.slice(0, 24)} className="mt-4 leading-relaxed text-fg-muted">
                  {p}
                </p>
              ))}
            </section>
          ))}
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
