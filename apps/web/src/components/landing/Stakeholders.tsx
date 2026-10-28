import { formatNumber } from "@/lib/format";
import type { PublicCounts } from "@/lib/public-api";

const GROUPS = [
  {
    who: "Restoration groups",
    what: "Register a project boundary with tenure and consent documents, plan monitoring periods, and see satellite alerts for their own sites.",
  },
  {
    who: "Coastal communities",
    what: "Collect plot evidence with an offline-first mobile app; stewardship is scored on continuity, survival and evidence, not on paperwork.",
  },
  {
    who: "Verifiers",
    what: "Review MRV reports with the full evidence trail, approve or request changes, and sign with two-factor authentication.",
  },
  {
    who: "Buyers",
    what: "Browse vintages with their reports, buy and retire credits, and share a public retirement certificate with a QR code.",
  },
];

export function Stakeholders({ counts }: { counts: PublicCounts | null }) {
  return (
    <section
      id="impact"
      aria-labelledby="impact-title"
      className="surface-light bg-off-white py-20 md:py-28"
    >
      <div className="mx-auto max-w-7xl px-5 md:px-8">
        <p className="text-sm font-medium uppercase tracking-[0.18em] text-mint-ink">
          Impact and stakeholders
        </p>
        <h2
          id="impact-title"
          className="mt-3 max-w-3xl font-display text-4xl font-semibold leading-tight md:text-5xl"
        >
          Built for the people who keep mangroves standing.
        </h2>
        <dl className="mt-14 grid gap-x-10 gap-y-10 md:grid-cols-2">
          {GROUPS.map((g) => (
            <div key={g.who} className="border-t border-line pt-5">
              <dt className="font-display text-xl font-semibold">{g.who}</dt>
              <dd className="mt-2 max-w-prose text-fg-muted">{g.what}</dd>
            </div>
          ))}
        </dl>

        {counts && (
          <section
            aria-labelledby="registry-title"
            className="mt-16 rounded-[var(--radius-card)] border border-line bg-panel p-6"
          >
            <h3
              id="registry-title"
              className="text-sm font-medium uppercase tracking-wide text-fg-muted"
            >
              Public registry, live
            </h3>
            <dl className="mt-4 grid gap-6 sm:grid-cols-3">
              {[
                ["Projects", counts.projects],
                ["Credit vintages", counts.vintages],
                ["Retirements", counts.retirements],
              ].map(([label, n]) => (
                <div key={label}>
                  <dt className="text-sm text-fg-muted">{label}</dt>
                  <dd className="tabular font-display text-4xl font-semibold">
                    {formatNumber(Number(n))}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-4 text-xs text-fg-muted">
              Counted from <code>/api/v1/public</code> at page load. Credits are testnet registry
              units; figures are estimated, pending accredited verification.
            </p>
          </section>
        )}
      </div>
    </section>
  );
}
