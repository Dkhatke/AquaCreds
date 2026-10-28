import Link from "next/link";
import { Logo } from "@/components/brand/Logo";
import { imagery } from "@/lib/imagery";

export function SiteFooter() {
  return (
    <footer className="surface-dark bg-deep-ocean py-12 text-sm">
      <div className="mx-auto grid max-w-7xl gap-10 px-5 md:grid-cols-[1.3fr_1fr_1fr] md:px-8">
        <div>
          <Logo />
          <p className="mt-4 max-w-sm text-fg-muted">
            Blue carbon MRV, registry and marketplace for Indian coastal ecosystems. Carbon figures
            are estimated, pending accredited verification; registry units are on a testnet.
          </p>
        </div>
        <nav aria-label="Footer">
          <h2 className="text-xs font-medium uppercase tracking-wide text-fg-muted">AquaCreds</h2>
          <ul className="mt-3 space-y-2">
            <li>
              <Link href="/how-it-works" className="hover:text-lagoon-mint">
                Methodology
              </Link>
            </li>
            <li>
              <Link href="/marketplace" className="hover:text-lagoon-mint">
                Marketplace
              </Link>
            </li>
            <li>
              <Link href="/console/demo" className="hover:text-lagoon-mint">
                Explore the demo
              </Link>
            </li>
            <li>
              <Link href="/console/login" className="hover:text-lagoon-mint">
                Sign in
              </Link>
            </li>
            <li>
              <a href="https://github.com/Dkhatke/AquaCreds" className="hover:text-lagoon-mint">
                Contact and source (GitHub)
              </a>
            </li>
          </ul>
        </nav>
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Data and attribution
          </h2>
          <ul className="mt-3 space-y-2 text-fg-muted">
            <li>
              {imagery.attribution}, processed by AquaCreds (
              <code>scripts/render_web_imagery.py</code>).
            </li>
            <li>Maps: OpenFreeMap, © OpenMapTiles, © OpenStreetMap contributors.</li>
            <li>Methodology: India CCTS BM FR05.001 with CDM AR-TOOL14.</li>
          </ul>
        </div>
      </div>
    </footer>
  );
}
