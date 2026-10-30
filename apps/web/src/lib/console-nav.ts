export interface ConsoleSection {
  slug: string;
  label: string;
  /** href when no project is selected; project-scoped sections may override per project. */
  href: string;
  /** For sections that are not built yet: what the page will do, and which API it uses. */
  intro?: { title: string; body: string[]; api: string[] };
}

export const SECTIONS: ConsoleSection[] = [
  { slug: "overview", label: "Overview", href: "/console" },
  { slug: "monitoring", label: "Satellite monitoring", href: "/console/monitoring" },
  { slug: "verification", label: "AI verification", href: "/console/verification" },
  { slug: "registry", label: "Project registry", href: "/console/registry" },
  { slug: "reports", label: "MRV reports", href: "/console/reports" },
  { slug: "chain", label: "Blockchain records", href: "/console/chain" },
  { slug: "marketplace", label: "Carbon marketplace", href: "/console/marketplace" },
  { slug: "community", label: "Community portal", href: "/console/community" },
];

export const PLACEHOLDER_SLUGS = SECTIONS.filter((s) => s.intro).map((s) => s.slug);

export function sectionBySlug(slug: string): ConsoleSection | undefined {
  return SECTIONS.find((s) => s.slug === slug);
}
