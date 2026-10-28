import type { CSSProperties } from "react";

const STEPS = [
  {
    name: "Collect",
    body: "Field teams capture geotagged plot photos and tree measurements in the mobile app; each photo needs a single-use capture nonce.",
    detail: "Plots are mandatory: satellites never issue credits on their own.",
  },
  {
    name: "Analyse",
    body: "Sentinel-2 and Sentinel-1 scenes over the project boundary are cloud-masked, composited and classified; evidence photos are checked for EXIF, geofence and duplicates.",
    detail: "Every result records its scenes, model version and hashes.",
  },
  {
    name: "Verify",
    body: "The carbon engine computes net removals per the methodology profile, and an independent verifier reviews the MRV report.",
    detail: "Verifiers cannot review projects of their own organisation.",
  },
  {
    name: "Record",
    body: "The approved report's hash is anchored on-chain, and credits are issued as ERC-1155 vintages that can be traded and retired.",
    detail: "Currently on the Polygon Amoy testnet: testnet registry units.",
  },
];

/** Collect → Analyse → Verify → Record. The pulse runs through the steps in order to explain the
 *  sequence; with reduced motion all steps are shown at full strength. */
export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      aria-labelledby="how-title"
      className="surface-light bg-off-white py-20 md:py-28"
    >
      <div className="mx-auto max-w-7xl px-5 md:px-8">
        <p className="text-sm font-medium uppercase tracking-[0.18em] text-mint-ink">
          How it works
        </p>
        <h2
          id="how-title"
          className="mt-3 max-w-3xl font-display text-4xl font-semibold leading-tight md:text-5xl"
        >
          Collect, analyse, verify, record.
        </h2>
        <ol className="mt-14 grid gap-10 md:grid-cols-4 md:gap-6">
          {STEPS.map((s, i) => (
            <li key={s.name} className="relative">
              <div className="flex items-center gap-3" aria-hidden="true">
                <span
                  className="flow-step-marker grid size-9 place-items-center rounded-full bg-deep-ocean font-display text-sm font-semibold text-lagoon-mint"
                  style={{ "--step": i } as CSSProperties}
                >
                  {i + 1}
                </span>
                {i < STEPS.length - 1 && <span className="hidden h-px flex-1 bg-line md:block" />}
              </div>
              <h3 className="mt-5 font-display text-2xl font-semibold">
                <span className="sr-only">Step {i + 1}: </span>
                {s.name}
              </h3>
              <p className="mt-3 text-sm leading-relaxed text-fg-muted">{s.body}</p>
              <p className="mt-3 border-l-2 border-lagoon-mint pl-3 text-sm text-fg">{s.detail}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
