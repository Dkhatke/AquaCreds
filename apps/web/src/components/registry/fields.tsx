import { type ReactNode, useId } from "react";
import { cn } from "@/lib/cn";

export const inputClass =
  "h-10 w-full rounded-lg border border-line bg-panel px-3 text-sm text-fg placeholder:text-fg-muted disabled:opacity-60";

/** Label + control (+ hint). `children` receives the generated id and hint id. */
export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: ReactNode;
  className?: string;
  children: (ids: { id: string; describedBy: string | undefined }) => ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={cn("min-w-0", className)}>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-fg-muted">
        {label}
      </label>
      {children({ id, describedBy: hintId })}
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-fg-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

/** The API's own validation messages, shown verbatim. */
export function ProblemList({ title, messages }: { title: string; messages: string[] }) {
  if (messages.length === 0) return null;
  return (
    <div role="alert" className="rounded-lg border border-failed/60 bg-failed/8 px-4 py-3">
      <p className="text-sm font-medium text-fail">{title}</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-fg">
        {messages.map((m) => (
          <li key={m}>{m}</li>
        ))}
      </ul>
    </div>
  );
}
