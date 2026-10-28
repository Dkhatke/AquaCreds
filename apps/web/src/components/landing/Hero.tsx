import Image from "next/image";
import { ButtonLink, buttonClasses } from "@/components/ui";
import { formatDate } from "@/lib/format";
import { heroImage, imagery } from "@/lib/imagery";

export function Hero() {
  return (
    <section
      aria-labelledby="hero-title"
      className="surface-dark relative isolate min-h-[92svh] overflow-hidden bg-deep-ocean"
    >
      <Image
        src={`/imagery/${heroImage.file}`}
        alt={`True-colour Sentinel-2 image of the Bhitarkanika mangroves and the Brahmani–Baitarani delta, Odisha, ${imagery.dates.map(formatDate).join(" and ")}`}
        fill
        priority
        sizes="100vw"
        className="-z-10 object-cover object-[60%_45%]"
      />
      {/* Scrim for text contrast over the imagery (not decoration). */}
      <div
        aria-hidden="true"
        className="absolute inset-0 -z-10 bg-[linear-gradient(90deg,rgb(11_32_36/0.92)_0%,rgb(11_32_36/0.7)_45%,rgb(11_32_36/0.15)_100%)]"
      />

      <div className="mx-auto flex min-h-[92svh] max-w-7xl flex-col justify-end px-5 pb-16 pt-32 md:px-8 md:pb-24">
        <p className="mb-5 text-sm font-medium uppercase tracking-[0.18em] text-lagoon-mint">
          AI-powered blue carbon MRV
        </p>
        <h1 id="hero-title" className="display-xl max-w-[14ch] text-off-white">
          From coastal ecosystems to verifiable carbon evidence.
        </h1>
        <p className="mt-6 max-w-xl text-lg leading-relaxed text-sea-mist/90">
          AquaCreds combines AI-driven ecosystem analysis, satellite intelligence and
          blockchain-backed records to make blue carbon monitoring more transparent.
        </p>
        <div className="mt-9 flex flex-wrap gap-3">
          <ButtonLink href="/console/demo" size="lg">
            Explore the demo <span aria-hidden="true">↗</span>
          </ButtonLink>
          <a href="#how-it-works" className={buttonClasses("secondary", "lg", "text-off-white")}>
            Discover the technology
          </a>
        </div>
      </div>

      <p className="absolute bottom-3 right-4 max-w-[90vw] text-right text-[11px] leading-snug text-sea-mist/80 md:right-8">
        Bhitarkanika, Odisha · Sentinel-2 L2A, {imagery.dates.map(formatDate).join(" + ")} ·{" "}
        {imagery.attribution}
      </p>
    </section>
  );
}
