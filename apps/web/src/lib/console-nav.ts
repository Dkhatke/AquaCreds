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
  {
    slug: "verification",
    label: "AI verification",
    href: "/console/verification",
    intro: {
      title: "Evidence checks for field photos",
      body: [
        "Every plot photo is checked automatically: EXIF consistency, the capture-nonce window, whether the GPS position falls inside the project boundary, and SHA-256 / perceptual-hash duplicates. The checks combine into a trust score per submission.",
        "This section will list submissions per monitoring period with each check's result, so a reviewer can see exactly why a photo was accepted or flagged.",
      ],
      api: [
        "GET /api/v1/periods/{period_id}/submissions",
        "GET /api/v1/submissions/{submission_id}",
      ],
    },
  },
  { slug: "registry", label: "Project registry", href: "/console/registry" },
  {
    slug: "reports",
    label: "MRV reports",
    href: "/console/reports",
    intro: {
      title: "Monitoring, reporting and verification",
      body: [
        "Each monitoring period produces a hashed MRV report computed by the carbon engine from field plots, with its methodology profile and versions. Verifiers from outside the organisation approve reports or request changes.",
        "Carbon figures shown here are estimated, pending accredited verification.",
      ],
      api: [
        "GET/POST /api/v1/periods/{period_id}/reports",
        "POST /api/v1/reports/{report_id}/review",
        "GET /api/v1/reports/{report_id}/verify",
      ],
    },
  },
  { slug: "chain", label: "Blockchain records", href: "/console/chain" },
  { slug: "marketplace", label: "Carbon marketplace", href: "/console/marketplace" },
  {
    slug: "community",
    label: "Community portal",
    href: "/console/community",
    intro: {
      title: "Stewardship and notifications",
      body: [
        "Organisations see their stewardship score (verification continuity, satellite-confirmed survival and evidence quality) with every input it was computed from, and manage members and notification preferences.",
      ],
      api: [
        "GET /api/v1/orgs/{org_id}/stewardship",
        "GET /api/v1/orgs/{org_id}/members",
        "GET /api/v1/notifications",
      ],
    },
  },
];

export const PLACEHOLDER_SLUGS = SECTIONS.filter((s) => s.intro).map((s) => s.slug);

export function sectionBySlug(slug: string): ConsoleSection | undefined {
  return SECTIONS.find((s) => s.slug === slug);
}
