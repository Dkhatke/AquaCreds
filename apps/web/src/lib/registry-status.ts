import type { Schema } from "@aquacreds/api-client";
import type { Status } from "@/components/ui";

export const REPORT_STATUS: Record<Schema<"ReportStatus">, { status: Status; label: string }> = {
  draft: { status: "neutral", label: "Draft" },
  under_review: { status: "review", label: "Under review" },
  changes_requested: { status: "review", label: "Changes requested" },
  approved: { status: "ok", label: "Approved" },
  rejected: { status: "failed", label: "Rejected" },
};

export const METHODOLOGY: Record<Schema<"Methodology">, string> = {
  ccts_bm_fr05_001: "CCTS BM FR05.001",
  vm0033_v2_1: "Verra VM0033 v2.1",
};

export const TENURE: Record<Schema<"TenureType">, string> = {
  forest_land: "Forest land",
  revenue_government_land: "Revenue / government land",
  port_or_central_land: "Port or central-government land",
  community_panchayat: "Community / panchayat land",
  private_land: "Private land",
};

export const DOCUMENT_KIND: Record<Schema<"DocumentKind">, string> = {
  forest_dept_agreement: "Forest-department agreement",
  collector_noc: "Collector NOC",
  port_or_central_permission: "Port / central-government permission",
  panchayat_resolution: "Panchayat resolution",
  fpic_record: "FPIC record",
  land_title: "Land title",
  owner_consent: "Owner consent",
  crz_clearance: "CRZ clearance",
  carbon_rights_agreement: "Carbon-rights agreement",
  other: "Other",
};

export const ECOSYSTEMS: Schema<"Ecosystem">[] = ["mangrove", "salt_marsh", "seagrass"];

/** Honesty label that goes next to every carbon figure (DESIGN_DIRECTION §2). */
export const CARBON_LABEL = "Estimated — pending accredited verification; testnet registry units";
