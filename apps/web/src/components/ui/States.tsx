import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

export function LoadingState({
  label = "Loading",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div role="status" aria-live="polite" className={cn("py-6", className)}>
      <div
        className="relative h-1 w-full overflow-hidden rounded bg-panel-raised"
        aria-hidden="true"
      >
        <div className="job-progress absolute inset-y-0 w-1/3 rounded bg-lagoon-mint/70" />
      </div>
      <p className="mt-3 text-sm text-fg-muted">{label}…</p>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
  className,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-lg border border-dashed border-line px-4 py-6", className)}>
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="mt-1 text-sm text-fg-muted">{children}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

/** Errors are shown, never papered over with placeholder numbers. */
export function ErrorState({
  title = "Could not load this data",
  error,
  onRetry,
  className,
}: {
  title?: string;
  error?: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : null;
  return (
    <div
      role="alert"
      className={cn("rounded-lg border border-failed/60 bg-failed/8 px-4 py-4", className)}
    >
      <p className="text-sm font-medium text-fail">{title}</p>
      {message && <p className="mt-1 break-words text-xs text-fg-muted">{message}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
