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
  {
    slug: "registry",
    label: "Project registry",
    href: "/console/registry",
    intro: {
      title: "Projects, boundaries and tenure documents",
      body: [
        "Register a project boundary, attach tenure and consent documents (forest-department agreement, panchayat resolution, FPIC record, CRZ clearance, carbon-rights agreement) and submit it for review. Boundaries are checked for overlaps with other projects.",
      ],
      api: [
        "GET/POST /api/v1/projects",
        "POST /api/v1/projects/{project_id}/documents",
        "POST /api/v1/projects/{project_id}/submit",
      ],
    },
  },
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
  {
    slug: "chain",
    label: "Blockchain records",
    href: "/console/chain",
    intro: {
      title: "On-chain anchors and transactions",
      body: [
        "Project registrations, report anchors and credit issuance are relayed to the registry contracts. This section will show each transaction with its status and confirmations, and verify a report hash against the chain.",
        "AquaCreds runs on the Polygon Amoy testnet: these are testnet registry units.",
      ],
      api: [
        "GET /api/v1/chain/status",
        "GET /api/v1/projects/{project_id}/chain",
        "GET /api/v1/chain/transactions/{tx_id}",
      ],
    },
  },
  {
    slug: "marketplace",
    label: "Carbon marketplace",
    href: "/console/marketplace",
    intro: {
      title: "Listings, trades and forward funding",
      body: [
        "List issued vintages, buy and retire credits, and run reverse auctions that pre-fund restoration against future verified removals. Payments use a mock stablecoin on the testnet, so all trades are demonstration transactions.",
      ],
      api: ["GET /api/v1/public/marketplace/listings", "GET /api/v1/forward-fund/requests"],
    },
  },
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
