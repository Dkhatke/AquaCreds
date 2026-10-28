import Image from "next/image";
import { StatusPill } from "@/components/ui";
import { formatDate } from "@/lib/format";
import { falseColourImage, imagery } from "@/lib/imagery";

const PANELS = [
  {
    title: "Ecosystem classification",
    body: "Mangrove, water, tidal flat and other classes per 10 m pixel, with the model version and its held-out accuracy.",
  },
  {
    title: "Index time series",
    body: "NDVI, MVI and Sentinel-1 backscatter per scene, clear-sky filtered, with the cloud-free share of each observation.",
  },
  {
    title: "Alerts",
    body: "Vegetation and SAR drops against the project's own baseline; high-severity alerts need independent acknowledgement.",
  },
  {
    title: "Field evidence",
    body: "Geotagged plot photos checked for EXIF, capture window, geofence and duplicates, each with a trust score.",
  },
];

export function PlatformPreview() {
  return (
    <section
      id="platform"
      aria-labelledby="platform-title"
      className="surface-dark bg-deep-ocean py-20 md:py-28"
    >
      <div className="mx-auto max-w-7xl px-5 md:px-8">
        <div className="grid gap-10 lg:grid-cols-[1fr_1.35fr] lg:items-end">
          <div>
            <p className="text-sm font-medium uppercase tracking-[0.18em] text-lagoon-mint">
              Platform
            </p>
            <h2
              id="platform-title"
              className="mt-3 font-display text-4xl font-semibold leading-tight md:text-5xl"
            >
              The project map is the workspace.
            </h2>
            <p className="mt-5 max-w-lg text-fg-muted">
              Every project in the AquaCreds console opens on its boundary over satellite imagery.
              Evidence, time series and alerts sit beside the map, and every number carries its
              source, model version and date.
            </p>
          </div>
          <figure className="relative overflow-hidden rounded-[var(--radius-card)] border border-line">
            <Image
              src={`/imagery/${falseColourImage.file}`}
              alt="False-colour (near-infrared, red, green) Sentinel-2 image of Bhitarkanika: dense mangrove canopy appears deep red"
              width={falseColourImage.width}
              height={falseColourImage.height}
              sizes="(min-width: 1024px) 55vw, 100vw"
              className="aspect-[16/10] w-full object-cover object-[55%_45%]"
            />
            <div className="absolute left-3 top-3 flex flex-wrap gap-2">
              <StatusPill status="neutral">Sentinel-2 · B08 / B04 / B03</StatusPill>
            </div>
            <figcaption className="border-t border-line bg-mangrove px-4 py-3 text-xs text-fg-muted">
              False colour: healthy mangrove canopy reflects strongly in near-infrared and reads
              deep red. {imagery.area}, {imagery.dates.map(formatDate).join(" + ")}.{" "}
              {imagery.attribution}.
            </figcaption>
          </figure>
        </div>
        <ul className="mt-14 grid gap-px overflow-hidden rounded-[var(--radius-card)] border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
          {PANELS.map((p) => (
            <li key={p.title} className="bg-mangrove p-6">
              <h3 className="font-display text-lg font-semibold">{p.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-fg-muted">{p.body}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
