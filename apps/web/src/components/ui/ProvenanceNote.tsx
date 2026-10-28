import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface Provenance {
  /** e.g. "Sentinel-2 L2A (Planetary Computer)" or "ecosystem_classifier_v1.1". */
  source?: string | undefined;
  modelVersion?: string | undefined;
  /** ISO date or date-time. */
  asOf?: string | undefined;
  /** Honesty labels: "context only", "estimated — pending accredited verification", "Demo". */
  label?: string | undefined;
}

/** Where a number came from. Every figure on a data page carries one of these. */
export function ProvenanceNote({
  source,
  modelVersion,
  asOf,
  label,
  className,
  children,
}: Provenance & { className?: string; children?: ReactNode }) {
  const parts = [
    source && `Source: ${source}`,
    modelVersion && `Model: ${modelVersion}`,
    asOf && `As of ${asOf}`,
  ].filter(Boolean);
  return (
    <p className={cn("text-xs leading-relaxed text-fg-muted", className)}>
      {label && <span className="mr-1.5 font-medium text-warn">{label}.</span>}
      {parts.join(" · ")}
      {children && (parts.length > 0 ? <> · {children}</> : children)}
    </p>
  );
}
