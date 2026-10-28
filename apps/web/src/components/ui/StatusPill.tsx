import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Mint = confirmed/verified, amber = review required, red = failed (DESIGN_DIRECTION §1). */
export type Status = "ok" | "review" | "failed" | "neutral";

const styles: Record<Status, string> = {
  ok: "border-lagoon-mint/60 bg-lagoon-mint/12 text-ok",
  review: "border-review-amber/70 bg-review-amber/14 text-warn",
  failed: "border-failed/70 bg-failed/12 text-fail",
  neutral: "border-line bg-panel-raised text-fg-muted",
};

const marks: Record<Status, string> = { ok: "✓", review: "!", failed: "×", neutral: "·" };

/** Status is never conveyed by colour alone: each pill has a mark and a text label. */
export function StatusPill({
  status,
  children,
  className,
}: {
  status: Status;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        styles[status],
        className,
      )}
      data-status={status}
    >
      <span aria-hidden="true" className="font-semibold">
        {marks[status]}
      </span>
      {children}
    </span>
  );
}
