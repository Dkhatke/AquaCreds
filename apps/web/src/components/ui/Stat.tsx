import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { type Provenance, ProvenanceNote } from "./ProvenanceNote";

interface StatProps {
  label: string;
  /** Pre-formatted value; `null` renders an explicit "no data" instead of a made-up zero. */
  value: string | null;
  unit?: string;
  provenance?: Provenance;
  hint?: ReactNode;
  className?: string;
}

/** A number with its unit and provenance. Values use tabular numerals. */
export function Stat({ label, value, unit, provenance, hint, className }: StatProps) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="text-xs font-medium uppercase tracking-wide text-fg-muted">{label}</dt>
      <dd className="mt-1">
        {value === null ? (
          <span className="text-sm text-fg-muted">No data</span>
        ) : (
          <span className="tabular font-display text-2xl font-semibold">
            {value}
            {unit && <span className="ml-1 text-sm font-normal text-fg-muted">{unit}</span>}
          </span>
        )}
        {hint && <div className="mt-0.5 text-xs text-fg-muted">{hint}</div>}
        {provenance && <ProvenanceNote className="mt-1" {...provenance} />}
      </dd>
    </div>
  );
}
