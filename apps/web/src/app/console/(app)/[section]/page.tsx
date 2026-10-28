import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Card } from "@/components/ui";
import { PLACEHOLDER_SLUGS, sectionBySlug } from "@/lib/console-nav";

type Props = { params: Promise<{ section: string }> };

export const dynamicParams = false;

export function generateStaticParams() {
  return PLACEHOLDER_SLUGS.map((section) => ({ section }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const s = sectionBySlug((await params).section);
  return { title: s?.label ?? "Console" };
}

/** Sections that are not built yet: what they will do and which API routes they use. */
export default async function SectionPage({ params }: Props) {
  const s = sectionBySlug((await params).section);
  if (!s?.intro) notFound();
  return (
    <div className="mx-auto max-w-3xl p-4 md:p-8">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-lagoon-mint">{s.label}</p>
      <h1 className="mt-2 font-display text-3xl font-semibold">{s.intro.title}</h1>
      {s.intro.body.map((p) => (
        <p key={p.slice(0, 32)} className="mt-4 leading-relaxed text-fg-muted">
          {p}
        </p>
      ))}
      <Card
        className="mt-8"
        title="Not built in the web app yet"
        meta="The API already serves these routes"
      >
        <ul className="space-y-1 font-mono text-xs text-fg-muted">
          {s.intro.api.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
