import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { Logo } from "@/components/brand/Logo";
import { HonestyNote } from "@/components/i18n/HonestyNote";
import { TranslationNotice } from "@/components/i18n/TranslationNotice";
import { imagery } from "@/lib/imagery";

export async function SiteFooter() {
  const t = await getTranslations("footer");
  return (
    <footer className="surface-dark bg-deep-ocean py-12 text-sm">
      <div className="mx-auto grid max-w-7xl gap-10 px-5 md:grid-cols-[1.3fr_1fr_1fr] md:px-8">
        <div>
          <Logo />
          <p className="mt-4 max-w-sm text-fg-muted">{t("blurb")}</p>
          <HonestyNote kind="estimated" className="mt-2 max-w-sm text-fg-muted" />
          <TranslationNotice className="mt-3 max-w-sm" />
        </div>
        <nav aria-label={t("nav")}>
          <h2 className="text-xs font-medium uppercase tracking-wide text-fg-muted">AquaCreds</h2>
          <ul className="mt-3 space-y-2">
            <li>
              <Link href="/how-it-works" className="hover:text-lagoon-mint">
                {t("methodology")}
              </Link>
            </li>
            <li>
              <Link href="/marketplace" className="hover:text-lagoon-mint">
                {t("marketplace")}
              </Link>
            </li>
            <li>
              <Link href="/console/demo" className="hover:text-lagoon-mint">
                {t("demo")}
              </Link>
            </li>
            <li>
              <Link href="/console/login" className="hover:text-lagoon-mint">
                {t("signIn")}
              </Link>
            </li>
            <li>
              <a href="https://github.com/Dkhatke/AquaCreds" className="hover:text-lagoon-mint">
                {t("contact")}
              </a>
            </li>
          </ul>
        </nav>
        <div>
          <h2 className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            {t("dataHeading")}
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
