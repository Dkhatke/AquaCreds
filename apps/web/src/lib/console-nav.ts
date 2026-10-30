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
  { slug: "reports", label: "MRV reports", href: "/console/reports" },
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
  { slug: "community", label: "Community portal", href: "/console/community" },
];

export const PLACEHOLDER_SLUGS = SECTIONS.filter((s) => s.intro).map((s) => s.slug);

export function sectionBySlug(slug: string): ConsoleSection | undefined {
  return SECTIONS.find((s) => s.slug === slug);
}
