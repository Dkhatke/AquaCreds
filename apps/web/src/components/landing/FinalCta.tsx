import { ButtonLink } from "@/components/ui";

export function FinalCta() {
  return (
    <section aria-labelledby="cta-title" className="surface-dark bg-mangrove py-20 md:py-24">
      <div className="mx-auto flex max-w-7xl flex-col gap-8 px-5 md:flex-row md:items-end md:justify-between md:px-8">
        <div>
          <h2
            id="cta-title"
            className="max-w-2xl font-display text-4xl font-semibold leading-tight md:text-5xl"
          >
            See a project from boundary to retirement.
          </h2>
          <p className="mt-4 max-w-xl text-fg-muted">
            The demo opens the console on a clearly labelled synthetic project. No account needed.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/console/demo" size="lg">
            Explore the demo <span aria-hidden="true">↗</span>
          </ButtonLink>
          <ButtonLink href="/how-it-works" variant="secondary" size="lg">
            Methodology
          </ButtonLink>
        </div>
      </div>
    </section>
  );
}
