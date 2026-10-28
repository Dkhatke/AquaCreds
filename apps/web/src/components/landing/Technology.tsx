import Link from "next/link";
import { formatPercent } from "@/lib/format";
import { modelCard } from "@/lib/model-cards";

export function Technology() {
  const card = modelCard("ecosystem_classifier_v1");
  const items = [
    {
      title: "AI / ML",
      body: (
        <>
          A LightGBM ecosystem classifier on Sentinel-2 dry-season features, validated with spatial
          cross-validation and a held-out region it never saw in training.
          {card && (
            <span className="mt-3 block text-fg">
              Held-out (Gulf of Kutch, n = {card.heldout.n}): mangrove F1{" "}
              <span className="tabular font-semibold">
                {formatPercent(card.heldout.mangrove_vs_rest_f1)}
              </span>
              , macro-F1 across six classes{" "}
              <span className="tabular font-semibold">{formatPercent(card.heldout.macro_f1)}</span>.
            </span>
          )}
        </>
      ),
      footnote: card ? `Model card: ${card.model_version}` : null,
    },
    {
      title: "Satellite cross-validation",
      body: "Optical Sentinel-2 indices (NDVI, MVI) and Sentinel-1 radar backscatter, which sees through monsoon cloud, are checked against field evidence. Satellite figures are context: they flag change, they do not issue credits.",
      footnote: "Copernicus data via STAC (Planetary Computer, Earth Search fallback)",
    },
    {
      title: "Digital MRV",
      body: "A deterministic carbon engine implements India CCTS BM FR05.001 with AR-TOOL14 biomass from field plots, uncertainty discounts and reversal handling. Each report is a hashed, reproducible record.",
      footnote: "Every figure traces to inputs, methodology profile and versions",
    },
    {
      title: "Blockchain records",
      body: "Report hashes are anchored on-chain; credits are ERC-1155 vintages with retirement certificates. Field organisations use custodial wallets, so nobody in the field needs crypto.",
      footnote: "Polygon Amoy testnet: testnet registry units",
    },
  ];
  return (
    <section
      id="technology"
      aria-labelledby="tech-title"
      className="surface-light border-t border-line bg-sea-mist py-20 md:py-28"
    >
      <div className="mx-auto max-w-7xl px-5 md:px-8">
        <p className="text-sm font-medium uppercase tracking-[0.18em] text-mint-ink">Technology</p>
        <h2
          id="tech-title"
          className="mt-3 max-w-3xl font-display text-4xl font-semibold leading-tight md:text-5xl"
        >
          Four kinds of evidence, one audit trail.
        </h2>
        <div className="mt-14 grid gap-px overflow-hidden rounded-[var(--radius-card)] border border-line bg-line md:grid-cols-2">
          {items.map((t) => (
            <article key={t.title} className="bg-off-white p-7">
              <h3 className="font-display text-xl font-semibold">{t.title}</h3>
              <div className="mt-3 text-sm leading-relaxed text-fg-muted">{t.body}</div>
              {t.footnote && <p className="mt-4 text-xs font-medium text-mint-ink">{t.footnote}</p>}
            </article>
          ))}
        </div>
        <p className="mt-6 text-sm text-fg-muted">
          Read the full methodology and the limits of satellite data on{" "}
          <Link
            href="/how-it-works"
            className="font-medium text-mint-ink underline underline-offset-2"
          >
            How AquaCreds works
          </Link>
          .
        </p>
      </div>
    </section>
  );
}
