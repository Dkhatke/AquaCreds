import type { Schema } from "@aquacreds/api-client";
import type { Status } from "@/components/ui";

type ProjectStatus = Schema<"ProjectStatus">;
type AlertSeverity = Schema<"AlertSeverity">;

export const PROJECT_STATUS: Record<ProjectStatus, { status: Status; label: string }> = {
  draft: { status: "neutral", label: "Draft" },
  submitted: { status: "review", label: "Submitted" },
  baseline_ready: { status: "review", label: "Baseline ready" },
  changes_requested: { status: "review", label: "Changes requested" },
  registered: { status: "ok", label: "Registered" },
  monitoring: { status: "ok", label: "Monitoring" },
  rejected: { status: "failed", label: "Rejected" },
};

export const ALERT_SEVERITY: Record<AlertSeverity, Status> = {
  low: "neutral",
  medium: "review",
  high: "failed",
};
